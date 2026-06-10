"""
CRIT-05D: VectorStore lifecycle regression and long-running workflow verification.

Validates that repeated indexing, search, session commits, MCP requests,
and CLI operations do not leak SQLite connections, leave databases locked,
or degrade over time.
"""

import pytest
import json
import time
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch
from oem_knowledge.vector_store import VectorStore, VectorStoreClosedError
from oem_knowledge.services.search import SearchService
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.fs import FileLock


# ========== Helpers ==========

def assert_valid_jsonl(path: Path) -> None:
    assert path.exists(), f"JSONL file {path} does not exist"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)


def assert_valid_json(path: Path) -> None:
    assert path.exists(), f"JSON file {path} does not exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def make_conversation(label: str) -> str:
    return (
        f"decision: Use {label} memory path\n"
        f"validation: {label} commit completed\n"
    )


def create_project_with_wiki(tmp_path: Path) -> KnowledgeEngine:
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test")
    wiki_dir = engine._concepts_dir(str(tmp_path))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (wiki_dir / f"concept{i}.md").write_text(f"# Concept {i}\n\nBody text with [[Linked{i}]]")
    return engine


@pytest.fixture(autouse=True)
def fast_locks():
    original_init = FileLock.__init__
    def patched_init(self, lock_path, timeout=10.0, stale_timeout=300.0, poll_interval=0.1, *args, **kwargs):
        forced_timeout = 0.2 if timeout >= 1.0 else timeout
        forced_stale = 0.4 if stale_timeout >= 10.0 else stale_timeout
        forced_poll = 0.02 if poll_interval >= 0.05 else poll_interval
        original_init(self, lock_path, timeout=forced_timeout, stale_timeout=forced_stale, poll_interval=forced_poll, *args, **kwargs)
    with patch.object(FileLock, "__init__", patched_init):
        yield


# ========== Test 1: Repeated indexing ==========

def test_repeated_indexing_no_lock_errors(tmp_path):
    """15+ repeated index_all() calls should not cause sqlite lock errors."""
    engine = create_project_with_wiki(tmp_path)

    elapsed_start = time.perf_counter()
    stats = engine.search.index_all(force=True)
    assert stats["status"] in ("success", "partial")

    for i in range(25):
        force = (i % 3 == 0)
        stats = engine.search.index_all(force=force)
        assert stats["status"] in ("success", "partial"), \
            f"Iteration {i}: unexpected status {stats['status']}"

    elapsed = time.perf_counter() - elapsed_start
    print(f"[bench] 25 index operations elapsed: {elapsed:.3f}s")

    chunks = engine.search.vector_store.all_chunks()
    assert len(chunks) > 0

    engine.search.vector_store.upsert("verify-id", "verify doc", {"source": "verify"}, [0.1])


# ========== Test 2: Repeated search ==========

def test_repeated_search_no_lock_errors(tmp_path):
    """100 search() calls should not cause lock errors or degrade results."""
    engine = create_project_with_wiki(tmp_path)
    engine.search.index_all(force=True)

    elapsed_start = time.perf_counter()
    for _ in range(100):
        results = engine.search.search("Concept", k=3)
        assert isinstance(results, list)

    elapsed = time.perf_counter() - elapsed_start
    print(f"[bench] 100 search operations elapsed: {elapsed:.3f}s")

    chunks = engine.search.vector_store.all_chunks()
    assert len(chunks) > 0


# ========== Test 3: Repeated session commits ==========

def test_repeated_session_commits_healthy(tmp_path):
    """10 repeated session_commit calls should not corrupt registry or events."""
    engine = KnowledgeEngine(tmp_path)
    engine.init_project("test-sessions")

    elapsed_start = time.perf_counter()
    for i in range(10):
        res = engine.session_commit(
            project=str(tmp_path),
            conversation_text=make_conversation(f"iteration-{i}"),
            session_id=f"sess-{i}",
        )
        assert res["status"] in ("success", "partial", "error"), \
            f"Iteration {i}: unexpected status {res['status']}"
        if res["status"] == "error":
            assert "lock" in res.get("message", "").lower() or any(
                "lock" in w.lower() for w in res.get("warnings", [])
            )

    elapsed = time.perf_counter() - elapsed_start
    print(f"[bench] 10 session commits elapsed: {elapsed:.3f}s")

    harness = tmp_path / ".oem"
    assert_valid_json(harness / "concept_registry.json")
    assert_valid_jsonl(harness / "events.jsonl")

    results = engine.search.search("decision", k=3)
    assert isinstance(results, list)


# ========== Test 4: Engine context manager repetition ==========

def test_engine_context_manager_repetition(tmp_path):
    """20 repeated context manager openings should not leak resources."""
    init_engine = KnowledgeEngine(tmp_path)
    init_engine.init_project("test-context")
    wiki_dir = init_engine._concepts_dir(str(tmp_path))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "test.md").write_text("# Test\n\nBody")
    init_engine.search.index_all(force=True)
    init_engine.close()

    for _ in range(20):
        with KnowledgeEngine(tmp_path) as engine:
            results = engine.search.search("test", k=3)
            assert isinstance(results, list)

    with KnowledgeEngine(tmp_path) as engine:
        chunks = engine.search.vector_store.all_chunks()
        assert len(chunks) > 0


# ========== Test 5: MCP-style request repetition ==========

def test_mcp_style_request_repetition(tmp_path, monkeypatch):
    """50 repeated MCP-style tool calls should close engine every time."""
    from fastmcp import FastMCP
    import oem_knowledge.server as server_module

    close_count = []

    class FakeEngine:
        def __init__(self, *args, **kwargs):
            self.search = MagicMock()
            self.search.search.return_value = []
            self.search.stats.return_value = {"total_chunks": 0}
            self.search.index_all.return_value = {"status": "success"}
            self.state = MagicMock()
            self.state._load_registry.return_value = {}
            self.state.consolidate.return_value = {"message": "", "merged": []}
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
            self.event_migrator.get_schema_status.return_value = {"status": "up_to_date"}
            self._resolve_harness = MagicMock(return_value=tmp_path / ".oem")

        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
        def close(self):
            close_count.append(True)
        def propose_merges(self, *args, **kwargs):
            return []
        def detect_contradictions(self, *args, **kwargs):
            return []
        def embedding_cache_ready(self):
            return True

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

    close_count.clear()
    for _ in range(50):
        registered_tools['knowledge_search'](query="test", project="")

    assert len(close_count) == 50, \
        f"Expected 50 closes, got {len(close_count)}"


# ========== Test 6: Close/reopen stress ==========

def test_close_reopen_stress(tmp_path):
    """100 close/reopen cycles should not corrupt the database."""
    db_path = tmp_path / "stress.db"
    elapsed_start = time.perf_counter()

    for i in range(100):
        store = VectorStore(db_path)
        store.upsert(f"id{i}", f"doc{i}", {"source": "stress"}, [0.1])
        store.close()

    elapsed = time.perf_counter() - elapsed_start
    print(f"[bench] 100 close/reopen cycles elapsed: {elapsed:.3f}s")

    with VectorStore(db_path) as store:
        chunks = store.all_chunks()
        assert len(chunks) == 100

    with VectorStore(db_path) as store:
        store.upsert("new-id", "new doc", {"source": "post-stress"}, [0.5])
        assert store.count() == 101


# ========== Test 7: No ResourceWarning ==========

def test_no_resource_warnings_during_repeated_ops(tmp_path):
    """Repeated operations should not produce ResourceWarning for unclosed sqlite connections."""
    engine = create_project_with_wiki(tmp_path)
    engine.search.index_all(force=True)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)

        for _ in range(10):
            engine.search.index_all(force=True)
        for _ in range(20):
            engine.search.search("test", k=3)

    resource_warnings = [w for w in caught if issubclass(w.category, ResourceWarning)]
    assert not resource_warnings, \
        f"Got {len(resource_warnings)} ResourceWarning(s): {[str(w.message) for w in resource_warnings]}"


# ========== Test 8: DB writable after workflows ==========

def test_database_writable_after_repeated_workflows(tmp_path):
    """After repeated indexing + search + commits, a fresh VectorStore can still write."""
    engine = create_project_with_wiki(tmp_path)

    for _ in range(10):
        engine.search.index_all(force=True)
    for _ in range(30):
        engine.search.search("Concept", k=3)

    db_path = tmp_path / ".oem" / ".local_vector_db" / "vectors.db"
    with VectorStore(db_path) as fresh_store:
        fresh_store.upsert("fresh-id", "fresh doc", {"source": "fresh"}, [0.9])
        chunks = fresh_store.all_chunks()
        assert len(chunks) > 0

    chunks = engine.search.vector_store.all_chunks()
    assert len(chunks) > 0


# ========== Test 9: Optional FD bounds (Linux) ==========

def test_file_descriptor_bounds(tmp_path):
    """Repeated operations should not leak file descriptors (Linux /proc only)."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.exists():
        pytest.skip("FD inspection only available on Linux /proc")

    engine = create_project_with_wiki(tmp_path)
    engine.search.index_all(force=True)

    fd_count_before = len(list(fd_dir.iterdir()))

    for _ in range(25):
        engine.search.index_all(force=True)
    for _ in range(50):
        engine.search.search("test", k=3)

    fd_count_after = len(list(fd_dir.iterdir()))
    fd_increase = fd_count_after - fd_count_before
    assert fd_increase < 20, \
        f"FD count grew by {fd_increase} after repeated ops (from {fd_count_before} to {fd_count_after})"
