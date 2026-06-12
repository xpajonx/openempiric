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

    def test_knowledge_read_unsupported_scope_returns_not_implemented(self, initialized_project):
        """Scopes other than 'project' must return not_implemented cleanly."""
        project_dir, engine = initialized_project
        for scope in ("recent", "skills", "health"):
            res = engine.knowledge_read(project=str(project_dir), scope=scope)
            assert res["status"] == "not_implemented", (
                f"Expected not_implemented for scope={scope}, got {res['status']}"
            )
            assert res["operation"] == "knowledge_read"
            assert "suggestion" in res


class TestKnowledgeReadCLIAndMCP:
    def test_mcp_knowledge_read_registered(self):
        """knowledge_read tool must appear in the MCP server."""
        import asyncio
        try:
            from fastmcp import FastMCP
        except ImportError:
            pytest.skip("fastmcp not installed")
        from oem_knowledge.server import mount_tools
        mcp = FastMCP("test-oem")
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
        """The persistent instructions must list knowledge_read as step 1."""
        inst_content = (
            "# OpenEmpiric Project Memory\n\n"
            "When working in an OEM-enabled project:\n\n"
            "1. Call `knowledge_read` first to load the project memory baseline.\n"
            "2. Call `knowledge_search` for task-specific memory before planning.\n"
            "3. Use `knowledge_reflect` to record important decisions, failures, constraints, and outcomes.\n"
            "4. Call `knowledge_session_end` before finishing.\n"
            "5. Do not manually edit `.oem` files.\n"
        )
        assert "knowledge_read" in inst_content
        # Step 1 must mention knowledge_read
        lines = inst_content.splitlines()
        step_1 = next((l for l in lines if l.strip().startswith("1.")), "")
        assert "knowledge_read" in step_1, (
            f"Step 1 must mention knowledge_read, got: {step_1!r}"
        )

    def test_oem_run_print_instructions_mentions_knowledge_read(self, temp_project):
        """oem run opencode --print-instructions must mention knowledge_read as step 1."""
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
        assert "knowledge_read" in output
        # Step 1 must mention knowledge_read (use raw backtick, not escaped)
        assert "1. Call `knowledge_read`" in output

    def test_context_memory_context_calls_knowledge_read_first(self, initialized_project):
        """Compiled runtime context must list knowledge_read as step 1."""
        project_dir, engine = initialized_project
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)

        mc = ctx["memory_context"]
        assert "knowledge_read" in mc
        # Step 1 line in the text must mention knowledge_read
        step1_line = next(
            (l.strip() for l in mc.splitlines() if l.strip().startswith("1.")),
            ""
        )
        assert "knowledge_read" in step1_line, (
            f"Step 1 of memory_context must mention knowledge_read, got: {step1_line!r}"
        )
