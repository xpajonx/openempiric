import pytest
import shutil
import time
from pathlib import Path
from harness_knowledge.engine import KnowledgeEngine

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project("test_project")
    yield project_dir
    shutil.rmtree(project_dir)

def test_concept_resolution_and_merging(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # 1. Create a concept
    res1 = engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: AI safety is important.",
        session_id="session_1"
    )
    
    registry = engine._load_registry(str(temp_project))
    assert len(registry) == 1
    cid = list(registry.keys())[0]
    assert registry[cid]["canonical_name"] == "ai-safety-is-important"
    
    # 2. Fuzzy match
    res2 = engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: AI safety is really important.",
        session_id="session_2"
    )
    
    registry = engine._load_registry(str(temp_project))
    assert len(registry) == 1  # Should fuzzy match and not create new
    assert "ai safety is really important." in registry[cid]["aliases"]
    
    # 3. Create a distinct concept
    res3 = engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: Machine learning alignment",
        session_id="session_3"
    )
    
    registry = engine._load_registry(str(temp_project))
    assert len(registry) == 2
    cids = list(registry.keys())
    cids.remove(cid)
    cid2 = cids[0]
    
    # 4. Merge concepts
    merge_res = engine.merge_concepts(str(temp_project), primary_id=cid, secondary_id=cid2)
    assert merge_res["status"] == "success"
    
    registry = engine._load_registry(str(temp_project))
    assert len(registry) == 1
    assert cid2 not in registry
    assert "machine learning alignment" in registry[cid]["aliases"]
    
def test_evaluate_concept_status(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # Materialize needs to be called to promote concepts
    # Add multiple sessions with evidence
    for i in range(5):
        engine.reflect_session(
            project=str(temp_project),
            conversation_text=f"Validation: Core concept",
            session_id=f"session_test_{i}"
        )
        engine.materialize_concepts(str(temp_project))
        
    registry = engine._load_registry(str(temp_project))
    cid = list(registry.keys())[0]
    cdata = registry[cid]
    
    # Status should be promoted
    assert cdata["status"] == "canonical"
    assert cdata["session_count"] >= 5
    assert cdata["confidence"] >= 4
