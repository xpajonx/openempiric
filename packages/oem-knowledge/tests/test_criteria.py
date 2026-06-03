import pytest
import shutil
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)


def test_concept_resolution_and_merging(temp_project):
    engine = KnowledgeEngine(temp_project)

    # 1. Create a concept
    engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: AI safety is important.",
        session_id="session_1",
    )
    engine.materialize_concepts(str(temp_project))

    registry = engine._load_registry(str(temp_project))
    # Find the concept ID for 'ai-safety-is-important'
    cid = None
    for k, v in registry.items():
        if v["canonical_name"] == "ai-safety-is-important":
            cid = k
            break
    assert cid is not None

    # 2. Fuzzy match
    engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: AI safety is very important.",
        session_id="session_2",
    )
    engine.materialize_concepts(str(temp_project))

    registry = engine._load_registry(str(temp_project))
    assert "ai safety is very important." in registry[cid]["aliases"]

    # 3. Create a distinct concept
    engine.reflect_session(
        project=str(temp_project),
        conversation_text="Hypothesis: Machine learning alignment",
        session_id="session_3",
    )
    engine.materialize_concepts(str(temp_project))

    registry = engine._load_registry(str(temp_project))
    cid2 = None
    for k, v in registry.items():
        if v["canonical_name"] == "machine-learning-alignment":
            cid2 = k
            break
    assert cid2 is not None

    # 4. Merge concepts
    merge_res = engine.merge_concepts(
        str(temp_project), primary_id=cid, secondary_id=cid2
    )
    assert merge_res["status"] == "success"

    registry = engine._load_registry(str(temp_project))
    assert cid2 not in registry
    assert "machine learning alignment" in registry[cid]["aliases"]


def test_evaluate_concept_status(temp_project):
    engine = KnowledgeEngine(temp_project)

    # Materialize needs to be called to promote concepts
    # Add multiple sessions with evidence
    for i in range(5):
        engine.reflect_session(
            project=str(temp_project),
            conversation_text="Validation: Core concept",
            session_id=f"session_test_{i}",
        )
        engine.materialize_concepts(str(temp_project))

    registry = engine._load_registry(str(temp_project))
    cid = None
    for k, v in registry.items():
        if v["canonical_name"] == "core-concept":
            cid = k
            break
    assert cid is not None
    cdata = registry[cid]

    # Status should be promoted
    assert cdata["status"] == "canonical"
    assert cdata["session_count"] >= 5
    assert cdata["confidence"] >= 4
