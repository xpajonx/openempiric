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
    assert "source_type: oem_project_skill" in approved_content
    assert "status: approved" in approved_content

    # Demote/change status
    demoted = engine.skills.update_skill_candidate_status("status-workflow", "deferred", str(tmp_path), force=True)
    assert demoted.status == "deferred"
    
    # Verify no destructive deletion in v0.98A:
    # the file in skills/ must still exist but with deferred status
    assert approved_file.exists()
    deferred_content = approved_file.read_text(encoding="utf-8")
    assert "status: superseded" in deferred_content


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


def test_skills_list_shows_candidates(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_list_1",
        slug="list-workflow",
        title="List Workflow",
        trigger="When listing",
        recommended_behavior="Should list them.",
        evidence=["Used once"],
        rationale="testing list",
        project=str(tmp_path),
    )
    candidates = engine.skills.list_skill_candidates(str(tmp_path))
    assert len(candidates) == 1
    assert candidates[0].slug == "list-workflow"
    assert candidates[0].status == "proposed"


def test_skills_show_displays_evidence(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_show_1",
        slug="show-workflow",
        title="Show Workflow",
        trigger="When showing",
        recommended_behavior="Should show them.",
        evidence=["Evidence A", "Evidence B"],
        rationale="testing show",
        project=str(tmp_path),
    )
    cand = engine.skills.load_skill_candidate("show-workflow", str(tmp_path))
    assert cand is not None
    assert cand.trigger == "When showing"
    assert cand.recommended_behavior == "Should show them."
    assert cand.evidence == ["Evidence A", "Evidence B"]


def test_skills_approve_writes_project_skill(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_approve_1",
        slug="approve-workflow",
        title="Approve Workflow",
        trigger="When approving",
        recommended_behavior="Should approve them.",
        evidence=["Evidence 1"],
        rationale="testing approve",
        project=str(tmp_path),
    )
    
    cand = engine.skills.update_skill_candidate_status("approve-workflow", "approved", str(tmp_path))
    assert cand.status == "approved"
    
    layout = engine.layout(str(tmp_path))
    approved_file = layout.skills_dir / "approve-workflow.md"
    assert approved_file.exists()
    
    content = approved_file.read_text(encoding="utf-8")
    assert "source_type: oem_project_skill" in content
    assert "status: approved" in content
    assert "## Skill" in content
    assert "Should approve them." in content
    assert "## Trigger" in content
    assert "When approving" in content


def test_skills_approve_records_promotion_event(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_promo_event_1",
        slug="promo-event-workflow",
        title="Promo Event Workflow",
        trigger="When promoting",
        recommended_behavior="Should promote.",
        evidence=["Evidence P"],
        rationale="testing promo",
        project=str(tmp_path),
    )
    
    engine.skills.update_skill_candidate_status("promo-event-workflow", "approved", str(tmp_path))
    layout = engine.layout(str(tmp_path))
    promotions_file = layout.skill_promotions_path
    
    assert promotions_file.exists()
    lines = promotions_file.read_text(encoding="utf-8").strip().splitlines()
    # At least two events (proposed, then approved)
    events = [json.loads(line) for line in lines]
    assert any(e["slug"] == "promo-event-workflow" and e["new_status"] == "approved" for e in events)


def test_skills_reject_updates_status_without_deleting_evidence(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_reject_1",
        slug="reject-workflow",
        title="Reject Workflow",
        trigger="When rejecting",
        recommended_behavior="Should reject.",
        evidence=["Evidence R"],
        rationale="testing reject",
        project=str(tmp_path),
    )
    
    cand = engine.skills.update_skill_candidate_status("reject-workflow", "rejected", str(tmp_path))
    assert cand.status == "rejected"
    assert cand.evidence == ["Evidence R"]
    
    loaded = engine.skills.load_skill_candidate("reject-workflow", str(tmp_path))
    assert loaded.status == "rejected"
    assert loaded.evidence == ["Evidence R"]


def test_skills_defer_updates_status(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_defer_1",
        slug="defer-workflow",
        title="Defer Workflow",
        trigger="When deferring",
        recommended_behavior="Should defer.",
        evidence=["Evidence D"],
        rationale="testing defer",
        project=str(tmp_path),
    )
    
    cand = engine.skills.update_skill_candidate_status("defer-workflow", "deferred", str(tmp_path))
    assert cand.status == "deferred"
    
    loaded = engine.skills.load_skill_candidate("defer-workflow", str(tmp_path))
    assert loaded.status == "deferred"


def test_skills_approve_does_not_modify_agents_md(temp_project):
    engine, tmp_path = temp_project
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("# Agent workflow\n", encoding="utf-8")
    
    engine.skills.create_skill_candidate(
        candidate_id="sc_agents_1",
        slug="agents-workflow",
        title="Agents Workflow",
        trigger="When testing agents",
        recommended_behavior="Should not touch AGENTS.md.",
        evidence=["Evidence A"],
        rationale="testing agents",
        project=str(tmp_path),
    )
    
    engine.skills.update_skill_candidate_status("agents-workflow", "approved", str(tmp_path))
    assert agents_md.read_text(encoding="utf-8") == "# Agent workflow\n"


def test_approved_skill_is_searchable_but_not_ingestion_eligible(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_searchable_1",
        slug="searchable-workflow",
        title="Searchable Workflow",
        trigger="When searching searchable",
        recommended_behavior="Should search.",
        evidence=["Evidence S"],
        rationale="testing searchable",
        project=str(tmp_path),
    )
    
    engine.skills.update_skill_candidate_status("searchable-workflow", "approved", str(tmp_path))
    
    # Run search indexing
    index_res = engine.search.index_all(force=True)
    assert index_res["status"] == "success"
    
    # Verify searchable in database
    search_res = engine.search.search("Searchable Workflow", k=1)
    assert len(search_res) > 0
    match = search_res[0]
    
    assert match["metadata"]["source_type"] == "oem_skill"
    assert is_ingestion_eligible(engine.layout(str(tmp_path)).skills_dir / "searchable-workflow.md") is False


def test_mcp_skill_candidates_returns_clean_markdown(temp_project):
    from fastmcp import FastMCP
    from oem_knowledge.server import mount_tools
    mcp = FastMCP("test_mcp")
    mount_tools(mcp)
    
    engine, tmp_path = temp_project
    # Create candidate
    engine.skills.create_skill_candidate(
        candidate_id="sc_mcp_1",
        slug="mcp-workflow",
        title="Mcp Workflow",
        trigger="When using MCP",
        recommended_behavior="Should work.",
        evidence=["Evidence M"],
        rationale="testing mcp",
        project=str(tmp_path),
    )
    
    import asyncio
    res = asyncio.run(mcp.call_tool("knowledge_skill_candidates", {"project": str(tmp_path)}))
    res_str = res.content[0].text
    assert "mcp-workflow" in res_str
    assert "Mcp Workflow" in res_str
    assert "proposed" in res_str


def test_agent_notification_mentions_high_confidence_candidate(temp_project):
    engine, tmp_path = temp_project
    
    # Create high confidence candidate (evidence >= 3)
    engine.skills.create_skill_candidate(
        candidate_id="sc_high_1",
        slug="high-conf-workflow",
        title="High Conf Workflow",
        trigger="When doing high confidence",
        recommended_behavior="Should behave well.",
        evidence=["Evidence 1", "Evidence 2", "Evidence 3"],
        rationale="testing high confidence",
        project=str(tmp_path),
    )
    
    # Mock reflect_session to return success
    def mock_reflect(*args, **kwargs):
        return {"status": "success", "knowledge_events": [], "report_path": str(tmp_path / "report.md")}
    
    from unittest.mock import patch
    with patch("oem_knowledge.services.reflection.ReflectionService.reflect_session", new=mock_reflect):
        res = engine.session_commit(project=str(tmp_path), conversation_text="test", update_index=False)
        assert res.get("notification") is not None
        assert "OEM noticed a repeated successful workflow pattern" in res["notification"]
        assert "High Conf Workflow" in res["notification"]
        assert "oem skills approve high-conf-workflow" in res["notification"]


def test_skills_approve_rejected_requires_force(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_transition_1",
        slug="transition-workflow",
        title="Transition Workflow",
        trigger="When transitioning",
        recommended_behavior="Should transition.",
        evidence=["Evidence T"],
        rationale="testing transition",
        project=str(tmp_path),
    )
    
    # Reject it first
    engine.skills.update_skill_candidate_status("transition-workflow", "rejected", str(tmp_path))
    
    # Trying to approve without force must raise ValueError
    with pytest.raises(ValueError) as exc:
        engine.skills.update_skill_candidate_status("transition-workflow", "approved", str(tmp_path), force=False)
    assert "Cannot transition from rejected to approved" in str(exc.value)


def test_skills_approve_rejected_with_force_records_event(temp_project):
    engine, tmp_path = temp_project
    engine.skills.create_skill_candidate(
        candidate_id="sc_transition_2",
        slug="transition-workflow-2",
        title="Transition Workflow 2",
        trigger="When transitioning again",
        recommended_behavior="Should transition again.",
        evidence=["Evidence T2"],
        rationale="testing transition again",
        project=str(tmp_path),
    )
    
    engine.skills.update_skill_candidate_status("transition-workflow-2", "rejected", str(tmp_path))
    
    # Approve with force
    cand = engine.skills.update_skill_candidate_status("transition-workflow-2", "approved", str(tmp_path), force=True)
    assert cand.status == "approved"
    
    layout = engine.layout(str(tmp_path))
    promotions_file = layout.skill_promotions_path
    lines = promotions_file.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    assert any(e["slug"] == "transition-workflow-2" and e["new_status"] == "approved" for e in events)


def test_skill_notification_failure_does_not_break_session_commit(temp_project):
    engine, tmp_path = temp_project
    
    # Mock list_skill_candidates to raise an exception
    def mock_list(*args, **kwargs):
        raise RuntimeError("database crash simulation")
        
    from unittest.mock import patch
    with patch("oem_knowledge.services.skills.SkillService.list_skill_candidates", new=mock_list):
        # Mock reflect_session to return success
        def mock_reflect(*args, **kwargs):
            return {"status": "success", "knowledge_events": [], "report_path": str(tmp_path / "report.md")}
        
        with patch("oem_knowledge.services.reflection.ReflectionService.reflect_session", new=mock_reflect):
            res = engine.session_commit(project=str(tmp_path), conversation_text="test", update_index=False)
            assert res.get("status") == "success"
            assert res.get("notification") is None


def test_mcp_skill_candidate_approve_missing_slug_returns_clean_error(temp_project):
    from fastmcp import FastMCP
    from oem_knowledge.server import mount_tools
    mcp = FastMCP("test_mcp")
    mount_tools(mcp)
    
    engine, tmp_path = temp_project
    
    import asyncio
    res = asyncio.run(mcp.call_tool("knowledge_skill_candidate_approve", {"slug": "", "project": str(tmp_path)}))
    res_str = res.content[0].text
    assert "Status: error" in res_str
    assert "Slug is required" in res_str


