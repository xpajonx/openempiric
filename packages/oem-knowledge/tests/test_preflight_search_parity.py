"""Wave 3B: preflight uses the shared SearchService retriever with identical ranking."""


def _make_engine(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    eng.search.set_retrieval_mode("bm25")
    return eng


def test_preflight_uses_shared_retriever(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path)
    store = eng.search.vector_store
    store.upsert("wiki/concept_001.md#chunk_0",
                 "Document: wiki/concept_001.md\nSection: Storage\n\nDecision: use SQLite for event storage",
                 {"source": "wiki/concept_001.md", "scope": "project", "memory_type": "decision",
                  "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400"}, None)
    calls = []
    original = eng.search.search
    def spy(q, k=3, **kw):
        calls.append(q)
        return original(q, k=k, **kw)
    monkeypatch.setattr(eng.search, "search", spy)
    res = eng.preflight("use SQLite for event storage", project=str(tmp_path / "proj"))
    assert calls, "preflight must call the shared retriever"
    memory_ids = [m.get("id") for m in res.get("matched_memory", [])]
    search_ids = [r["id"] for r in eng.search.search("use SQLite for event storage", k=10)]
    assert any(mid in search_ids for mid in memory_ids), "preflight memory ids must come from search results"


def test_preflight_weak_memory_gate_preserved(tmp_path):
    eng = _make_engine(tmp_path)
    store = eng.search.vector_store
    store.upsert("generic-1", "Decision: the project continues as planned",
                 {"source": "events.jsonl", "scope": "project", "memory_type": "decision",
                  "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400"}, None)
    res = eng.preflight("quarterly financial report review", project=str(tmp_path / "proj"))
    memory_ids = [m.get("id") for m in res.get("matched_memory", [])]
    assert "generic-1" not in memory_ids, "weak topic-only memory must stay gated out"


def test_preflight_fallback_on_retriever_error(tmp_path, monkeypatch):
    eng = _make_engine(tmp_path)
    def broken(q, k=3, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(eng.search, "search", broken)
    res = eng.preflight("anything at all", project=str(tmp_path / "proj"))
    assert res.get("status") in ("success", "warn", "noop")
