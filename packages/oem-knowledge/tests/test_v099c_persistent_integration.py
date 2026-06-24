"""
Tests for Task v0.99C Addendum — knowledge_read as the First Project-Memory Primitive.

Covers:
- Standard shape
- Read-only safety (no mutations, no mtime changes)
- Missing .oem error path
- Project identity from manifest
- Runtime status summary
- Recent sessions from outcomes.jsonl
- Approved skills
- No LLM / no index calls
- MCP tool registration
- OpenCode instructions checklist
- Unsupported scope returns not_implemented
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from oem_knowledge.cli.parser import _setup_parser
from oem_knowledge.cli.commands.session import run_session_command
from oem_knowledge.cli.commands.system import run_system_command
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.manifest import load_manifest, get_manifest_path
from oem_knowledge.runtime import run_agent
from oem_knowledge.source_classifier import is_ingestion_eligible


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    yield project_dir, engine
    shutil.rmtree(project_dir, ignore_errors=True)


@pytest.fixture
def initialized_project(tmp_path):
    project_dir = tmp_path / "initialized_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir, engine
    shutil.rmtree(project_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Original v0.99C tests (preserved)
# ---------------------------------------------------------------------------

def test_manifest_creation_during_init(temp_project):
    project_dir, engine = temp_project
    engine.init_project(str(project_dir))

    manifest_path = get_manifest_path(project_dir)
    assert manifest_path.exists()

    manifest = load_manifest(project_dir)
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["project_id"] == "test_project"
    assert manifest["memory_root"] == ".oem"
    assert manifest["created_by"] == "openempiric"


def test_manifest_updates_during_setup_opencode(temp_project):
    project_dir, engine = temp_project
    engine.init_project(str(project_dir))

    with patch("oem_knowledge.cli.commands.system.Path.home") as mock_home, \
         patch("oem_knowledge.cli.commands.system.check_mcp_server", return_value=(True, True, 5, "")):

        mock_home.return_value = project_dir / "user_home"

        parser = _setup_parser()
        args = parser.parse_args(["setup", "opencode"])
        args.project = str(project_dir)

        run_system_command(args)

        manifest = load_manifest(project_dir)
        assert manifest is not None
        assert "opencode" in manifest["agent_integrations"]
        assert manifest["agent_integrations"]["opencode"]["enabled"] is True


def test_knowledge_read_execution(initialized_project):
    project_dir, engine = initialized_project

    res = engine.knowledge_read(project=str(project_dir))
    assert res["status"] == "success"
    assert res["operation"] == "knowledge_read"
    assert "sections" in res

    sections = res["sections"]
    assert "runtime_status" in sections
    assert "important_concepts" in sections
    assert "approved_skills" in sections


def test_run_agent_flags_handling(temp_project):
    project_dir, engine = temp_project

    parser = _setup_parser()

    # Test --no-init fails
    args = parser.parse_args(["run", "opencode", "--no-init", "--project", str(project_dir)])
    with pytest.raises(SystemExit) as excinfo:
        run_agent("opencode", engine, str(project_dir), args)
    assert excinfo.value.code == 1

    # Test non-TTY fails without init-if-missing
    with patch("sys.stdin.isatty", return_value=False):
        with pytest.raises(SystemExit) as excinfo:
            run_agent("opencode", engine, str(project_dir), None)
        assert excinfo.value.code == 1

    # Test --init-if-missing initializes and proceeds
    with patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_run, \
         patch("oem_knowledge.cli.commands.system.cmd_setup_opencode") as mock_setup:

        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process

        args = parser.parse_args([
            "run", "opencode", "--init-if-missing",
            "--project", str(project_dir),
            "--skip-doctor", "--skip-session-start", "--skip-session-end",
        ])

        run_agent("opencode", engine, str(project_dir), args)
        assert engine.is_initialized(str(project_dir))


def test_ingestion_filter_exclusions():
    assert is_ingestion_eligible("manifest.json") is False
    assert is_ingestion_eligible(".oem/manifest.json") is False
    assert is_ingestion_eligible("init.sh") is False
    assert is_ingestion_eligible("oem.md") is False
    assert is_ingestion_eligible("memory-start.md") is False
    assert is_ingestion_eligible(".oem/.runtime/preflight_context.md") is False
    assert is_ingestion_eligible(".oem/preflight/preflight_events.jsonl") is False


# ---------------------------------------------------------------------------
# New Addendum tests
# ---------------------------------------------------------------------------

class TestKnowledgeReadShape:
    def test_knowledge_read_project_scope_returns_standard_shape(self, initialized_project):
        project_dir, engine = initialized_project
        res = engine.knowledge_read(project=str(project_dir), scope="project")

        assert res["status"] == "success"
        assert res["operation"] == "knowledge_read"
        assert res["scope"] == "project"
        assert "project" in res
        assert "message" in res
        assert "sections" in res
        assert "warnings" in res
        # suggestion is either None or a string
        assert "suggestion" in res

        sections = res["sections"]
        for expected_key in (
            "project", "runtime_status", "recent_sessions",
            "important_concepts", "approved_skills",
            "warnings", "suggested_next_searches",
        ):
            assert expected_key in sections, f"Missing section key: {expected_key}"
            assert isinstance(sections[expected_key], list)

    def test_knowledge_read_includes_manifest_project_identity(self, initialized_project):
        project_dir, engine = initialized_project
        res = engine.knowledge_read(project=str(project_dir))
        project_section = res["sections"]["project"]
        assert any("ID:" in line for line in project_section), (
            f"Project identity missing from sections['project']: {project_section}"
        )

    def test_knowledge_read_includes_runtime_status_summary(self, initialized_project):
        project_dir, engine = initialized_project
        res = engine.knowledge_read(project=str(project_dir))
        runtime_status = res["sections"]["runtime_status"]
        assert isinstance(runtime_status, list)
        assert len(runtime_status) > 0, "runtime_status should not be empty after init"

    def test_knowledge_read_includes_recent_memory_when_available(self, initialized_project):
        project_dir, engine = initialized_project

        # Write a fake outcomes entry
        state_dir = project_dir / ".oem" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        outcomes_file = state_dir / "outcomes.jsonl"
        outcome_entry = {
            "timestamp": "2026-01-01T00:00:00Z",
            "session_id": "session-abc123",
            "outcome": "success",
            "goal_satisfaction": 0.9,
        }
        with open(outcomes_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(outcome_entry) + "\n")

        res = engine.knowledge_read(project=str(project_dir))
        recent = res["sections"]["recent_sessions"]
        assert isinstance(recent, list)
        assert len(recent) > 0
        # session_id is truncated to 8 chars in output, so check for the 8-char prefix
        assert any("session-" in entry for entry in recent), (
            f"Expected session entry in recent_sessions: {recent}"
        )

    def test_knowledge_read_includes_approved_skills_when_available(self, initialized_project):
        project_dir, engine = initialized_project
        # Even with no skills, approved_skills should be a list (possibly empty)
        res = engine.knowledge_read(project=str(project_dir))
        assert isinstance(res["sections"]["approved_skills"], list)


class TestKnowledgeReadReadOnly:
    def test_knowledge_read_is_read_only(self, initialized_project):
        """No new files should appear in .oem after knowledge_read."""
        project_dir, engine = initialized_project
        oem_dir = project_dir / ".oem"

        files_before = set(oem_dir.rglob("*"))
        engine.knowledge_read(project=str(project_dir))
        files_after = set(oem_dir.rglob("*"))

        new_files = files_after - files_before
        assert not new_files, f"knowledge_read created new files: {new_files}"

    def test_knowledge_read_does_not_change_oem_file_mtimes(self, initialized_project):
        """Snapshot mtimes before and after; assert no file was created or modified."""
        project_dir, engine = initialized_project
        oem_dir = project_dir / ".oem"

        # Snapshot mtimes
        def get_mtime_snapshot():
            return {
                str(p): p.stat().st_mtime
                for p in oem_dir.rglob("*")
                if p.is_file()
            }

        snapshot_before = get_mtime_snapshot()
        # Small sleep to ensure time resolution
        time.sleep(0.05)

        engine.knowledge_read(project=str(project_dir))

        snapshot_after = get_mtime_snapshot()

        # Check no new files
        new_files = set(snapshot_after) - set(snapshot_before)
        assert not new_files, f"knowledge_read created new files: {new_files}"

        # Check no mtime changes
        modified = {
            p for p in snapshot_before
            if p in snapshot_after and snapshot_after[p] != snapshot_before[p]
        }
        assert not modified, f"knowledge_read modified these files: {modified}"

    def test_knowledge_read_does_not_run_llm(self, initialized_project):
        """Verify no embedding model or LLM is called."""
        project_dir, engine = initialized_project

        with patch.object(type(engine), "model", new_callable=lambda: property(
            lambda self: (_ for _ in ()).throw(AssertionError("LLM/embedding model accessed!"))
        )):
            # Should not raise AssertionError
            try:
                res = engine.knowledge_read(project=str(project_dir))
                assert res["status"] == "success"
            except AssertionError as e:
                pytest.fail(f"knowledge_read accessed LLM/embedding model: {e}")

    def test_knowledge_read_does_not_index_or_mutate_files(self, initialized_project):
        """Monkeypatch index/write to assert they're not called."""
        project_dir, engine = initialized_project

        with patch.object(engine.search, "index_all") as mock_index, \
             patch.object(engine.state, "_append_event") as mock_append:
            engine.knowledge_read(project=str(project_dir))
            mock_index.assert_not_called()
            mock_append.assert_not_called()


class TestKnowledgeReadErrorPaths:
    def test_knowledge_read_missing_oem_returns_error_with_suggestion(self, tmp_path):
        """If .oem doesn't exist, return error dict with suggestion."""
        project_dir = tmp_path / "empty_project"
        project_dir.mkdir()
        engine = KnowledgeEngine(project_dir)

        res = engine.knowledge_read(project=str(project_dir))

        assert res["status"] == "error"
        assert res["operation"] == "knowledge_read"
        assert "No OEM project memory found" in res["message"]
        assert "suggestion" in res
        assert res["suggestion"]  # non-empty string
        # Must NOT have created .oem
        assert not (project_dir / ".oem").exists()

    def test_knowledge_read_no_public_scope_returns_not_implemented(self, initialized_project):
        """Unsupported read scopes return a structured error with status 'error'."""
        project_dir, engine = initialized_project
        res = engine.knowledge_read(project=str(project_dir), scope="invalid_scope")
        assert res["status"] == "error"
        assert "Unsupported read scope" in res["message"]

    def test_knowledge_read_all_public_scopes_are_read_only(self, initialized_project):
        """All public scopes (project, recent, skills, health) must be read-only."""
        project_dir, engine = initialized_project
        
        # Snapshot directory state
        files_before = set(project_dir.rglob("*"))
        mtimes_before = {f: f.stat().st_mtime for f in files_before if f.is_file()}

        for scope in ("project", "recent", "skills", "health"):
            res = engine.knowledge_read(project=str(project_dir), scope=scope)
            assert res["status"] == "success"

        files_after = set(project_dir.rglob("*"))
        assert files_before == files_after, "Files were created or deleted during read"
        for f in files_before:
            if f.is_file():
                assert f.stat().st_mtime == mtimes_before[f], f"File {f} was mutated"

    def test_knowledge_read_can_be_used_mid_task_without_mutation(self, initialized_project):
        """knowledge_read can be called mid-task without mutating project state or running indexing."""
        project_dir, engine = initialized_project
        files_before = set(project_dir.rglob("*"))
        
        res = engine.knowledge_read(project=str(project_dir), scope="project")
        assert res["status"] == "success"
        
        files_after = set(project_dir.rglob("*"))
        assert files_before == files_after


class TestKnowledgeReadCLIAndMCP:
    def test_mcp_knowledge_read_registered(self):
        """knowledge_read tool must appear in the MCP server."""
        import asyncio
        try:
            from fastmcp import FastMCP
        except ImportError:
            pytest.skip("FastMCP not installed")

        mcp = FastMCP("oem")
        from oem_knowledge.server import mount_tools
        mount_tools(mcp)
        tool_names = [t.name for t in asyncio.run(mcp.list_tools())]
        assert "knowledge_read" in tool_names, (
            f"knowledge_read not in MCP tools: {tool_names}"
        )

    def test_oem_read_cli_command_registered(self):
        """oem read must be a valid CLI command with scope and limit args."""
        parser = _setup_parser()
        args = parser.parse_args(["read", "--scope", "project", "--limit", "5"])
        assert args.command == "read"
        assert args.scope == "project"
        assert getattr(args, "limit", None) == 5


class TestKnowledgeReadInstructions:
    def test_opencode_instructions_call_knowledge_read_first(self):
        """The persistent instructions must list knowledge_preflight as step 1, knowledge_session_start as step 5, and knowledge_read as step 6."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "knowledge_preflight" in OEM_MEMORY_INSTRUCTIONS
        assert "knowledge_session_start" in OEM_MEMORY_INSTRUCTIONS
        assert "knowledge_read" in OEM_MEMORY_INSTRUCTIONS
        lines = OEM_MEMORY_INSTRUCTIONS.splitlines()
        step_1 = next((l for l in lines if l.strip().startswith("1.")), "")
        step_5 = next((l for l in lines if l.strip().startswith("5.")), "")
        step_6 = next((l for l in lines if l.strip().startswith("6.")), "")
        assert "knowledge_preflight" in step_1
        assert "knowledge_session_start" in step_5
        assert "knowledge_read" in step_6

    def test_opencode_instructions_include_session_start(self):
        """The instructions must call knowledge_session_start as step 5."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "5. Call `knowledge_session_start`" in OEM_MEMORY_INSTRUCTIONS

    def test_opencode_instructions_describe_knowledge_read_as_learning_primitive(self):
        """The instructions must describe knowledge_read as a learning/orientation primitive."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "Use `knowledge_read` whenever you need orientation" in OEM_MEMORY_INSTRUCTIONS
        assert "- `knowledge_read` teaches broad project context" in OEM_MEMORY_INSTRUCTIONS

    def test_oem_run_print_instructions_mentions_read_as_orientation_not_startup_only(self, temp_project):
        """oem run opencode --print-instructions must print the orientation description for knowledge_read."""
        project_dir, engine = temp_project
        engine.init_project(str(project_dir))

        parser = _setup_parser()
        args = parser.parse_args([
            "run", "opencode", "--print-instructions", "--project", str(project_dir)
        ])

        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            with pytest.raises(SystemExit) as exc:
                run_agent("opencode", engine, str(project_dir), args)
        assert exc.value.code == 0

        output = "\n".join(captured)
        assert "Use `knowledge_read` whenever you need orientation" in output

    def test_oem_run_print_instructions_mentions_knowledge_read(self, temp_project):
        """oem run opencode --print-instructions must print the instructions template."""
        project_dir, engine = temp_project
        engine.init_project(str(project_dir))

        parser = _setup_parser()
        args = parser.parse_args([
            "run", "opencode", "--print-instructions", "--project", str(project_dir)
        ])

        captured = []
        with patch("builtins.print", side_effect=lambda *a, **kw: captured.append(" ".join(str(x) for x in a))):
            with pytest.raises(SystemExit) as exc:
                run_agent("opencode", engine, str(project_dir), args)
        assert exc.value.code == 0

        output = "\n".join(captured)
        assert "1. Before planning a non-trivial task, check `.oem/.runtime/preflight_context.md`" in output
        assert "5. Call `knowledge_session_start`" in output
        assert "6. Use `knowledge_read`" in output

    def test_context_memory_context_calls_knowledge_read_first(self, initialized_project):
        """Compiled runtime context must include OEM_MEMORY_INSTRUCTIONS."""
        project_dir, engine = initialized_project
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)

        mc = ctx["memory_context"]
        assert "knowledge_session_start" in mc
        assert "knowledge_read" in mc

    def test_knowledge_search_instruction_is_specific_query_only(self):
        """The instructions must suggest knowledge_search for specific queries."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "Use `knowledge_search` when you have a specific memory query" in OEM_MEMORY_INSTRUCTIONS
        assert "- `knowledge_search` retrieves specific memory" in OEM_MEMORY_INSTRUCTIONS

    def test_reflect_instruction_mentions_structured_events_or_markers(self):
        """The instructions must suggest structured events or explicit markers for reflection."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "Use `knowledge_reflect` to record important decisions" in OEM_MEMORY_INSTRUCTIONS
        assert "- Prefer structured events or explicit markers for reflection" in OEM_MEMORY_INSTRUCTIONS

    def test_session_end_instruction_required_before_finishing(self):
        """The instructions must state session_end is required before finishing."""
        from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS
        assert "Call `knowledge_session_end` before finishing" in OEM_MEMORY_INSTRUCTIONS
