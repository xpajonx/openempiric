from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from fastmcp import FastMCP
from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.server import mount_tools

mcp = FastMCP("openempiric")
mount_tools(mcp)


@pytest.fixture
def tmp_proj():
    """Create a temp project with empty .harness/ structure."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def _call(tool: str, params: dict | None = None):
    """Helper to call an MCP tool synchronously."""
    if params is None:
        params = {}
    return asyncio.run(mcp.call_tool(tool, params))


class TestCriteria:
    """7 success criteria for harness-mcp."""

    def test_c1_knowledge_init_creates_harness(self, tmp_proj):
        """C2: knowledge_init creates a .oem/ tree."""
        result = _call("knowledge_init", {"project": tmp_proj})
        assert (Path(tmp_proj) / OEM_DIR).is_dir()
        assert (Path(tmp_proj) / OEM_DIR / "wiki").is_dir()
        assert (Path(tmp_proj) / OEM_DIR / "state").is_dir()
        assert "OpenEmpiric Initialized" in result.content[0].text

    def test_c2_knowledge_search_returns_results(self, tmp_proj):
        """C3: knowledge_search works (even with 0 results)."""
        _call("knowledge_init", {"project": tmp_proj})
        result = _call("knowledge_search", {"query": "test", "project": tmp_proj})
        assert result.content[0].text is not None
        assert "Search" in result.content[0].text

    def test_c3_knowledge_session_commit(self, tmp_proj):
        """C4: knowledge_session_commit writes session report + materializes concepts."""
        _call("knowledge_init", {"project": tmp_proj})

        # Commit with structured conversation text
        convo = (
            "decision: Use FastAPI for the backend\n"
            "experiment: Tried PostgreSQL with pgvector\n"
            "validation: Response time improved by 40%\n"
        )
        result = _call(
            "knowledge_session_commit",
            {
                "project": tmp_proj,
                "conversation_text": convo,
            },
        )

        text = result.content[0].text
        assert "Session commit succeeded" in text
        assert "Commit Complete" in text

        # Check session report was written
        sessions_dir = Path(tmp_proj) / OEM_DIR / "sessions"
        assert sessions_dir.is_dir()
        assert len(list(sessions_dir.glob("*.md"))) >= 1

    def test_c4_knowledge_materialize_concepts(self, tmp_proj):
        """Verify concept materialization creates concept files."""
        _call("knowledge_init", {"project": tmp_proj})

        _call(
            "knowledge_session_commit",
            {
                "project": tmp_proj,
                "conversation_text": "decision: Use React for the frontend",
            },
        )

        result = _call("knowledge_materialize", {"project": tmp_proj})
        text = result.content[0].text
        assert "Materialization" in text or "materialized" in text.lower()

    def test_c5_todo_tools(self, tmp_proj):
        """C5: Todo tools return without error."""
        import json
        _call("knowledge_init", {"project": tmp_proj})

        # oem_todo_write
        result = _call(
            "oem_todo_write",
            {
                "items": json.dumps(
                    [
                        {"content": "do something", "status": "pending"},
                        {"content": "do something else", "status": "in_progress"},
                    ]
                ),
                "workdir": tmp_proj,
            },
        )
        assert "Todo list updated" in result.content[0].text

        # oem_todo_read
        result = _call("oem_todo_read", {"workdir": tmp_proj})
        assert "Todo list" in result.content[0].text

        # oem_todo_advance
        todos = json.loads(
            (Path(tmp_proj) / OEM_DIR / "state" / "todos.json").read_text()
        )
        result = _call(
            "oem_todo_advance",
            {
                "item_id": todos[0]["id"],
                "status": "completed",
                "workdir": tmp_proj,
            },
        )
        assert "Updated item" in result.content[0].text

    def test_c6_knowledge_session_start(self, tmp_proj):
        """C6: knowledge_session_start reads state and returns pre-injection context."""
        _call("knowledge_init", {"project": tmp_proj})

        # Write some state
        handoff = Path(tmp_proj) / OEM_DIR / "session-handoff.md"
        handoff.write_text(
            "# Session Handoff\n\n## Next Action\nComplete the auth module\n"
        )

        result = _call("knowledge_session_start", {"project": tmp_proj})
        text = result.content[0].text
        assert "Session Start" in text or "session" in text.lower()

    def test_c7_plan_and_todo_persistence(self, tmp_proj):
        """Verify plans and todos persist in .harness/state/."""
        _call("knowledge_init", {"project": tmp_proj})

        # Write todo
        import json

        _call(
            "oem_todo_write",
            {
                "items": json.dumps(
                    [
                        {"content": "test persistence", "status": "pending"},
                    ]
                ),
                "workdir": tmp_proj,
            },
        )

        # Verify file exists
        todo_file = Path(tmp_proj) / OEM_DIR / "state" / "todos.json"
        assert todo_file.exists()
        data = json.loads(todo_file.read_text())
        assert len(data) == 1
        assert data[0]["content"] == "test persistence"

    def test_knowledge_cli_parser(self):
        """Verify knowledge CLI parser structure and stats execution."""
        import sys
        from unittest.mock import patch
        from oem_knowledge.cli import main

        with patch.object(sys, "argv", ["oem", "stats"]):
            with patch("oem_knowledge.cli.KnowledgeEngine") as mock_engine:
                mock_engine.return_value.stats.return_value = {
                    "total_chunks": 0,
                    "db_size_mb": 0.0,
                    "harness_path": "/tmp",
                }
                try:
                    main()
                except SystemExit as e:
                    assert e.code == 0 or e.code is None

    def test_truncation_guard(self, tmp_proj):
        """Verify that truncation guard blocks heavily truncated updates."""
        _call("knowledge_init", {"project": tmp_proj})
        eng = KnowledgeEngine(tmp_proj)
        concepts_dir = Path(tmp_proj) / OEM_DIR / "wiki"
        concepts_dir.mkdir(parents=True, exist_ok=True)
        file_path = concepts_dir / "concept_001.md"

        long_content = "Some initial content. " * 50
        eng._safe_write_concept_file(file_path, long_content, tmp_proj)

        short_content = "Too short."
        with pytest.raises(ValueError, match="Truncation risk detected"):
            eng._safe_write_concept_file(file_path, short_content, tmp_proj)

    def test_path_traversal_protection(self, tmp_proj):
        """Verify that path traversal raises PermissionError."""
        _call("knowledge_init", {"project": tmp_proj})
        eng = KnowledgeEngine(tmp_proj)
        traversal_file = Path(tmp_proj) / "outside.md"
        with pytest.raises(PermissionError, match="Path traversal attempted"):
            eng._safe_write_concept_file(traversal_file, "Some content", tmp_proj)

    def test_typed_links_and_reciprocal(self, tmp_proj):
        """Verify that typed links and reciprocal links are correctly parsed and saved."""
        _call("knowledge_init", {"project": tmp_proj})
        eng = KnowledgeEngine(tmp_proj)

        registry = eng._load_registry(tmp_proj)
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "concept-one",
            "aliases": ["one"],
            "status": "validated",
            "confidence": 3,
            "evidence_count": 3,
            "session_count": 2,
            "sessions": ["session_one"],
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "concept-two",
            "aliases": ["two"],
            "status": "validated",
            "confidence": 3,
            "evidence_count": 3,
            "session_count": 2,
            "sessions": ["session_two"],
        }
        eng._save_registry(registry, tmp_proj)

        concepts_dir = Path(tmp_proj) / OEM_DIR / "wiki"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        c1_content = """---
concept_id: concept_001
canonical_name: concept-one
status: validated
confidence: 3
evidence_count: 3
session_count: 2
aliases: ["one"]
---
# Concept One
We link here: [[depends_on:concept_002|Concept Two]]."""
        (concepts_dir / "concept_001.md").write_text(c1_content)

        c2_content = """---
concept_id: concept_002
canonical_name: concept-two
status: validated
confidence: 3
evidence_count: 3
session_count: 2
aliases: ["two"]
---
# Concept Two"""
        (concepts_dir / "concept_002.md").write_text(c2_content)

        eng.update_graph(tmp_proj)

        updated_reg = eng._load_registry(tmp_proj)
        assert any(
            r["type"] == "depends_on" and r["target"] == "concept_002"
            for r in updated_reg["concept_001"]["relationships"]
        )
        assert any(
            r["type"] == "depended_on_by" and r["target"] == "concept_001"
            for r in updated_reg["concept_002"]["relationships"]
        )

        c2_new_content = (concepts_dir / "concept_002.md").read_text()
        assert "[[depended_on_by:concept_001|" in c2_new_content

    def test_linter_broken_and_orphans(self, tmp_proj):
        """Verify linter detects broken links and orphans."""
        _call("knowledge_init", {"project": tmp_proj})

        concepts_dir = Path(tmp_proj) / OEM_DIR / "wiki"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        c1_content = """---
concept_id: concept_001
canonical_name: concept-one
status: validated
confidence: 3
---
# Concept One
Broken: [[concept_999]]."""
        (concepts_dir / "concept_001.md").write_text(c1_content)

        import asyncio
        from oem_knowledge.linter import run_lint

        res = asyncio.run(run_lint(Path(tmp_proj)))

        assert res["status"] == "success"
        assert len(res["broken_links"]) == 1
        assert res["broken_links"][0]["target"] == "concept_999"
        assert res["broken_links"][0]["line"] == 8
        assert "concept_001" in res["orphans"]

    def test_linter_auto_healing(self, tmp_proj):
        """Verify linter heals broken links using aliases."""
        _call("knowledge_init", {"project": tmp_proj})
        eng = KnowledgeEngine(tmp_proj)

        registry = eng._load_registry(tmp_proj)
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "concept-two",
            "aliases": ["AI Safety"],
            "status": "validated",
            "confidence": 3,
            "evidence_count": 3,
            "session_count": 2,
            "sessions": ["session_two"],
        }
        eng._save_registry(registry, tmp_proj)

        concepts_dir = Path(tmp_proj) / OEM_DIR / "wiki"
        concepts_dir.mkdir(parents=True, exist_ok=True)

        c1_content = """---
concept_id: concept_001
canonical_name: concept-one
status: validated
confidence: 3
---
# Concept One
We discuss [[AI Safety]] here."""
        (concepts_dir / "concept_001.md").write_text(c1_content)

        import asyncio
        from oem_knowledge.linter import run_lint

        # First run without fix=True
        res = asyncio.run(run_lint(Path(tmp_proj), fix=False))
        assert len(res["healed_links"]) == 1
        assert res["healed_links"][0]["target_concept"] == "concept_002"
        assert res["fixed_files_count"] == 0

        # Now run with fix=True
        res_fix = asyncio.run(run_lint(Path(tmp_proj), fix=True))
        assert res_fix["fixed_files_count"] == 1

        updated_content = (concepts_dir / "concept_001.md").read_text()
        assert "[[concept_002|AI Safety]]" in updated_content

    def test_sfs_bounds_check(self, tmp_proj):
        """Verify SFS enforces directory boundaries and truncation rules."""
        from oem_knowledge.engine import SecureFileSystem

        sfs = SecureFileSystem(Path(tmp_proj))
        outside = Path(tmp_proj).parent / "outside_test.txt"

        # Traversal check
        with pytest.raises(PermissionError, match="outside project boundary"):
            sfs.write_text(outside, "some data")

        # Truncation check
        target = Path(tmp_proj) / "test_file.txt"
        sfs.write_text(target, "A very long text content " * 10)
        with pytest.raises(ValueError, match="Truncation risk detected"):
            sfs.write_text(target, "Short")
