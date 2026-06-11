from __future__ import annotations

import json
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.models import SkillCandidate, SkillPromotionEvent
from oem_knowledge.source_classifier import SourceType, classify_source, is_ingestion_eligible


@pytest.fixture
def temp_project(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    eng.init_project(str(tmp_path))
    return eng, tmp_path


def test_create_skill_candidate_markdown(temp_project):
    engine, tmp_path = temp_project
    
    candidate = engine.skills.create_skill_candidate(
        candidate_id="skill_candidate_001",
        slug="critical-fix-characterization-workflow",
        title="Critical Fix Characterization Workflow",
        trigger="When fixing audit-critical production bugs.",
        recommended_behavior="Start with characterization tests before implementation.",
        evidence=[
            "CRIT-03A concept ID collision tests",
            "CRIT-04A import-time environment mutation tests",
            "CRIT-05A VectorStore lifecycle tests"
        ],
        rationale="This repeated workflow reduced implementation risk and kept the full suite green.",
        confidence="high",
        status="proposed",
        project=str(tmp_path),
    )
    
    layout = engine.layout(str(tmp_path))
    file_path = layout.skill_candidates_dir / "critical-fix-characterization-workflow.md"
    
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8")
    
    # Assert frontmatter
    assert "generated_by: openempiric" in content
    assert "source_type: oem_skill_candidate" in content
    assert "status: proposed" in content
    assert "confidence: high" in content
    assert 'slug: "critical-fix-characterization-workflow"' in content
    
    # Assert body sections
    assert "# Critical Fix Characterization Workflow" in content
    assert "## Trigger" in content
    assert "When fixing audit-critical production bugs." in content
    assert "## Recommended behavior" in content
    assert "Start with characterization tests before implementation." in content
    assert "## Evidence" in content
    assert "- CRIT-03A concept ID collision tests" in content
    assert "- CRIT-04A import-time environment mutation tests" in content
    assert "- CRIT-05A VectorStore lifecycle tests" in content
    assert "## Why this should become a skill" in content
    assert "This repeated workflow reduced implementation risk and kept the full suite green." in content
    assert "## Status" in content
    assert "Proposed" in content


def test_skill_candidate_contains_generated_by_metadata(temp_project):
    engine, tmp_path = temp_project
    
    candidate = engine.skills.create_skill_candidate(
        candidate_id="sc_002",
        slug="another-workflow",
        title="Another Workflow",
        trigger="some trigger",
        recommended_behavior="some behavior",
        evidence=["some evidence"],
        rationale="some rationale",
        project=str(tmp_path),
    )
    
    loaded = engine.skills.load_skill_candidate("another-workflow", str(tmp_path))
    assert loaded is not None
    assert loaded.candidate_id == "sc_002"
    assert loaded.status == "proposed"


def test_skill_candidate_is_classified_as_oem_generated():
    c_candidates = classify_source(".oem/skill_candidates/test-skill.md")
    assert c_candidates.source_type == SourceType.OEM_SKILL_CANDIDATE
    assert c_candidates.ingestion_eligible is False
    
    c_skills = classify_source(".oem/skills/test-skill.md")
    assert c_skills.source_type == SourceType.OEM_SKILL
    assert c_skills.ingestion_eligible is False
    
    c_promo = classify_source(".oem/skill_promotions.jsonl")
    assert c_promo.source_type == SourceType.OEM_CONFIG
    assert c_promo.ingestion_eligible is False


def test_skill_candidate_is_searchable_but_not_ingestion_eligible(temp_project):
    engine, tmp_path = temp_project
    
    # Create candidate
    engine.skills.create_skill_candidate(
        candidate_id="skill_003",
        slug="searchable-candidate",
        title="Searchable Candidate Workflow",
        trigger="when searching",
        recommended_behavior="use index search",
        evidence=["test indexing evidence"],
        rationale="because we want it indexed",
        project=str(tmp_path),
    )
    
    # Run search indexing
    index_res = engine.search.index_all(force=True)
    assert index_res["status"] == "success"
    
    # Verify searchable in database
    search_res = engine.search.search("Searchable Candidate Workflow", k=1)
    assert len(search_res) > 0
    match = search_res[0]
    
    assert match["metadata"]["source_type"] == SourceType.OEM_SKILL_CANDIDATE
    assert match["metadata"]["ingestion_eligible"] is False


def test_list_skill_candidates(temp_project):
    engine, tmp_path = temp_project
    
    assert len(engine.skills.list_skill_candidates(str(tmp_path))) == 0
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_1",
        slug="candidate-1",
        title="Workflow One",
        trigger="trigger 1",
        recommended_behavior="behavior 1",
        evidence=[],
        rationale="rationale 1",
        project=str(tmp_path),
    )
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_2",
        slug="candidate-2",
        title="Workflow Two",
        trigger="trigger 2",
        recommended_behavior="behavior 2",
        evidence=[],
        rationale="rationale 2",
        project=str(tmp_path),
    )
    
    candidates = engine.skills.list_skill_candidates(str(tmp_path))
    assert len(candidates) == 2
    slugs = {c.slug for c in candidates}
    assert slugs == {"candidate-1", "candidate-2"}


def test_update_skill_candidate_status(temp_project):
    engine, tmp_path = temp_project
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_status",
        slug="status-workflow",
        title="Status Workflow",
        trigger="test trigger",
        recommended_behavior="test behavior",
        evidence=[],
        rationale="test rationale",
        project=str(tmp_path),
    )
    
    # Approve
    updated = engine.skills.update_skill_candidate_status("status-workflow", "approved", str(tmp_path))
    assert updated.status == "approved"
    
    # Check that skills/ directory has the approved file
    layout = engine.layout(str(tmp_path))
    approved_file = layout.skills_dir / "status-workflow.md"
    assert approved_file.exists()
    approved_content = approved_file.read_text(encoding="utf-8")
    assert "source_type: oem_skill" in approved_content
    assert "status: approved" in approved_content

    # Demote/change status
    demoted = engine.skills.update_skill_candidate_status("status-workflow", "deferred", str(tmp_path))
    assert demoted.status == "deferred"
    
    # Verify no destructive deletion in v0.98A:
    # the file in skills/ must still exist but with deferred status
    assert approved_file.exists()
    deferred_content = approved_file.read_text(encoding="utf-8")
    assert "status: deferred" in deferred_content


def test_record_skill_promotion_event_jsonl(temp_project):
    engine, tmp_path = temp_project
    
    layout = engine.layout(str(tmp_path))
    promotions_file = layout.skill_promotions_path
    assert not promotions_file.exists()
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_promo",
        slug="promo-workflow",
        title="Promo Workflow",
        trigger="test",
        recommended_behavior="test",
        evidence=[],
        rationale="test",
        project=str(tmp_path),
    )
    
    assert promotions_file.exists()
    lines = promotions_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    event_data = json.loads(lines[0])
    assert event_data["slug"] == "promo-workflow"
    assert event_data["event_type"] == "proposed"


def test_skill_candidate_storage_does_not_mutate_agents_md(temp_project):
    engine, tmp_path = temp_project
    
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Agent Guidelines\n", encoding="utf-8")
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_no_mutate",
        slug="no-mutate-workflow",
        title="No Mutate Workflow",
        trigger="test",
        recommended_behavior="test",
        evidence=[],
        rationale="test",
        project=str(tmp_path),
    )
    
    engine.skills.update_skill_candidate_status("no-mutate-workflow", "approved", str(tmp_path))
    
    assert agents_md.read_text(encoding="utf-8") == "# Agent Guidelines\n"


def test_evaluate_skill_candidates_creates_candidate_from_repeated_success(temp_project):
    engine, tmp_path = temp_project
    
    # 1. Setup concept registry
    engine.state._save_registry({
        "concept_critical_fix": {
            "canonical_name": "critical-fix-characterization",
            "aliases": [],
            "status": "validated",
            "confidence": 3,
        }
    }, str(tmp_path))
    
    # 2. Setup outcomes
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix-characterization"],
        "referenced_concepts": ["critical-fix-characterization"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    # 3. Setup events (at least 2 matching trigger/behavior heuristic)
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_001",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix-characterization"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_002",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix-characterization"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    # Evaluate
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    
    assert res["status"] == "success"
    assert res["candidates_created"] == 1
    assert len(res["candidates"]) == 1
    
    cand = res["candidates"][0]
    assert cand["slug"] == "critical-fix-characterization-workflow"
    assert cand["title"] == "Critical Fix Characterization Workflow"
    assert cand["confidence"] == "medium"
    assert cand["status"] == "proposed"
    
    # Verify markdown file exists
    layout = engine.layout(str(tmp_path))
    cand_file = layout.skill_candidates_dir / "critical-fix-characterization-workflow.md"
    assert cand_file.exists()
    
    loaded = engine.skills.load_skill_candidate("critical-fix-characterization-workflow", str(tmp_path))
    assert loaded is not None
    assert loaded.trigger == "When fixing production bugs"
    assert loaded.recommended_behavior == "Start with characterization tests."
    assert loaded.source_event_ids == ["ev_001", "ev_002"]
    assert loaded.source_concept_ids == ["concept_critical_fix"]


def test_evaluate_skill_candidates_requires_evidence(temp_project):
    engine, tmp_path = temp_project
    
    # Setup concept registry
    engine.state._save_registry({
        "concept_critical_fix": {
            "canonical_name": "critical-fix",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    # 1 outcome but no events (no evidence)
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    assert res["candidates_created"] == 0


def test_evaluate_skill_candidates_requires_trigger(temp_project):
    engine, tmp_path = temp_project
    
    engine.state._save_registry({
        "concept_no_trigger": {
            "canonical_name": "no-trigger-concept",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_1",
        "outcome": "success",
        "retrieved_concepts": ["no-trigger-concept"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    # Events have no "When/If/During" trigger keywords
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_1",
        event_type="decision",
        concept_candidates=["no-trigger-concept"],
        summary="Some behavior that is recommended.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_1",
        event_type="decision",
        concept_candidates=["no-trigger-concept"],
        summary="Another behavior that is recommended.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    assert res["candidates_created"] == 0


def test_evaluate_skill_candidates_skips_vague_pattern_without_behavior(temp_project):
    engine, tmp_path = temp_project
    
    engine.state._save_registry({
        "concept_no_behavior": {
            "canonical_name": "no-behavior-concept",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_1",
        "outcome": "success",
        "retrieved_concepts": ["no-behavior-concept"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    # Events have trigger but no clear behavior keywords (should/must/always/start with)
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_1",
        event_type="decision",
        concept_candidates=["no-behavior-concept"],
        summary="When fixing bugs, we did some testing.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_1",
        event_type="decision",
        concept_candidates=["no-behavior-concept"],
        summary="When fixing bugs, testing occurred.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    assert res["candidates_created"] == 0


def test_evaluate_skill_candidates_does_not_duplicate_existing_candidate(temp_project):
    engine, tmp_path = temp_project
    
    # Create an existing proposed candidate
    engine.skills.create_skill_candidate(
        candidate_id="sc_existing",
        slug="critical-fix-characterization-workflow",
        title="Critical Fix Characterization Workflow",
        trigger="When fixing production bugs",
        recommended_behavior="Start with characterization tests.",
        evidence=["old evidence"],
        rationale="because",
        source_event_ids=["ev_old"],
        project=str(tmp_path),
    )
    
    engine.state._save_registry({
        "concept_critical_fix": {
            "canonical_name": "critical-fix-characterization",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix-characterization"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_001",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix-characterization"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_002",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix-characterization"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    
    # Duplicate detected, skipped creating a new one (but updates evidence on the proposed one)
    assert res["candidates_created"] == 0
    
    loaded = engine.skills.load_skill_candidate("critical-fix-characterization-workflow", str(tmp_path))
    assert loaded is not None
    assert "old evidence" in loaded.evidence
    assert "When fixing production bugs, start with characterization tests." in loaded.evidence
    assert set(loaded.source_event_ids) == {"ev_old", "ev_001", "ev_002"}


def test_evaluate_skill_candidates_does_not_approve_automatically(temp_project):
    engine, tmp_path = temp_project
    
    engine.state._save_registry({
        "concept_cf": {
            "canonical_name": "critical-fix",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    assert res["candidates_created"] == 1
    
    loaded = engine.skills.load_skill_candidate(res["candidates"][0]["slug"], str(tmp_path))
    assert loaded.status == "proposed"  # must NOT be approved automatically


def test_evaluate_skill_candidates_does_not_modify_agents_md(temp_project):
    engine, tmp_path = temp_project
    
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Agents\n", encoding="utf-8")
    
    engine.state._save_registry({
        "concept_cf": {
            "canonical_name": "critical-fix",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    
    assert agents_md.read_text(encoding="utf-8") == "# Agents\n"


def test_evaluate_skill_candidates_records_promotion_event(temp_project):
    engine, tmp_path = temp_project
    
    engine.state._save_registry({
        "concept_cf": {
            "canonical_name": "critical-fix",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    
    layout = engine.layout(str(tmp_path))
    assert layout.skill_promotions_path.exists()
    lines = layout.skill_promotions_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    evt = json.loads(lines[0])
    assert evt["slug"] == res["candidates"][0]["slug"]
    assert evt["event_type"] == "proposed"


def test_rejected_candidate_is_not_immediately_recreated(temp_project):
    engine, tmp_path = temp_project
    
    engine.state._save_registry({
        "concept_cf": {
            "canonical_name": "critical-fix",
            "aliases": [],
            "status": "validated",
        }
    }, str(tmp_path))
    
    outcomes_file = engine.layout(str(tmp_path)).root / "state" / "outcomes.jsonl"
    outcomes_file.parent.mkdir(parents=True, exist_ok=True)
    outcomes_file.write_text(json.dumps({
        "session_id": "session_success_1",
        "outcome": "success",
        "retrieved_concepts": ["critical-fix"],
        "timestamp": "2026-06-11T00:00:00Z"
    }) + "\n", encoding="utf-8")
    
    from oem_knowledge.models import KnowledgeEvent
    ev1 = KnowledgeEvent(
        event_id="ev_1",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-03A",
        confidence=4,
        source="chat"
    )
    ev2 = KnowledgeEvent(
        event_id="ev_2",
        timestamp="2026-06-11T00:00:00Z",
        project="test",
        session_id="session_success_1",
        event_type="decision",
        concept_candidates=["critical-fix"],
        summary="When fixing production bugs, start with characterization tests.",
        evidence="CRIT-04A",
        confidence=4,
        source="chat"
    )
    engine.state._append_event(ev1, str(tmp_path))
    engine.state._append_event(ev2, str(tmp_path))
    
    # First evaluate: creates candidate
    res = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    slug = res["candidates"][0]["slug"]
    
    # Reject it
    engine.skills.update_skill_candidate_status(slug, "rejected", str(tmp_path))
    
    # Evaluate again: must not recreate it
    res2 = engine.skill_promotion.evaluate_skill_candidates(str(tmp_path))
    assert res2["candidates_created"] == 0

