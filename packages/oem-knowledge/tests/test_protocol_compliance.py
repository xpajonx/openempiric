"""Protocol compliance tests - verify implementations satisfy their contracts.
Phase 0 of the OEM layered architecture rebuild.
"""

import pytest
from oem_knowledge.storage import (
    EventStoreProtocol,
    RegistryStoreProtocol,
    ConceptFilesProtocol,
)


@pytest.fixture
def engine(tmp_path):
    """Create a fresh engine with initialized project."""
    from oem_knowledge.engine import KnowledgeEngine
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    eng = KnowledgeEngine(project_dir)
    eng.init_project(str(project_dir))
    return eng


class TestStorageProtocolCompliance:

    def test_state_is_event_store(self, engine):
        assert isinstance(engine.state, EventStoreProtocol)

    def test_state_is_registry_store(self, engine):
        assert isinstance(engine.state, RegistryStoreProtocol)

    def test_materialization_is_concept_files(self, engine):
        assert isinstance(engine.materialization, ConceptFilesProtocol)


class TestComputationProtocolCompliance:

    def test_state_is_snapshot(self, engine):
        from oem_knowledge.computation import SnapshotProtocol
        assert isinstance(engine.state, SnapshotProtocol)

    def test_reflection_is_reflection_proto(self, engine):
        from oem_knowledge.computation import ReflectionProtocol
        assert isinstance(engine.reflection, ReflectionProtocol)

    def test_search_is_indexing(self, engine):
        from oem_knowledge.computation import IndexingProtocol
        assert isinstance(engine.search, IndexingProtocol)

    def test_search_is_search(self, engine):
        from oem_knowledge.computation import SearchProtocol
        assert isinstance(engine.search, SearchProtocol)
