import pytest
from oem_knowledge.engine import KnowledgeEngine

def test_engine_delegation(tmp_path):
    # Initialize a KnowledgeEngine pointing to a temp directory
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_delegation_proj")

    # Verify that the service instances are created
    assert engine.search is not None
    assert engine.materialization is not None
    assert engine.reflection is not None
    assert engine.state is not None

    # Verify direct service calls work
    stats = engine.search.stats()
    assert "total_chunks" in stats
    assert "db_size_mb" in stats

    # Verify evaluate_concept_status
    cdata = {"concept_id": "concept_001", "canonical_name": "test-concept"}
    updated_cdata = engine.state.evaluate_concept_status(cdata, "observation", "session_1")
    assert updated_cdata["status"] == "candidate"

    # Verify explain_concept
    explanation = engine.state.explain_concept(concept_id="concept_001")
    # Should say not found or return error status because we haven't registered it yet
    assert explanation["status"] == "error"


def test_embed_returns_python_floats(tmp_path):
    numpy = pytest.importorskip("numpy")
    from unittest.mock import PropertyMock, patch

    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_float_conversion")

    svc = engine.search

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


def test_fallback_concepts_materialize(tmp_path):
    """Verify that fallback-extracted concepts materialize into the registry."""
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project(str(tmp_path))

    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Fixed doctor global install detection.\nRefactored reflection pipeline.",
        session_id="test_materialize_1",
    )
    assert res["status"] == "success"
    assert res["explainability"]["fallback_extraction_used"] is True

    mat_res = engine.materialization.materialize_concepts(str(tmp_path))
    assert "materialized" in mat_res

    registry = engine.state._load_registry(str(tmp_path))
    concept_names = [v["canonical_name"] for v in registry.values()]
    assert any("doctor" in c or "global" in c for c in concept_names), (
        f"Expected doctor/global concept in registry, got {concept_names}"
    )
    assert any("reflection" in c or "pipeline" in c for c in concept_names), (
        f"Expected reflection/pipeline concept in registry, got {concept_names}"
    )


def test_report_includes_extracted_concepts(tmp_path):
    """Verify session reports include fallback-extracted concepts."""
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project(str(tmp_path))

    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Added executable availability checks.",
        session_id="test_report_1",
    )
    report_path = res["report_path"]
    assert report_path is not None

    import json
    report_text = open(report_path).read()
    report_events = json.loads(report_text.split("```json")[1].split("```")[0])
    event_concepts = [e["concept"] for e in report_events["knowledge_events"]]
    assert any("executable" in c or "availability" in c for c in event_concepts), (
        f"Expected executable/availability in report, got {event_concepts}"
    )
