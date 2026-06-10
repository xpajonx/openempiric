import pytest
from pathlib import Path
from unittest.mock import MagicMock
from oem_knowledge.vector_store import VectorStore, VectorStoreClosedError
from oem_knowledge.services.search import SearchService
from oem_knowledge.engine import KnowledgeEngine

# ========== Helpers ==========

def make_vector_store(tmp_path: Path) -> VectorStore:
    """Create a VectorStore with a temp database."""
    db_path = tmp_path / "test_vectors.db"
    return VectorStore(db_path)

def assert_store_closed(store: VectorStore):
    """Assert that operations on a closed store raise VectorStoreClosedError."""
    with pytest.raises(VectorStoreClosedError):
        store.upsert("id", "doc", {}, None)
    with pytest.raises(VectorStoreClosedError):
        store.all_chunks()
    with pytest.raises(VectorStoreClosedError):
        store.count()

# ========== Test 1: VectorStore close disallows future use ==========

def test_vector_store_close_disallows_future_use(tmp_path):
    """After close(), all public operations should raise VectorStoreClosedError."""
    store = make_vector_store(tmp_path)
    store.upsert("id1", "doc", {"meta": "data"}, [0.1, 0.2])
    store.close()
    
    assert_store_closed(store)
    
    # Verify the error type is VectorStoreClosedError, not sqlite3.ProgrammingError
    with pytest.raises(VectorStoreClosedError):
        store.upsert("id2", "doc2", {}, None)

# ========== Test 2: VectorStore context manager ==========

def test_vector_store_context_manager_closes_connection(tmp_path):
    """VectorStore should be usable as a context manager."""
    with make_vector_store(tmp_path) as store:
        store.upsert("id1", "doc", {"meta": "data"}, [0.1, 0.2])
        chunks = store.all_chunks()
        assert len(chunks) == 1
    
    # After context exit, store should be closed
    assert_store_closed(store)

# ========== Test 3: Idempotent close ==========

def test_vector_store_close_is_idempotent(tmp_path):
    """Calling close() multiple times should not crash."""
    store = make_vector_store(tmp_path)
    store.close()
    store.close()  # Should not raise
    
    # Verify that subsequent operations fail with clear error
    with pytest.raises(VectorStoreClosedError, match="VectorStore is closed"):
        store.upsert("id", "doc", {}, None)

# ========== Test 4: SearchService.close() closes cached VectorStore ==========

def test_search_service_close_closes_cached_vector_store(tmp_path):
    """SearchService.close() should close its cached VectorStore."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    
    # Access vector_store to trigger creation
    store = engine.search.vector_store
    store.upsert("id1", "doc", {"source": "test"}, [0.1, 0.2])
    
    # Track close calls
    close_called = []
    original_close = store.close
    def tracking_close():
        close_called.append(True)
        original_close()
    store.close = tracking_close
    
    # Close via SearchService (to be implemented in CRIT-05B)
    engine.search.close()
    
    assert close_called
    assert_store_closed(store)

# ========== Test 5: Engine.close() closes owned VectorStore ==========

def test_engine_close_closes_owned_vector_store(tmp_path):
    """KnowledgeEngine.close() should delegate to SearchService.close()."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    
    store = engine.search.vector_store
    store.upsert("id1", "doc", {"source": "test"}, [0.1, 0.2])
    
    # Track close
    close_called = []
    original_close = store.close
    def tracking_close():
        close_called.append(True)
        original_close()
    store.close = tracking_close
    
    engine.close()  # To be implemented in CRIT-05B
    
    assert close_called
    assert_store_closed(store)

# ========== Test 6: Repeated indexing smoke test ==========

def test_repeated_indexing_no_connection_leak(tmp_path):
    """Repeated index_all() calls should not cause sqlite lock errors or resource exhaustion."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    
    # Create some test files
    wiki_dir = engine._concepts_dir(str(tmp_path))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (wiki_dir / f"concept{i}.md").write_text(f"# Concept {i}\n\nBody text")
    
    for _ in range(10):
        stats = engine.search.index_all(force=True)
        assert stats["status"] in ("success", "partial")  # No lock errors
    
    # DB should still be usable
    chunks = engine.search.vector_store.all_chunks()
    assert len(chunks) > 0

# ========== Test 7: Close releases SQLite lock ==========

def test_close_releases_sqlite_lock(tmp_path):
    """Closing a store should release database resources for another store to write."""
    db_path = tmp_path / "shared.db"
    
    # First store: write and close
    store1 = VectorStore(db_path)
    store1.upsert("id1", "doc1", {"source": "test"}, [0.1])
    store1.close()
    
    # Second store: should be able to write to same DB
    store2 = VectorStore(db_path)
    store2.upsert("id2", "doc2", {"source": "test"}, [0.2])
    chunks = store2.all_chunks()
    
    assert len(chunks) == 2
    store2.close()

# ========== Test 8: Server MCP handler closes engine resources ==========

def test_server_mcp_handler_closes_engine_resources(tmp_path, monkeypatch):
    """MCP tool handlers should close KnowledgeEngine after each tool call."""
    from fastmcp import FastMCP
    import oem_knowledge.server as server_module

    closed = []

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            self.search = MagicMock()
            self.search.search.return_value = []
            self.search.stats.return_value = {"total_chunks": 0, "db_size_mb": 0, "harness_path": ""}
            self.state = MagicMock()
            self.state.consolidate.return_value = {"message": "", "merged": []}
            self.state._load_registry.return_value = {}
            self.state.get_events.return_value = []
            self.state.get_event.side_effect = KeyError("not found")
            self.state.merge_concepts.return_value = {"status": "error", "message": ""}
            self.state.detect_stale_concepts.return_value = []
            self.materialization = MagicMock()
            self.materialization.materialize_concepts.return_value = {"message": "", "materialized": []}
            self.materialization.update_graph.return_value = {"message": "", "links_updated": 0}
            self.reflection = MagicMock()
            self.reflection.reflect_session.return_value = {"status": "success", "knowledge_events": [], "report_path": ""}
            self.fitness = MagicMock()
            self.fitness.calculate_fitness.return_value = {}
            self.event_migrator = MagicMock()
            self.event_migrator.get_schema_status.return_value = {"status": "up_to_date", "message": ""}
            self._resolve_harness = MagicMock(return_value=tmp_path / ".oem")
            self.init_project = MagicMock(return_value={"status": "success"})

        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            closed.append(True)
        def propose_merges(self, *args, **kwargs):
            return []
        def detect_contradictions(self, *args, **kwargs):
            return []
        def embedding_cache_ready(self):
            return True
        def _ensure_open(self):
            pass

    monkeypatch.setattr(server_module, "KnowledgeEngine", FakeEngine)

    registered_tools = {}

    class MockMCP(FastMCP):
        def __init__(self):
            super().__init__("test")
        def tool(self, name=None):
            def decorator(f):
                registered_tools[name or f.__name__] = f
                return f
            return decorator

    server_module.mount_tools(MockMCP())

    # Test success path - knowledge_search
    closed.clear()
    result = registered_tools['knowledge_search'](query="test", project="")
    assert len(closed) == 1, "knowledge_search should close its engine on success"
    assert "error" not in result.lower()

    # Test success path - knowledge_consolidate
    closed.clear()
    result = registered_tools['knowledge_consolidate'](project="")
    assert len(closed) == 1, "knowledge_consolidate should close its engine on success"
    assert "error" not in result.lower()

    # Test success path - knowledge_get_events
    closed.clear()
    result = registered_tools['knowledge_get_events'](project="")
    assert len(closed) == 1, "knowledge_get_events should close its engine on success"
    assert "error" not in result.lower()

    # Test error path - set up a fake that raises
    failing_closed = []

    class FailingFakeEngine(FakeEngine):
        def close(self):
            failing_closed.append(True)
        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(server_module, "KnowledgeEngine", FailingFakeEngine)

    # Re-register tools with failing engine
    registered_tools2 = {}
    class MockMCP2(FastMCP):
        def __init__(self):
            super().__init__("test2")
        def tool(self, name=None):
            def decorator(f):
                registered_tools2[name or f.__name__] = f
                return f
            return decorator
    server_module.mount_tools(MockMCP2())

    failing_closed.clear()
    result = registered_tools2['knowledge_get_event'](project="", event_id="nonexistent")
    assert len(failing_closed) >= 1, "knowledge_get_event should close its engine even on KeyError"
    # The tool catches KeyError and returns a "not found" panel, not an unhandled exception
    assert "not found" in result.lower() or "error" in result.lower()


# ========== Test 9: SearchService.close() is idempotent ==========

def test_search_service_close_is_idempotent(tmp_path):
    """Calling SearchService.close() multiple times should not crash."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    store = engine.search.vector_store
    store.upsert("id1", "doc", {"source": "test"}, [0.1])

    engine.search.close()
    engine.search.close()  # Second call should be a no-op
    assert engine.search._vector_store is None


# ========== Test 10: SearchService lazily recreates store after close ==========

def test_search_service_reuses_after_close(tmp_path):
    """SearchService should lazily create a fresh VectorStore after close()."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")

    store1 = engine.search.vector_store
    store1.upsert("id1", "doc1", {"source": "test"}, [0.1])

    engine.search.close()
    assert engine.search._vector_store is None

    # Accessing vector_store again should create a new one
    store2 = engine.search.vector_store
    assert store2 is not None
    assert store2 is not store1
    assert engine.search._vector_store is store2
    store2.upsert("id2", "doc2", {"source": "test"}, [0.2])
    chunks = store2.all_chunks()
    assert len(chunks) == 2  # Both old and new chunks should be readable


# ========== Test 11: KnowledgeEngine context manager ==========

def test_knowledge_engine_context_manager(tmp_path):
    """KnowledgeEngine should be usable as a context manager."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    store = engine.search.vector_store
    store.upsert("id1", "doc", {"source": "test"}, [0.1])

    close_called = []
    original_close = store.close
    def tracking_close():
        close_called.append(True)
        original_close()
    store.close = tracking_close

    with engine:
        pass

    assert close_called, "Engine.__exit__ should call SearchService.close()"