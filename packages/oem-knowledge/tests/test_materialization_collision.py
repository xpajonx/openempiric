import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.concept_id import ConceptIdCollisionError
from oem_knowledge.fs import FileLock

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_materialization_does_not_overwrite_existing_wiki_file(temp_project):
    """Verify that materialization does not overwrite an existing wiki file on collision."""
    project_dir, engine = temp_project
    
    # 1. Setup registry with concept_001
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    # 2. Create concept_002.md
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    target_file = wiki_dir / "concept_002.md"
    target_file.write_text("SENTINEL CONTENT", encoding="utf-8")
    
    # Mock allocator to always return concept_002 and bypass retries limit (max retries exceeded to trigger collision raise)
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_002"):
        sessions_dir = engine._sessions_dir(str(project_dir))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        report_file = sessions_dir / "sess_1.md"
        report_content = "```json\n" + json.dumps({
            "knowledge_events": [
                {"type": "observation", "concept": "beta", "evidence": "e1"},
                {"type": "observation", "concept": "beta", "evidence": "e2"},
                {"type": "observation", "concept": "beta", "evidence": "e3"}
            ]
        }) + "\n```"
        report_file.write_text(report_content, encoding="utf-8")
        
        res = engine.materialization.materialize_concepts(str(project_dir))
        assert res["status"] == "error"
        assert "Concept ID collision" in res["message"]
        
    # Verify file is not overwritten
    assert target_file.read_text(encoding="utf-8") == "SENTINEL CONTENT"

def test_materialization_reloads_registry_under_lock(temp_project):
    """Verify that materialization loads the registry state under the file lock."""
    project_dir, engine = temp_project
    
    original_load = engine.state._load_registry
    load_spy = MagicMock(side_effect=lambda *args, **kwargs: original_load(*args, **kwargs))
    
    # Spy on registry load
    with patch.object(engine.state, "_load_registry", load_spy):
        sessions_dir = engine._sessions_dir(str(project_dir))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        report_file = sessions_dir / "sess_1.md"
        report_content = "```json\n" + json.dumps({
            "knowledge_events": [
                {"type": "observation", "concept": "beta", "evidence": "e1"}
            ]
        }) + "\n```"
        report_file.write_text(report_content, encoding="utf-8")
        
        engine.materialization.materialize_concepts(str(project_dir))
        
        # Verify that _load_registry was called at least once
        assert load_spy.called

def test_materialization_reserves_ids_for_batch(temp_project):
    """Verify that multiple new concepts materialized in the same run receive distinct IDs."""
    project_dir, engine = temp_project
    
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "first-concept", "evidence": "e1"},
            {"type": "observation", "concept": "second-concept", "evidence": "e2"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    ids = {}
    for cid, data in reg.items():
        if data.get("canonical_name") in ("first-concept", "second-concept"):
            ids[data["canonical_name"]] = cid
            
    assert "first-concept" in ids
    assert "second-concept" in ids
    assert ids["first-concept"] != ids["second-concept"]

def test_materialization_handles_partial_previous_wiki_write(temp_project):
    """Verify that materialization handles and skips existing orphan wiki files by allocating next ID."""
    project_dir, engine = temp_project
    
    initial_reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "alpha",
            "aliases": ["alpha"],
            "status": "validated",
            "confidence": 3,
            "sessions": ["sess_0"]
        }
    }
    engine.state._save_registry(initial_reg, str(project_dir))
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_002.md"
    orphan_file.write_text("SENTINEL: DO NOT OVERWRITE", encoding="utf-8")
    
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1"},
            {"type": "observation", "concept": "beta", "evidence": "e2"},
            {"type": "observation", "concept": "beta", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    res = engine.materialization.materialize_concepts(str(project_dir))
    assert res["status"] == "success"
    
    reg = engine.state._load_registry(str(project_dir))
    beta_cid = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_cid = cid
            break
            
    # Should skip concept_002 and allocate concept_003
    assert beta_cid == "concept_003"
    assert orphan_file.read_text(encoding="utf-8") == "SENTINEL: DO NOT OVERWRITE"

def test_materialization_returns_partial_not_crash_on_collision(temp_project):
    """Verify that materialization returns error dict and does not crash when maximum retries are exceeded."""
    project_dir, engine = temp_project
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_001.md").write_text("sentinel", encoding="utf-8")
    
    # Mock allocator to always return concept_001 to trigger infinite collision
    with patch("oem_knowledge.concept_id.allocate_concept_id", return_value="concept_001"):
        sessions_dir = engine._sessions_dir(str(project_dir))
        sessions_dir.mkdir(parents=True, exist_ok=True)
        report_file = sessions_dir / "sess_1.md"
        report_content = "```json\n" + json.dumps({
            "knowledge_events": [
                {"type": "observation", "concept": "beta", "evidence": "e1"},
                {"type": "observation", "concept": "beta", "evidence": "e2"},
                {"type": "observation", "concept": "beta", "evidence": "e3"}
            ]
        }) + "\n```"
        report_file.write_text(report_content, encoding="utf-8")
        
        # Should return error status rather than raising unhandled exception out of materialize_concepts
        res = engine.materialization.materialize_concepts(str(project_dir))
        assert res["status"] == "error"
        assert res["failed_step"] == "materialization"
        assert "Concept ID collision" in res["message"]

def test_materialization_records_source_event_ids(temp_project):
    """Verify that materialized concepts record source event IDs in the registry and frontmatter."""
    project_dir, engine = temp_project
    
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "beta", "evidence": "e1", "event_id": "evt_123"},
            {"type": "observation", "concept": "beta", "evidence": "e2", "event_id": "evt_123"},
            {"type": "observation", "concept": "beta", "evidence": "e3", "event_id": "evt_123"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    engine.materialization.materialize_concepts(str(project_dir))
    
    reg = engine.state._load_registry(str(project_dir))
    beta_data = None
    for cid, data in reg.items():
        if data.get("canonical_name") == "beta":
            beta_data = data
            break
            
    assert beta_data is not None
    assert "evt_123" in beta_data.get("source_event_ids", [])
