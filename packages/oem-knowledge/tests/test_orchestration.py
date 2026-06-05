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


def test_embed_returns_python_floats(tmp_path):
    numpy = pytest.importorskip("numpy")
    from unittest.mock import PropertyMock, patch

    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_float_conversion")

    svc = engine.search_service

    fake_embeddings = [
        numpy.array([numpy.float32(0.1), numpy.float32(-0.2), numpy.float32(0.3)]),
        numpy.array([numpy.float32(0.4), numpy.float32(-0.5), numpy.float32(0.6)]),
    ]

    with patch(
        "oem_knowledge.engine.KnowledgeEngine.model",
        new_callable=PropertyMock,
    ) as mock_model_prop:
        mock_model_prop.return_value.embed.return_value = fake_embeddings
        result = svc.embed(["text1", "text2"])

    assert len(result) == 2
    assert all(isinstance(v, float) for emb in result for v in emb), (
        f"expected all float, got element type {type(result[0][0])}"
    )
    assert result[0] == pytest.approx([0.1, -0.2, 0.3])
    assert result[1] == pytest.approx([0.4, -0.5, 0.6])
