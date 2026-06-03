import pytest
from oem_knowledge.engine import KnowledgeEngine

def test_engine_delegation(tmp_path):
    # Initialize a KnowledgeEngine pointing to a temp directory
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_delegation_proj")

    # Verify that the service instances are created
    assert engine.search_service is not None
    assert engine.materialization_service is not None
    assert engine.reflection_service is not None
    assert engine.state_service is not None

    # Verify delegation works for SearchService
    # Verify that engine.stats calls search_service.stats
    stats = engine.stats()
    assert "total_chunks" in stats
    assert "db_size_mb" in stats

    # Verify delegation works for StateService
    # Verify evaluate_concept_status
    cdata = {"concept_id": "concept_001", "canonical_name": "test-concept"}
    updated_cdata = engine.evaluate_concept_status(cdata, "observation", "session_1")
    assert updated_cdata["status"] == "candidate"

    # Verify explain_concept delegation
    explanation = engine.explain_concept(concept_id="concept_001")
    # Should say not found or return error status because we haven't registered it yet
    assert explanation["status"] == "error"
