"""Wave 2: search filters apply against normalized metadata (scope/type/window)."""


def _make_engine(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    eng.search.set_retrieval_mode("bm25")
    return eng


def _seed(eng, chunks):
    store = eng.search.vector_store
    for doc_id, document, metadata in chunks:
        store.upsert(doc_id, document, metadata, None)


def test_scope_project_excludes_user_records(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("proj-1", "Decision: use SQLite for event storage", {"scope": "project", "memory_type": "decision", "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400", "source": "events.jsonl"}),
        ("user-1", "Preference: prefer hybrid retrieval", {"scope": "user", "memory_type": "preference", "timestamp": "2026-07-10T12:00:00Z", "created_at": "1783670400", "source": "user_events.jsonl"}),
    ])
    proj = eng.search.search("decision SQLite", k=5, scope="project")
    assert [r["id"] for r in proj] == ["proj-1"]
    user = eng.search.search("preference hybrid", k=5, scope="user")
    assert [r["id"] for r in user] == ["user-1"]


def test_missing_scope_counts_as_project(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("no-scope-1", "Decision: adopt spawn-isolated indexing", {"memory_type": "decision", "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400", "source": "events.jsonl"}),
    ])
    proj = eng.search.search("spawn-isolated indexing", k=5, scope="project")
    assert [r["id"] for r in proj] == ["no-scope-1"]
    user = eng.search.search("spawn-isolated indexing", k=5, scope="user")
    assert user == []


def test_explicit_index_time_memory_type_is_authoritative(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("evt-1", "Adopted spawn-isolated indexing with 10s budget", {"scope": "project", "memory_type": "decision", "timestamp": "2026-07-20T11:00:00Z", "created_at": "1785146400", "source": "events.jsonl"}),
    ])
    res = eng.search.search("spawn-isolated indexing", k=5, memory_type="decision")
    assert [r["id"] for r in res] == ["evt-1"]


def test_unknown_metadata_type_falls_back_to_document_classification(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("evt-2", "Decision: use SQLite for event storage", {"scope": "project", "memory_type": "banana", "timestamp": "2026-07-20T11:00:00Z", "created_at": "1785146400", "source": "events.jsonl"}),
    ])
    res = eng.search.search("SQLite decision", k=5, memory_type="decision")
    assert [r["id"] for r in res] == ["evt-2"]


def test_window_filter_excludes_out_of_window(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("old-1", "Observation: old note", {"scope": "project", "memory_type": "observation", "timestamp": "2026-01-01T00:00:00Z", "created_at": "1767225600", "source": "events.jsonl"}),
        ("new-1", "Observation: recent note", {"scope": "project", "memory_type": "observation", "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400", "source": "events.jsonl"}),
    ])
    res = eng.search.search("note", k=5, since="2026-06-01")
    assert [r["id"] for r in res] == ["new-1"]


def test_invalid_window_returns_empty(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("proj-1", "Decision: use SQLite for event storage", {"scope": "project", "memory_type": "decision", "timestamp": "2026-07-01T10:00:00Z", "created_at": "1782914400", "source": "events.jsonl"}),
    ])
    assert eng.search.search("SQLite", k=5, since="not-a-date") == []


def test_default_search_returns_records_without_scope_metadata(tmp_path):
    eng = _make_engine(tmp_path)
    _seed(eng, [
        ("plain-1", "Decision: use SQLite", {"created_at": "1782914400", "source": "events.jsonl"}),
    ])
    res = eng.search.search("use SQLite", k=5)
    assert [r["id"] for r in res] == ["plain-1"]
