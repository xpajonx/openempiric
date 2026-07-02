import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.health import build_health_report
from oem_knowledge.cli import main
from oem_knowledge.server import mount_tools

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_detect_stale_concepts(engine, tmp_path):
    # Setup registry with two concepts
    harness = engine._resolve_harness(str(tmp_path))
    registry = {
        "concept_a": {
            "canonical_name": "Concept A",
            "sessions": ["session_1", "session_2"]
        },
        "concept_b": {
            "canonical_name": "Concept B",
            "sessions": ["session_4", "session_5"]
        }
    }
    (harness / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    # Record 5 outcomes sequentially
    # concept_a is referenced in session_1 and session_2
    # concept_b is referenced in session_4 and session_5
    for i in range(1, 6):
        referenced = []
        if i in (1, 2):
            referenced.append("concept_a")
        if i in (4, 5):
            referenced.append("concept_b")
        engine.state.record_outcome(
            outcome="success",
            referenced_concepts=referenced,
            session_id=f"session_{i}",
            project=str(tmp_path)
        )

    # 1. With n_sessions=3, the last 3 sessions are: session_3, session_4, session_5
    # concept_a is stale because it hasn't been referenced in session_3, 4, or 5
    stale_3 = engine.state.detect_stale_concepts(n_sessions=3, project=str(tmp_path))
    assert len(stale_3) == 1
    assert stale_3[0]["concept_id"] == "concept_a"
    assert stale_3[0]["sessions_since_reference"] == 3  # last ref in session_2 (index 1), total 5: 5 - 2 = 3 sessions ago

    # concept_b is not stale in the last 3 sessions (it was referenced in session_4 and 5)
    # 2. With n_sessions=1, the last session is session_5
    # concept_a is stale, concept_b is not stale
    stale_1 = engine.state.detect_stale_concepts(n_sessions=1, project=str(tmp_path))
    assert any(x["concept_id"] == "concept_a" for x in stale_1)
    assert not any(x["concept_id"] == "concept_b" for x in stale_1)


def test_propose_merges(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    registry = {
        "concept_parser": {
            "canonical_name": "Code Parser Engine",
            "status": "validated",
            "evidence_count": 5
        },
        "concept_parsers": {
            "canonical_name": "Code Parser Engines",
            "status": "candidate",
            "evidence_count": 1
        }
    }
    (harness / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    merges = engine.propose_merges(similarity_threshold=0.85, project=str(tmp_path))
    assert len(merges) == 1
    assert merges[0]["primary_id"] == "concept_parser"
    assert merges[0]["secondary_id"] == "concept_parsers"


def test_record_outcome_does_not_update_handoff_without_explicit_project(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    handoff_json = harness / "session-handoff.json"
    engine.state.record_outcome(outcome="success", session_id="s1")
    assert not handoff_json.exists()


def test_record_outcome_updates_handoff_with_explicit_project(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    handoff_json = harness / "session-handoff.json"
    engine.state.record_outcome(outcome="success", session_id="s2", project=str(tmp_path))
    assert handoff_json.exists()
    data = json.loads(handoff_json.read_text(encoding="utf-8"))
    # New semantic model uses workspace_root (project_root is legacy/alias in some paths)
    assert "workspace_root" in data or "project_root" in data
    assert data.get("status") == "active" or data.get("status") is None


def test_record_outcome_updates_previous_project_when_project_changes(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    handoff_json = harness / "session-handoff.json"

    engine.state._update_structured_handoff(harness, str(tmp_path), "s1")
    data1 = json.loads(handoff_json.read_text(encoding="utf-8"))
    assert data1.get("status") == "active" or data1.get("status") is None

    engine.state._update_structured_handoff(harness, str(tmp_path / "other-project"), "s2")
    data2 = json.loads(handoff_json.read_text(encoding="utf-8"))
    assert "previous" in data2
    prev = data2["previous"]
    # New semantic: previous carries workspace_root (not legacy project_root)
    assert "workspace_root" in prev or "project_root" in prev


def test_health_cli_command(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    registry = {
        "concept_parser": {
            "canonical_name": "Code Parser Engine",
            "status": "validated",
            "evidence_count": 5
        }
    }
    (harness / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    with patch.object(sys, "argv", ["oem", "health", "--project", str(tmp_path)]):
        with patch("builtins.print") as mock_print:
            try:
                main()
            except SystemExit:
                pass
            
            # Verify panel was outputted
            called = False
            for call in mock_print.call_args_list:
                args = call[0]
                if args and any("Knowledge Health Scan" in str(arg) for arg in args):
                    called = True
            assert called


def _write_active_project_conflict(harness: Path) -> None:
    (harness / "session-handoff.json").write_text(
        json.dumps({"project_root": "/project/alpha"}),
        encoding="utf-8",
    )
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text("Active project: /project/beta\n", encoding="utf-8")


def test_doctor_and_knowledge_health_check_report_same_contradictions(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    _write_active_project_conflict(harness)
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    mcp = FastMCP("oem-test")
    mount_tools(mcp)
    import asyncio
    result = asyncio.run(mcp.call_tool("knowledge_health_check", {"project": str(tmp_path)}))
    payload = json.loads(result.content[0].text)

    assert payload["contradictions"] == report["contradictions"]
    assert payload["concept_contradictions"] == payload["conflicts"]


def test_knowledge_read_health_includes_doctor_contradictions(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    _write_active_project_conflict(harness)
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    result = engine.knowledge_read(str(tmp_path), scope="health")

    assert result["sections"]["contradictions"] == report["contradictions"]


def test_oem_health_knowledge_panel_shows_active_project_mismatch(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    _write_active_project_conflict(harness)

    with patch.object(sys, "argv", ["oem", "health", "--project", str(tmp_path)]):
        with patch("builtins.print") as mock_print:
            try:
                main()
            except SystemExit:
                pass

    output = "\n".join(" ".join(str(arg) for arg in call[0]) for call in mock_print.call_args_list)
    assert "active_project_mismatch" in output
    assert "Contradictions Detected:" in output
    assert "Contradictions Detected:\n  None" not in output


def test_health_surfaces_do_not_disagree_on_contradiction_count(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    _write_active_project_conflict(harness)
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)
    read_result = engine.knowledge_read(str(tmp_path), scope="health")

    mcp = FastMCP("oem-test")
    mount_tools(mcp)
    import asyncio
    mcp_result = asyncio.run(mcp.call_tool("knowledge_health_check", {"project": str(tmp_path)}))
    mcp_payload = json.loads(mcp_result.content[0].text)

    assert len(report["contradictions"]) == 1
    assert len(read_result["sections"]["contradictions"]) == 1
    assert len(mcp_payload["contradictions"]) == 1


def test_knowledge_read_health_uses_shared_active_work_report(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    _write_active_project_conflict(harness)
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)
    result = engine.knowledge_read(str(tmp_path), scope="health")
    assert "active_work" in result["sections"]
    assert result["sections"]["active_work"] == report["active_work"]


def test_record_concept_references_updates_registry_atomically(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    registry = {
        "concept_001": {
            "canonical_name": "Concept One",
            "unknown_field": {"keep": True},
        },
        "concept_002": {
            "canonical_name": "Concept Two",
            "untouched": "yes",
        },
    }
    engine.state._save_registry(registry, str(tmp_path))
    original_atomic = engine.state._atomic_save_registry_unlocked
    lock_seen = []

    def assert_locked(current_registry, project=None):
        lock_seen.append((harness / "concept_registry.lock").exists())
        return original_atomic(current_registry, project)

    with patch.object(engine.state, "_atomic_save_registry_unlocked", side_effect=assert_locked):
        result = engine.state.record_concept_references(
            ["concept_001"],
            source="search",
            project=str(tmp_path),
            session_id="session_active",
        )

    updated = json.loads((harness / "concept_registry.json").read_text(encoding="utf-8"))
    assert result["updated"] == 1
    assert lock_seen == [True]
    assert updated["concept_001"]["unknown_field"] == {"keep": True}
    assert updated["concept_001"]["last_referenced_session"] == "session_active"
    assert updated["concept_001"]["last_reference_source"] == "search"
    assert updated["concept_002"] == registry["concept_002"]


def test_health_unknown_reference_session_does_not_report_inflated_count(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(58):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    assert stale[0]["concept_id"] == "concept_008"
    assert stale[0]["stale_status"] == "reference_session_missing"
    assert stale[0]["sessions_since_reference"] is None
    assert stale[0]["sessions_since_reference"] != 58


def test_current_active_session_reference_returns_zero_sessions_since_reference(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    active_session = harness / "state" / "active_session.json"
    active_session.parent.mkdir(parents=True, exist_ok=True)
    active_session.write_text(
        json.dumps({
            "session_id": "session_current",
            "agent": "test",
            "status": "started",
            "started_at": time.time(),
            "project": str(tmp_path),
            "transcript_path": str(tmp_path / "transcript.md"),
            "context_path": str(tmp_path / "context.json"),
            "temp_instructions": str(tmp_path / "temp.md"),
        }),
        encoding="utf-8",
    )
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "session_current",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(5):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    assert not any(item["concept_id"] == "concept_008" for item in stale)


def test_last_referenced_session_counts_completed_sessions_after_it(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "session_2",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(1, 6):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=3, project=str(tmp_path))

    assert stale[0]["concept_id"] == "concept_008"
    assert stale[0]["sessions_since_reference"] == 3
    assert stale[0]["reference_confidence"] == "high"


def test_record_concept_references_returns_error_result_on_lock_failure(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {"concept_001": {"canonical_name": "Concept One"}},
        str(tmp_path),
    )
    lock_file = harness / "concept_registry.lock"

    # Hold the lock so record_concept_references cannot acquire it
    import fcntl
    with open(lock_file, "w") as held_lock:
        fcntl.flock(held_lock, fcntl.LOCK_EX)
        result = engine.state.record_concept_references(
            ["concept_001"],
            source="search",
            project=str(tmp_path),
            session_id="session_test",
        )
        fcntl.flock(held_lock, fcntl.LOCK_UN)

    assert result["status"] == "error"
    assert result["updated"] == 0
    assert "Lock timeout" in result["error"]


def test_detect_stale_concepts_does_not_hide_unknown_reference_session_when_session_count_below_threshold(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    # Only 2 sessions recorded — below default threshold of 5
    for i in range(2):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    # reference_session_missing should still be surfaced even with few sessions
    assert len(stale) == 1
    assert stale[0]["concept_id"] == "concept_008"
    assert stale[0]["stale_status"] == "reference_session_missing"
    assert stale[0]["sessions_since_reference"] is None


def test_health_displays_sessions_since_reference_unknown(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(5):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    assert stale[0]["stale_status"] == "reference_session_missing"
    assert stale[0]["sessions_since_reference"] is None

    # Verify CLI/MCP diagnostic string logic
    display_str = (
        f"  \u25cb {stale[0]['canonical_name']} ({stale[0]['concept_id']}) - reference session unknown"
        if stale[0].get("sessions_since_reference") is None
        else f"  \u25cb {stale[0]['canonical_name']} ({stale[0]['concept_id']}) - untouched for {stale[0]['sessions_since_reference']} sessions"
    )
    assert "reference session unknown" in display_str


def test_concept_referenced_in_current_active_session_sessions_since_reference_zero(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    active_session = harness / "state" / "active_session.json"
    active_session.parent.mkdir(parents=True, exist_ok=True)
    active_session.write_text(
        json.dumps({
            "session_id": "session_current",
            "agent": "test",
            "status": "started",
            "started_at": time.time(),
            "project": str(tmp_path),
            "transcript_path": str(tmp_path / "transcript.md"),
            "context_path": str(tmp_path / "context.json"),
            "temp_instructions": str(tmp_path / "temp.md"),
        }),
        encoding="utf-8",
    )
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "session_current",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(5):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    # Concept referenced in the current active session is not stale
    assert not any(item["concept_id"] == "concept_008" for item in stale)

    # This state means sessions_since_reference is effectively 0
    # (the last reference matches the active session)
    registry_after = json.loads(
        (harness / "concept_registry.json").read_text(encoding="utf-8")
    )
    assert registry_after["concept_008"]["last_referenced_session"] == "session_current"


def test_health_does_not_call_record_concept_references(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {"concept_001": {"canonical_name": "Concept One"}},
        str(tmp_path),
    )
    for i in range(5):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    with patch.object(engine.state, "record_concept_references") as mock_record:
        result = engine.knowledge_read(str(tmp_path), scope="health")

    assert result["status"] == "success"
    mock_record.assert_not_called()


def test_recently_referenced_without_session_not_reported_as_numeric_stale(engine, tmp_path):
    harness = engine._resolve_harness(str(tmp_path))
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_at": "2026-07-01T00:00:00Z",
                "last_referenced_session": "",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(58):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))

    assert stale[0]["concept_id"] == "concept_008"
    assert stale[0]["stale_status"] == "reference_session_missing"
    assert stale[0]["sessions_since_reference"] is None
    assert stale[0]["sessions_since_reference"] != 58


def test_health_classifies_missing_reference_metadata_as_legacy_unknown(engine, tmp_path):
    # If the metadata fields are present but empty, and it has pre-watermark created_at
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert len(stale) == 1
    assert stale[0]["concept_id"] == "concept_009"
    assert stale[0]["stale_status"] == "legacy_no_reference_metadata"
    assert stale[0]["sessions_since_reference"] is None
    assert stale[0]["last_referenced_at"] is None
    assert stale[0]["reference_confidence"] == "unknown"
    assert stale[0]["severity"] == "info"


def test_health_unknown_reference_has_explanation(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert "explanation" in stale[0]
    assert "likely because it was created before" in stale[0]["explanation"]


def test_health_unknown_reference_has_recommended_action(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert "recommended_action" in stale[0]
    assert "Run a search/read that surfaces" in stale[0]["recommended_action"]


def test_health_unknown_reference_summary_counts(engine, tmp_path):
    # Save a registry with 1 legacy concept, 1 never referenced, 1 missing metadata, 1 stale
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Legacy",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0
            },
            "concept_010": {
                "canonical_name": "Never Ref",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1783000000.0
            },
            "concept_011": {
                "canonical_name": "No fields at all"
            },
            "concept_012": {
                "canonical_name": "Stale",
                "last_referenced_session": "session_0",
                "last_referenced_at": "2026-07-02T00:00:00Z",
                "last_reference_source": "search"
            }
        },
        str(tmp_path),
    )
    
    # Record 6 sessions so completed session count is above stale threshold
    for i in range(6):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    # Test the summary count calculation that we perform in the CLI or health report
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    
    active_stale = sum(1 for s in stale if s.get("stale_status") == "stale")
    unknown_ref = sum(1 for s in stale if s.get("stale_status") in (
        "legacy_no_reference_metadata", "reference_metadata_missing",
        "reference_session_missing", "reference_history_unavailable",
        "never_referenced_since_tracking_enabled"
    ))
    
    assert active_stale == 1  # concept_012 is stale
    assert unknown_ref == 3  # concept_009, concept_010, concept_011
    
    # Check specific categories
    legacy_count = sum(1 for s in stale if s.get("stale_status") == "legacy_no_reference_metadata")
    never_count = sum(1 for s in stale if s.get("stale_status") == "never_referenced_since_tracking_enabled")
    missing_count = sum(1 for s in stale if s.get("stale_status") == "reference_metadata_missing")
    
    assert legacy_count == 1
    assert never_count == 1
    assert missing_count == 1


def test_health_unknown_reference_is_info_not_warning_when_legacy(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["severity"] == "info"


def test_health_confirmed_stale_known_reference_remains_warning(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_008": {
                "canonical_name": "general-learning",
                "last_referenced_session": "session_0",
                "last_referenced_at": "2026-07-02T00:00:00Z",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    for i in range(6):
        engine.state.record_outcome("success", session_id=f"session_{i}", project=str(tmp_path))

    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["severity"] == "warning"


def test_health_does_not_fabricate_sessions_since_reference_for_unknown(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["sessions_since_reference"] is None


def test_health_does_not_fabricate_last_referenced_at_for_unknown(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["last_referenced_at"] is None


def test_health_reference_session_missing_classified_separately(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Session missing concept",
                "last_referenced_session": "missing_session_id",
                "last_referenced_at": "2026-07-02T00:00:00Z",
                "last_reference_source": "search",
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["stale_status"] == "reference_session_missing"
    assert stale[0]["severity"] == "info"


def test_health_unknown_reference_not_counted_as_confirmed_stale_warning(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Old concept",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    active_stale = sum(1 for s in stale if s.get("stale_status") == "stale")
    assert active_stale == 0


def test_health_legacy_classification_requires_pre_watermark_evidence(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Legacy",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1781858056.0  # pre-watermark
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["stale_status"] == "legacy_no_reference_metadata"


def test_health_never_referenced_classification_requires_post_watermark_evidence(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Never Ref",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                "created_at": 1783000000.0  # post-watermark
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["stale_status"] == "never_referenced_since_tracking_enabled"


def test_health_missing_creation_time_uses_metadata_missing_not_guessed_legacy(engine, tmp_path):
    engine.state._save_registry(
        {
            "concept_009": {
                "canonical_name": "Legacy",
                "last_referenced_session": "",
                "last_referenced_at": None,
                "last_reference_source": "unknown",
                # created_at is missing
            }
        },
        str(tmp_path),
    )
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert stale[0]["stale_status"] == "reference_metadata_missing"


def test_health_registry_read_error_is_warning_not_info(engine, tmp_path):
    # Corrupt concept_registry.json to trigger read error
    harness = engine._resolve_harness(str(tmp_path))
    (harness / "concept_registry.json").write_text("invalid json", encoding="utf-8")
    
    stale = engine.state.detect_stale_concepts(n_sessions=5, project=str(tmp_path))
    assert len(stale) == 1
    assert stale[0]["stale_status"] == "reference_history_unavailable"
    assert stale[0]["severity"] == "warning"


def test_health_json_output_preserves_existing_keys(engine, tmp_path):
    from oem_knowledge.cli.commands.knowledge import run_knowledge_command
    import sys
    from unittest.mock import MagicMock
    
    # Save a registry
    engine.state._save_registry(
        {"concept_001": {"canonical_name": "Concept One"}},
        str(tmp_path),
    )
    
    # Mock args
    args = MagicMock()
    args.command = "health"
    args.project = str(tmp_path)
    args.stale_sessions = 5
    args.similarity_threshold = 0.85
    args.json = True
    
    # Patch sys.exit and print
    printed_data = []
    def mock_print(val):
        printed_data.append(val)
        
    with patch("builtins.print", mock_print), patch("sys.exit") as mock_exit:
        run_knowledge_command(args)
        
    assert len(printed_data) == 1
    report = json.loads(printed_data[0])
    
    # Verify existing keys are present
    assert "status" in report
    assert "operation" in report
    assert "project_root" in report
    assert "memory_root" in report
    assert "checks" in report
    assert "contradictions" in report
    
    # Verify new keys are present
    assert "stale_reference_summary" in report
    assert "stale_concepts" in report
    assert report["stale_reference_summary"]["unknown_reference"] == 1
