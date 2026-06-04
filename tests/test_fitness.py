from __future__ import annotations
import json
import math
import tempfile
import shutil
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_calculate_fitness_basic(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # Mock registry
    registry = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "knowledge-fitness",
            "aliases": ["fitness-engine"],
            "evidence_count": 5,
            "status": "canonical"
        },
        "concept_002": {
            "concept_id": "concept_002",
            "canonical_name": "outcome-tracking",
            "aliases": [],
            "evidence_count": 3,
            "status": "validated"
        }
    }
    eng._save_registry(registry, tmp_proj)

    # Record some session outcomes
    # Session 1: success, retrieved concept_001, referenced concept_001
    eng.record_outcome("success", ["concept_001"], "reason 1", "session_1", tmp_proj)
    # Session 2: failure, retrieved concept_001, referenced concept_001
    eng.record_outcome("failure", ["concept_001"], "reason 2", "session_2", tmp_proj)
    # Session 3: success, retrieved concept_001 & concept_002, referenced only concept_001 (concept_002 ignored)
    # We simulate this by writing directly or writing session state
    state_dir = Path(tmp_proj) / OEM_DIR / "state"
    session_state_file = state_dir / "session_state.json"
    session_state_file.write_text(json.dumps({
        "session_id": "session_3",
        "last_injected_concepts": ["concept_001", "concept_002"]
    }), encoding="utf-8")
    eng.record_outcome("success", ["concept_001"], "reason 3", "session_3", tmp_proj)

    fitness = eng.calculate_fitness(tmp_proj)

    # Check concept_001
    # Retrieved in session_1 (defaulted to referenced), session_2 (defaulted to referenced), session_3 (explicit injected)
    assert fitness["concept_001"].retrieved == 3
    assert fitness["concept_001"].referenced == 3
    assert fitness["concept_001"].ignored == 0
    assert fitness["concept_001"].successful_sessions == 2
    assert fitness["concept_001"].failed_sessions == 1
    assert fitness["concept_001"].evidence_count == 5
    assert fitness["concept_001"].fitness_score == pytest.approx(2 / 3, 0.001)

    # Check concept_002
    assert fitness["concept_002"].retrieved == 1
    assert fitness["concept_002"].referenced == 0
    assert fitness["concept_002"].ignored == 1
    assert fitness["concept_002"].successful_sessions == 0
    assert fitness["concept_002"].failed_sessions == 0
    assert fitness["concept_002"].evidence_count == 3
    assert fitness["concept_002"].fitness_score == 0.0


def test_concept_resolution_and_unregistered(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "knowledge-fitness",
            "aliases": ["fitness-engine"],
            "evidence_count": 2,
            "status": "canonical"
        }
    }
    eng._save_registry(registry, tmp_proj)

    # Record outcome referencing concept using alias and canonical name, and unregistered concept
    eng.record_outcome("success", ["fitness-engine", "unregistered-concept"], None, "session_1", tmp_proj)

    fitness = eng.calculate_fitness(tmp_proj)

    # fitness-engine should resolve to concept_001
    assert fitness["concept_001"].referenced == 1
    assert fitness["concept_001"].successful_sessions == 1

    # unregistered-concept should be tracked separately under its own ID
    assert "unregistered-concept" in fitness
    assert fitness["unregistered-concept"].referenced == 1
    assert fitness["unregistered-concept"].successful_sessions == 1
    assert fitness["unregistered-concept"].evidence_count == 0


def test_legacy_outcome_log_fallback(tmp_proj):
    # Older log records didn't have retrieved_concepts. Let's make sure it falls back.
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "knowledge-fitness",
            "aliases": [],
            "status": "canonical"
        }
    }
    eng._save_registry(registry, tmp_proj)

    # Write outcome record directly without retrieved_concepts
    outcomes_file = Path(tmp_proj) / OEM_DIR / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "schema_version": 1,
        "session_id": "session_legacy",
        "outcome": "success",
        "referenced_concepts": ["concept_001"],
        "timestamp": "2026-06-04T00:00:00Z"
    }) + "\n", encoding="utf-8")

    fitness = eng.calculate_fitness(tmp_proj)
    assert fitness["concept_001"].retrieved == 1
    assert fitness["concept_001"].referenced == 1
    assert fitness["concept_001"].ignored == 0
    assert fitness["concept_001"].successful_sessions == 1


def test_dashboard_composite_ranking(tmp_proj):
    """High evidence + high fitness should rank higher than high fitness + low evidence."""
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_a": {
            "concept_id": "concept_a",
            "canonical_name": "ai-safety",
            "aliases": [],
            "evidence_count": 20,
            "confidence": 5,
            "status": "canonical"
        },
        "concept_b": {
            "concept_id": "concept_b",
            "canonical_name": "retry-strategy",
            "aliases": [],
            "evidence_count": 1,
            "confidence": 2,
            "status": "candidate"
        },
    }
    eng._save_registry(registry, tmp_proj)

    # concept_a: 9/10 success rate but high evidence
    for i in range(9):
        eng.record_outcome("success", ["concept_a"], None, f"session_a_{i}", tmp_proj)
    eng.record_outcome("failure", ["concept_a"], None, "session_a_fail", tmp_proj)

    # concept_b: 10/10 success rate but only 1 evidence
    eng.record_outcome("success", ["concept_b"], None, "session_b_1", tmp_proj)

    fitness = eng.calculate_fitness(tmp_proj)

    fit_a = fitness["concept_a"]
    fit_b = fitness["concept_b"]

    # concept_b has 100% fitness but very low evidence
    assert fit_b.fitness_score == 1.0
    assert fit_a.fitness_score == pytest.approx(0.9, 0.001)

    # Composite ranking: fitness_score * (1 + 0.3 * log1p(evidence_count))
    composite_a = fit_a.fitness_score * (1.0 + 0.3 * math.log1p(fit_a.evidence_count))
    composite_b = fit_b.fitness_score * (1.0 + 0.3 * math.log1p(fit_b.evidence_count))

    # concept_a should outrank concept_b in composite score despite lower raw fitness
    assert composite_a > composite_b, (
        f"concept_a ({composite_a:.4f}) should rank above concept_b ({composite_b:.4f}) "
        f"because higher evidence makes it a stronger signal"
    )


def test_dashboard_untested_concepts(tmp_proj):
    """Concepts with no outcomes should appear as untested."""
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_new": {
            "concept_id": "concept_new",
            "canonical_name": "new-concept",
            "aliases": [],
            "evidence_count": 0,
            "confidence": 1,
            "status": "candidate"
        }
    }
    eng._save_registry(registry, tmp_proj)

    # No outcomes recorded
    fitness = eng.calculate_fitness(tmp_proj)

    fit = fitness["concept_new"]
    assert fit.referenced == 0
    assert fit.fitness_score == 0.0


def test_fitness_promotion(tmp_proj):
    """Test candidate is promoted to validated with High Fitness + minimum evidence."""
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_candidate": {
            "concept_id": "concept_candidate",
            "canonical_name": "high-fitness-candidate",
            "aliases": [],
            "status": "candidate",
            "confidence": 1,
            "evidence_count": 2,
        }
    }
    eng._save_registry(registry, tmp_proj)

    # 2 successes (High Fitness: 100%, succ_sessions=2, evidence=2)
    eng.record_outcome("success", ["concept_candidate"], None, "session_1", tmp_proj)
    eng.record_outcome("success", ["concept_candidate"], None, "session_2", tmp_proj)

    fitness = eng.calculate_fitness(tmp_proj)

    # Manually trigger evaluation
    cdata = registry["concept_candidate"]
    cdata = eng.evaluate_concept_status(cdata, "observation", "session_3", fitness_data=fitness)

    assert cdata["status"] == "validated"
    assert "Telemetry Correlation" in cdata["promotion_history"][-1]["reason"]
    assert "High fitness promotion" in cdata["promotion_history"][-1]["reason"]


def test_fitness_demotion_needs_review(tmp_proj):
    """Test validated concept demotes to needs_review under Repeated Failures with confidence awareness."""
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = {
        "concept_validated": {
            "concept_id": "concept_validated",
            "canonical_name": "flaky-concept",
            "aliases": [],
            "status": "validated",
            "confidence": 2,
            "evidence_count": 4,
        }
    }
    eng._save_registry(registry, tmp_proj)

    # 3 failures (Repeated Failures: fitness < 60%, failed_sessions=3 >= min_failures=3)
    for i in range(3):
        eng.record_outcome("failure", ["concept_validated"], None, f"session_fail_{i}", tmp_proj)

    fitness = eng.calculate_fitness(tmp_proj)

    # Trigger evaluation
    cdata = registry["concept_validated"]
    cdata = eng.evaluate_concept_status(cdata, "observation", "session_evaluation", fitness_data=fitness)

    assert cdata["status"] == "needs_review"
    assert "Telemetry Correlation" in cdata["promotion_history"][-1]["reason"]
    assert "Repeated failures" in cdata["promotion_history"][-1]["reason"]


def test_needs_review_materialization(tmp_proj):
    """Test that a needs_review concept stays materialized on disk and displays a warning header."""
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # Pre-populate registry with a needs_review concept
    registry = {
        "concept_flaky": {
            "concept_id": "concept_flaky",
            "canonical_name": "flaky-concept",
            "aliases": [],
            "status": "needs_review",
            "confidence": 1,
            "evidence_count": 5,
            "session_count": 5,
        }
    }
    eng._save_registry(registry, tmp_proj)

    # Create dummy session reports to trigger materialize_concepts
    sessions_dir = Path(tmp_proj) / OEM_DIR / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    
    session_report = sessions_dir / "session_20260604.md"
    session_report.write_text("""# Session Report
```json
{
  "knowledge_events": [
    {
      "concept": "flaky-concept",
      "type": "observation",
      "evidence": "Repeated error observed."
    }
  ]
}
```
""", encoding="utf-8")

    # Run materialize_concepts
    eng.materialize_concepts(tmp_proj)

    concept_file = Path(tmp_proj) / OEM_DIR / "wiki" / "concept_flaky.md"
    assert concept_file.exists()
    content = concept_file.read_text(encoding="utf-8")
    assert "status: needs_review" in content
    assert "requires review due to repeated session failures." in content

