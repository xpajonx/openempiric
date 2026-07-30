"""Tests for rebuild_registry as the primary snapshot computation path.

Phase 0 of the OEM layered architecture rebuild: proving that rebuild_registry()
produces identical output from identical event logs (idempotency) and that
user-scoped events are handled correctly.
"""

import json
import pytest
import shutil
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project with initialized OEM memory."""
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)


def _append_events(engine: KnowledgeEngine, project_dir: Path, events: list[dict]):
    """Helper: write events directly to events.jsonl."""
    events_path = engine._events_path(str(project_dir))
    with open(events_path, "a") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def test_rebuild_registry_is_idempotent(temp_project):
    """Double replay of the same events produces identical registry."""
    engine = KnowledgeEngine(temp_project)

    events = [
        {
            "event_id": "ev-001",
            "timestamp": "2026-07-30T00:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-1",
            "event_type": "decision",
            "concept_candidates": ["use-sqlite"],
            "summary": "Decided to use SQLite for local storage",
            "evidence": "SQLite is embedded and serverless",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
        {
            "event_id": "ev-002",
            "timestamp": "2026-07-30T01:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-1",
            "event_type": "failure",
            "concept_candidates": ["use-sqlite"],
            "summary": "SQLite WAL mode caused lock contention",
            "evidence": "Multiple writers locked up under WAL",
            "confidence": 3,
            "source": "agent_structured",
            "schema_version": 1,
        },
        {
            "event_id": "ev-003",
            "timestamp": "2026-07-30T02:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-2",
            "event_type": "decision",
            "concept_candidates": ["use-postgres"],
            "summary": "Decided to move to PostgreSQL for production",
            "evidence": "PostgreSQL handles concurrent writes better",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
    ]

    _append_events(engine, temp_project, events)

    # First rebuild
    result1 = engine.state.rebuild_registry(str(temp_project))
    assert result1["status"] == "success"

    registry1 = engine.state._load_registry(str(temp_project))

    # Delete the registry file to force a fresh rebuild
    registry_path = engine._registry_path(str(temp_project))
    if registry_path.exists():
        registry_path.unlink()

    # Second rebuild from same events
    result2 = engine.state.rebuild_registry(str(temp_project))
    assert result2["status"] == "success"

    registry2 = engine.state._load_registry(str(temp_project))

    # Same number of concepts
    assert len(registry1) == len(registry2)

    # Each concept should have identical data
    for cid in registry1:
        assert cid in registry2
        c1 = registry1[cid]
        c2 = registry2[cid]
        assert c1["canonical_name"] == c2["canonical_name"]
        assert c1["status"] == c2["status"]
        assert c1["evidence_count"] == c2["evidence_count"]
        assert sorted(c1.get("aliases", [])) == sorted(c2.get("aliases", []))
        assert c1.get("concept_id") == c2.get("concept_id")


def test_rebuild_registry_preserves_event_data(temp_project):
    """Rebuild captures all event data: evidence_count, source_event_ids, sessions."""
    engine = KnowledgeEngine(temp_project)

    events = [
        {
            "event_id": "ev-100",
            "timestamp": "2026-07-30T00:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-a",
            "event_type": "observation",
            "concept_candidates": ["feature-x"],
            "summary": "Working on feature X",
            "evidence": "Started implementation of feature X",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
        {
            "event_id": "ev-101",
            "timestamp": "2026-07-30T01:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-a",
            "event_type": "observation",
            "concept_candidates": ["feature-x"],
            "summary": "Feature X progress",
            "evidence": "Completed core logic for feature X",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
        {
            "event_id": "ev-102",
            "timestamp": "2026-07-30T02:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-b",
            "event_type": "failure",
            "concept_candidates": ["feature-x"],
            "summary": "Feature X broke in CI",
            "evidence": "Integration test failed for feature X",
            "confidence": 3,
            "source": "agent_structured",
            "schema_version": 1,
        },
    ]

    _append_events(engine, temp_project, events)
    engine.state.rebuild_registry(str(temp_project))

    registry = engine.state._load_registry(str(temp_project))

    # Find feature-x concept
    feature_concept = None
    for cid, cdata in registry.items():
        if cdata["canonical_name"] == "feature-x":
            feature_concept = cdata
            break

    assert feature_concept is not None
    # Three events -> evidence_count should be 3
    assert feature_concept["evidence_count"] == 3
    # All three event IDs should be in source_event_ids
    assert "ev-100" in feature_concept["source_event_ids"]
    assert "ev-101" in feature_concept["source_event_ids"]
    assert "ev-102" in feature_concept["source_event_ids"]
    # Two distinct sessions
    assert "sess-a" in feature_concept.get("sessions", [])
    assert "sess-b" in feature_concept.get("sessions", [])


def test_rebuild_registry_command_log_events_filtered(temp_project):
    """Command log events are filtered out during rebuild."""
    engine = KnowledgeEngine(temp_project)

    events = [
        {
            "event_id": "ev-good",
            "timestamp": "2026-07-30T00:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-1",
            "event_type": "decision",
            "concept_candidates": ["valid-concept"],
            "summary": "We chose FastAPI over Flask",
            "evidence": "FastAPI has better async support",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
        {
            "event_id": "ev-cmd",
            "timestamp": "2026-07-30T01:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-1",
            "event_type": "observation",
            "concept_candidates": ["valid-concept"],
            "summary": "Command `pip install fastapi` executed with exit code 0",
            "evidence": "Output: Successfully installed fastapi-0.100.0",
            "confidence": 1,
            "source": "opencode_hook",
            "schema_version": 1,
        },
    ]

    _append_events(engine, temp_project, events)
    engine.state.rebuild_registry(str(temp_project))

    registry = engine.state._load_registry(str(temp_project))

    # Find valid-concept
    valid_concept = None
    for cid, cdata in registry.items():
        if cdata["canonical_name"] == "valid-concept":
            valid_concept = cdata
            break

    assert valid_concept is not None
    # Only the good event should count (command log filtered)
    assert valid_concept["evidence_count"] == 1
    assert "ev-good" in valid_concept.get("source_event_ids", [])
    assert "ev-cmd" not in valid_concept.get("source_event_ids", [])


def test_rebuild_registry_preserves_user_scoped_events(temp_project):
    """User-scoped events are included when include_user=True but not otherwise."""
    engine = KnowledgeEngine(temp_project)

    # Project-scoped event
    project_events = [
        {
            "event_id": "ev-proj-001",
            "timestamp": "2026-07-30T00:00:00Z",
            "project": str(temp_project),
            "session_id": "sess-1",
            "event_type": "decision",
            "concept_candidates": ["project-rule"],
            "summary": "This project uses black for formatting",
            "evidence": "Configured pyproject.toml with black",
            "confidence": 4,
            "source": "agent_structured",
            "schema_version": 1,
        },
    ]
    _append_events(engine, temp_project, project_events)

    # Write a user-scoped event to the user events file
    from oem_knowledge.services.state import get_user_events_path
    user_path = get_user_events_path()
    user_event = {
        "event_id": "ev-user-002",
        "timestamp": "2026-07-30T00:00:00Z",
        "project": str(temp_project),
        "session_id": "sess-1",
        "event_type": "preference",
        "concept_candidates": ["user-style"],
        "summary": "Prefers functional style over OOP",
        "evidence": "Always writes pure functions, avoids classes",
        "confidence": 4,
        "source": "inline_agent",
        "schema_version": 1,
        "scope": "user",
    }
    if user_path:
        user_path.parent.mkdir(parents=True, exist_ok=True)
        with open(user_path, "a") as f:
            f.write(json.dumps(user_event) + "\n")

    # Rebuild without user events (default)
    result_default = engine.state.rebuild_registry(str(temp_project))
    assert result_default["status"] == "success"

    registry_default = engine.state._load_registry(str(temp_project))
    has_user_concept = any(
        cdata["canonical_name"] == "user-style"
        for cdata in registry_default.values()
    )
    # Without include_user=True, user-scoped concepts are NOT in the project registry
    assert not has_user_concept, (
        "User-scoped concepts should NOT appear in project registry without include_user=True"
    )
    # Project concepts should still be there
    has_proj_concept = any(
        cdata["canonical_name"] == "project-rule"
        for cdata in registry_default.values()
    )
    assert has_proj_concept, "Project-scoped concepts should be in the project registry"

    # Now load events with include_user=True and verify user events are accessible
    all_events = engine.state._load_events(str(temp_project), include_user=True)
    user_event_ids = [e["event_id"] for e in all_events if e.get("scope") == "user"]
    assert "ev-user-002" in user_event_ids, (
        "User-scoped event should be returned when include_user=True"
    )


def test_rebuild_registry_empty_events_produces_empty_registry(temp_project):
    """Rebuild with no events produces an empty registry."""
    engine = KnowledgeEngine(temp_project)

    # No events written -- events file is empty
    result = engine.state.rebuild_registry(str(temp_project))
    assert result["status"] == "success"

    registry = engine.state._load_registry(str(temp_project))
    assert registry == {}
    assert result["materialized"] == 0


def test_rebuild_registry_skips_corrupt_event_lines(temp_project):
    """Corrupt JSON lines in events.jsonl are skipped during rebuild."""
    engine = KnowledgeEngine(temp_project)

    # Write valid events + one corrupt line
    events_path = engine._events_path(str(temp_project))

    valid_event = {
        "event_id": "ev-valid",
        "timestamp": "2026-07-30T00:00:00Z",
        "project": str(temp_project),
        "session_id": "sess-1",
        "event_type": "decision",
        "concept_candidates": ["valid-concept"],
        "summary": "Valid decision",
        "evidence": "Valid evidence",
        "confidence": 4,
        "source": "agent_structured",
        "schema_version": 1,
    }

    with open(events_path, "w") as f:
        f.write(json.dumps(valid_event) + "\n")
        f.write("{corrupt_json_line_not_parseable\n")
        f.write(json.dumps(valid_event) + "\n")

    result = engine.state.rebuild_registry(str(temp_project))
    assert result["status"] == "success"

    registry = engine.state._load_registry(str(temp_project))
    # valid-concept should exist (built from the two valid events)
    valid_concept = None
    for cid, cdata in registry.items():
        if cdata["canonical_name"] == "valid-concept":
            valid_concept = cdata
            break

    assert valid_concept is not None
    # Two valid events -> evidence_count should be 2
    assert valid_concept["evidence_count"] == 2
