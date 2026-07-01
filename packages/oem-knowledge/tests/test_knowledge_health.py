import json
import sys
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
