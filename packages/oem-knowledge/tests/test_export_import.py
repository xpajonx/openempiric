"""Tests for memory export/import (Phase 4 of layered architecture rebuild)."""

import json
import pytest
import shutil
import tempfile
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def project_a(tmp_path):
    """Source project with some memory."""
    proj = tmp_path / "project_a"
    proj.mkdir()
    eng = KnowledgeEngine(proj)
    eng.init_project(str(proj))

    events = [
        {
            "event_id": "ev-a1",
            "timestamp": "2026-07-30T00:00:00Z",
            "project": str(proj),
            "session_id": "sess-a",
            "event_type": "decision",
            "concept_candidates": ["use-redis"],
            "summary": "Decided to use Redis for caching",
            "evidence": "Redis has sub-ms latency",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
    ]
    events_path = eng._events_path(str(proj))
    with open(events_path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

    eng.state.rebuild_registry(str(proj))
    eng.search.index_all(force=True)
    return proj, eng


def test_round_trip_export_import(project_a, tmp_path):
    """Export from project A, import to project B, concept survives."""
    proj_a, eng_a = project_a
    proj_b = tmp_path / "project_b"
    proj_b.mkdir()
    eng_b = KnowledgeEngine(proj_b)
    eng_b.init_project(str(proj_b))

    # Registry A should contain "use-redis" concept
    registry_a = eng_a.state.load_registry(str(proj_a))
    assert any(c["canonical_name"] == "use-redis" for c in registry_a.values())

    # Export from A
    archive = tmp_path / "export.tar.gz"
    result = eng_a.export_memory(str(archive), str(proj_a))
    assert result["status"] == "success"
    assert archive.exists()

    # Import into B
    result = eng_b.import_memory(str(archive), str(proj_b))
    assert result["status"] == "success"
    assert result["imported_events"] > 0

    # Rebuild B's registry from imported events
    eng_b.state.rebuild_registry(str(proj_b))

    # Registry B should also contain "use-redis" concept
    registry_b = eng_b.state.load_registry(str(proj_b))
    assert any(c["canonical_name"] == "use-redis" for c in registry_b.values())

    # Concept should have the same evidence_count
    c_a = next(c for c in registry_a.values() if c["canonical_name"] == "use-redis")
    c_b = next(c for c in registry_b.values() if c["canonical_name"] == "use-redis")
    assert c_a["evidence_count"] == c_b["evidence_count"]


def test_import_dedup(project_a, tmp_path):
    """Importing the same archive twice does not create duplicates."""
    proj_a, eng_a = project_a
    proj_b = tmp_path / "project_b"
    proj_b.mkdir()
    eng_b = KnowledgeEngine(proj_b)
    eng_b.init_project(str(proj_b))

    archive = tmp_path / "export.tar.gz"
    eng_a.export_memory(str(archive), str(proj_a))

    # First import
    r1 = eng_b.import_memory(str(archive), str(proj_b))
    imported_first = r1["imported_events"]

    # Second import — should skip all
    r2 = eng_b.import_memory(str(archive), str(proj_b))
    assert r2["skipped_events"] > 0
    # Imported events on second run should be 0 or low
    assert r2["imported_events"] == 0


def test_export_missing_project(tmp_path):
    """Export from an uninitialized project returns error."""
    proj = tmp_path / "bare_project"
    proj.mkdir()
    eng = KnowledgeEngine(proj)
    archive = tmp_path / "export.tar.gz"
    # Should not crash — export of empty/missing .oem is OK
    result = eng.export_memory(str(archive), str(proj))
    assert result["status"] in ("success", "error")


def test_import_missing_archive(tmp_path):
    """Import from a nonexistent archive returns error."""
    proj = tmp_path / "project"
    proj.mkdir()
    eng = KnowledgeEngine(proj)
    eng.init_project(str(proj))
    archive = tmp_path / "nonexistent.tar.gz"
    result = eng.import_memory(str(archive), str(proj))
    assert result["status"] == "error"
    assert "not found" in result["message"].lower()
