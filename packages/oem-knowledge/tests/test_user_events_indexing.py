"""Wave 3A: user-scoped events are indexed, searchable, idempotent, and hygienic."""

import os
import json
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _user_home(monkeypatch, tmp_path):
    monkeypatch.setenv("OEM_USER_ID", "test-user@example.com")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    yield tmp_path


def _write_user_events(events: list[dict]) -> Path:
    from oem_knowledge.services.state import get_user_events_path
    path = get_user_events_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def _make_engine(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    eng.search.set_retrieval_mode("bm25")
    return eng


def _event(event_id, summary, event_type="preference", timestamp="2026-07-10T12:00:00Z"):
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "project": "test-proj",
        "session_id": "sess-1",
        "event_type": event_type,
        "summary": summary,
        "evidence": summary,
        "source": "inline_agent",
    }


def test_user_events_indexed_and_searchable_by_scope(tmp_path):
    _write_user_events([_event("evt-1", "Preference: prefer hybrid retrieval")])
    eng = _make_engine(tmp_path)
    eng.search.index_user_events()
    user = eng.search.search("hybrid retrieval", k=5, scope="user")
    assert [r["id"] for r in user] == ["user#evt-1"]
    proj = eng.search.search("hybrid retrieval", k=5, scope="project")
    assert proj == []


def test_index_is_idempotent_by_event_id(tmp_path):
    _write_user_events([_event("evt-1", "Preference: prefer hybrid retrieval")])
    eng = _make_engine(tmp_path)
    eng.search.index_user_events()
    eng.search.index_user_events()
    assert eng.search.vector_store.count_chunks_by_source("user_events.jsonl") == 1


def test_stale_user_events_removed_on_reindex(tmp_path):
    _write_user_events([_event("evt-1", "Preference: hybrid"), _event("evt-2", "Preference: bm25")])
    eng = _make_engine(tmp_path)
    eng.search.index_user_events()
    _write_user_events([_event("evt-2", "Preference: bm25")])
    eng.search.index_user_events()
    assert eng.search.vector_store.count_chunks_by_source("user_events.jsonl") == 1
    res = eng.search.search("bm25", k=5, scope="user")
    assert [r["id"] for r in res] == ["user#evt-2"]


def test_command_log_user_events_rejected(tmp_path):
    _write_user_events([_event("evt-1", "Command `ls` executed with exit code 0")])
    eng = _make_engine(tmp_path)
    stats = eng.search.index_user_events()
    assert eng.search.vector_store.count_chunks_by_source("user_events.jsonl") == 0
    assert stats["rejected"] == 1


def test_malformed_line_rejected(tmp_path):
    path = _write_user_events([_event("evt-1", "Preference: hybrid")])
    with open(path, "a", encoding="utf-8") as f:
        f.write("not json {{{\n")
    eng = _make_engine(tmp_path)
    stats = eng.search.index_user_events()
    assert eng.search.vector_store.count_chunks_by_source("user_events.jsonl") == 1
    assert stats["rejected"] == 1


def test_missing_event_id_rejected(tmp_path):
    ev = _event("evt-1", "Preference: hybrid")
    del ev["event_id"]
    _write_user_events([ev])
    eng = _make_engine(tmp_path)
    stats = eng.search.index_user_events()
    assert eng.search.vector_store.count_chunks_by_source("user_events.jsonl") == 0
    assert stats["rejected"] == 1


def test_inline_user_memory_indexed_immediately(tmp_path):
    eng = _make_engine(tmp_path)
    res = eng.reflection.add_inline_memory(
        memory_type="preference",
        content="Preference: use hybrid retrieval mode",
        scope="user",
        evidence="The team prefers hybrid retrieval with BM25 fallback for reliability",
    )
    assert res.get("status") == "success"
    found = eng.search.search("hybrid retrieval", k=5, scope="user")
    assert len(found) >= 1
    assert found[0]["metadata"].get("scope") == "user"


def test_epoch_timestamp_file_chunks_are_window_filterable(tmp_path):
    eng = _make_engine(tmp_path)
    harness = eng._resolve_harness(str(tmp_path / "proj"))
    wiki = harness / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    concept_file = wiki / "concept_001.md"
    concept_file.write_text("# Storage\n\nDecision: use SQLite for event storage\n", encoding="utf-8")
    ts = 1768521600  # 2026-01-15
    os.utime(concept_file, (ts, ts))
    eng.search.index_all()
    res = eng.search.search("use SQLite", k=5)
    assert any("concept_001.md" in r["id"] for r in res)
    filtered = eng.search.search("use SQLite", k=5, since="2026-06-01")
    assert all("concept_001.md" not in r["id"] for r in filtered)
