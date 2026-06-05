from __future__ import annotations

import json
import tempfile
import shutil
import time
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_record_outcome_explicit(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    res = eng.record_outcome(
        outcome="success",
        referenced_concepts=["concept_001", "concept_002"],
        reason="Fully completed implementation plan",
        session_id="session_test_123",
        project=tmp_proj,
    )

    assert res["status"] == "success"
    assert res["session_id"] == "session_test_123"
    assert res["outcome"] == "success"
    assert res["referenced_concepts"] == ["concept_001", "concept_002"]
    assert res["reason"] == "Fully completed implementation plan"

    # Verify written file
    outcomes_file = Path(tmp_proj) / OEM_DIR / "state" / "outcomes.jsonl"
    assert outcomes_file.exists()
    lines = outcomes_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["schema_version"] == 1
    assert entry["session_id"] == "session_test_123"
    assert entry["outcome"] == "success"
    assert entry["referenced_concepts"] == ["concept_001", "concept_002"]
    assert entry["reason"] == "Fully completed implementation plan"
    assert "timestamp" in entry
    assert "metrics" in entry


def test_record_outcome_state_fallback(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # 1. Create a mock session_state.json
    state_dir = Path(tmp_proj) / OEM_DIR / "state"
    session_state_file = state_dir / "session_state.json"
    state_data = {
        "session_id": "session_fall_999",
        "last_injected_concepts": ["concept_003", "concept_004"],
        "last_injected_at": "2026-06-04T00:00:00Z"
    }
    session_state_file.write_text(json.dumps(state_data), encoding="utf-8")

    # 2. Record outcome with auto-resolution
    res = eng.record_outcome(
        outcome="failure",
        project=tmp_proj,
    )

    assert res["status"] == "success"
    assert res["session_id"] == "session_fall_999"
    assert res["outcome"] == "failure"
    assert res["referenced_concepts"] == ["concept_003", "concept_004"]
    assert res["reason"] is None


def test_record_outcome_generated_session_id(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # No session_state.json or passed session_id
    res = eng.record_outcome(
        outcome="abandoned",
        project=tmp_proj,
    )

    assert res["status"] == "success"
    assert res["session_id"].startswith("session_")
    assert res["session_id"] != "session_unknown"
    assert res["outcome"] == "abandoned"
    assert res["referenced_concepts"] == []


def test_record_outcome_append_only_history(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # Record 3 outcomes sequentially
    eng.record_outcome("success", ["concept_a"], "reason 1", "session_1", tmp_proj)
    eng.record_outcome("failure", ["concept_b"], "reason 2", "session_2", tmp_proj)
    eng.record_outcome("abandoned", ["concept_c"], "reason 3", "session_3", tmp_proj)

    outcomes_file = Path(tmp_proj) / OEM_DIR / "state" / "outcomes.jsonl"
    lines = outcomes_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3

    # Validate ordering
    entry1 = json.loads(lines[0])
    entry2 = json.loads(lines[1])
    entry3 = json.loads(lines[2])

    assert entry1["session_id"] == "session_1"
    assert entry1["outcome"] == "success"

    assert entry2["session_id"] == "session_2"
    assert entry2["outcome"] == "failure"

    assert entry3["session_id"] == "session_3"
    assert entry3["outcome"] == "abandoned"


def test_record_outcome_with_goal_satisfaction(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    res = eng.record_outcome(
        outcome="success",
        referenced_concepts=["concept_001"],
        reason="Completed with minor feedback",
        session_id="session_sat_123",
        project=tmp_proj,
        goal_satisfaction=0.7,
    )

    assert res["status"] == "success"
    assert res["goal_satisfaction"] == 0.7

    outcomes_file = Path(tmp_proj) / OEM_DIR / "state" / "outcomes.jsonl"
    lines = outcomes_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert entry["goal_satisfaction"] == 0.7


def test_weighted_fitness_calculation(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # Setup concept registry
    harness = eng._resolve_harness(tmp_proj)
    registry = {
        "concept_a": {"canonical_name": "Concept A", "status": "validated"},
        "concept_b": {"canonical_name": "Concept B", "status": "validated"}
    }
    (harness / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    # Record outcomes with varying goal satisfactions
    eng.record_outcome("success", ["concept_a"], "reason 1", "session_1", tmp_proj, goal_satisfaction=1.0)
    eng.record_outcome("failure", ["concept_a"], "reason 2", "session_2", tmp_proj, goal_satisfaction=0.5)
    eng.record_outcome("success", ["concept_b"], "reason 3", "session_3", tmp_proj, goal_satisfaction=0.8)

    fitness = eng.calculate_fitness(tmp_proj)
    assert fitness["concept_a"].fitness_score == 0.75
    assert fitness["concept_b"].fitness_score == 0.8

