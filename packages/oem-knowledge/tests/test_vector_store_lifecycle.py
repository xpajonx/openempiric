import pytest
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
from oem_knowledge.vector_store import VectorStore
from oem_knowledge.services.search import SearchService
from oem_knowledge.engine import KnowledgeEngine

# ========== Helpers ==========

def make_vector_store(tmp_path: Path) -> VectorStore:
    """Create a VectorStore with a temp database."""
    db_path = tmp_path / "test_vectors.db"
    return VectorStore(db_path)

def assert_store_closed(store: VectorStore):
    """Assert that operations on a closed store raise an error."""
    # Any public method should fail after close
    # Current implementation raises sqlite3.ProgrammingError, not RuntimeError
    with pytest.raises((RuntimeError, sqlite3.ProgrammingError)):
        store.upsert("id", "doc", {}, None)
    with pytest.raises((RuntimeError, sqlite3.ProgrammingError)):
        store.all_chunks()
    with pytest.raises((RuntimeError, sqlite3.ProgrammingError)):
        store.count()

# ========== Test 1: VectorStore close disallows future use ==========

def test_vector_store_close_disallows_future_use(tmp_path):
    """After close(), all public operations should raise an error."""
    store = make_vector_store(tmp_path)
    store.upsert("id1", "doc", {"meta": "data"}, [0.1, 0.2])
    store.close()
    
    assert_store_closed(store)
    
    # Verify the actual error type is sqlite3.ProgrammingError (current implementation)
    with pytest.raises(sqlite3.ProgrammingError):
        store.upsert("id2", "doc2", {}, None)

# ========== Test 2: VectorStore context manager ==========

@pytest.mark.xfail(
    reason="CRIT-05: VectorStore does not implement context manager protocol (__enter__/__exit__)",
    strict=True,
)
def test_vector_store_context_manager_closes_connection(tmp_path):
    """VectorStore should be usable as a context manager."""
    with make_vector_store(tmp_path) as store:
        store.upsert("id1", "doc", {"meta": "data"}, [0.1, 0.2])
        chunks = store.all_chunks()
        assert len(chunks) == 1
    
    # After context exit, store should be closed
    assert_store_closed(store)

# ========== Test 3: Idempotent close ==========

@pytest.mark.xfail(
    reason="CRIT-05: VectorStore.close() is not idempotent (sqlite3 raises on double-close)",
    strict=False,
)
def test_vector_store_close_is_idempotent(tmp_path):
    """Calling close() multiple times should not crash."""
    store = make_vector_store(tmp_path)
    store.close()
    store.close()  # Should not raise
    
    # Verify that subsequent operations also fail gracefully
    with pytest.raises((RuntimeError, sqlite3.ProgrammingError)):
        store.upsert("id", "doc", {}, None)
    
    # Note: Current implementation appears to be idempotent already
    # This test may XPASS if sqlite3.close() is idempotent

# ========== Test 4: SearchService.close() closes cached VectorStore ==========

@pytest.mark.xfail(
    reason="CRIT-05: SearchService has no close() method to release its cached VectorStore",
    strict=True,
)
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

@pytest.mark.xfail(
    reason="CRIT-05: KnowledgeEngine has no close() method to release owned resources",
    strict=True,
)
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

# ========== Test 8: Server MCP handler leaks (documentation) ==========

@pytest.mark.xfail(
    reason="CRIT-05C: MCP/server handlers create KnowledgeEngine per tool call without closing owned VectorStore resources",
    strict=True,
)
def test_server_mcp_handler_closes_engine_resources(tmp_path):
    """MCP tool handlers should close KnowledgeEngine after each tool call."""
    # This documents the current leak in server.py:
    # mount_tools() creates engine = KnowledgeEngine() at module level
    # Each @mcp.tool() creates new KnowledgeEngine(project or None) without close()
    # 
    # CRIT-05C will fix by:
    # - Adding engine.close() call in each tool handler
    # - Or using a shared engine with explicit lifecycle management
    
    # For now, characterize by verifying the leak pattern exists
    from oem_knowledge.server import knowledge_index, knowledge_search
    
    # These create engines internally but don't close them
    # (Can't easily test without running full MCP, so this is documentation)
    pass