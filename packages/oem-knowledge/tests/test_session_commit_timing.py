import pytest
import shutil
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.fs import LockTimeoutError

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)

def test_session_commit_returns_phase_timings(temp_project):
    engine = KnowledgeEngine(temp_project)
    res = engine.session_commit(
        str(temp_project),
        conversation_text="- Fix test timing",
        session_id="test_ses_1"
    )
    assert "phase_timings" in res
    timings = res["phase_timings"]
    assert "load_state" in timings
    assert "reflection" in timings
    assert "append_events" in timings
    assert "materialization" in timings
    assert "search_index" in timings
    assert "write_report" in timings
    assert "cleanup" in timings
    assert "total" in timings
    assert timings["total"] >= 0

def test_session_commit_no_index_skips_search_index(temp_project):
    engine = KnowledgeEngine(temp_project)
    with patch.object(engine.search, "index_all") as mock_index:
        res = engine.session_commit(
            str(temp_project),
            conversation_text="- Fix test timing",
            session_id="test_ses_2",
            update_index=False
        )
        assert res["status"] in ("success", "partial")
        mock_index.assert_not_called()
        assert any("skipped" in w.lower() for w in res["warnings"])

def test_session_commit_index_budget_timeout_returns_partial(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # We mock _index_all_impl to simulate budget exceed
    def mock_index_all(*args, **kwargs):
        return {
            "status": "partial",
            "error": "Indexing budget exceeded"
        }
    
    with patch.object(engine.search, "_index_all_impl", new=mock_index_all):
        res = engine.session_commit(
            str(temp_project),
            conversation_text="- Fix test timing budget",
            session_id="test_ses_3",
            update_index=True,
            index_budget_seconds=10.0
        )
        assert res["status"] == "partial"
        assert res["failed_step"] == "indexing"
        assert any("skipped after timeout budget" in w.lower() for w in res["warnings"])

def test_session_commit_saves_events_when_index_skipped(temp_project):
    engine = KnowledgeEngine(temp_project)
    res = engine.session_commit(
        str(temp_project),
        conversation_text="- Fix test timing budget",
        session_id="test_ses_4",
        update_index=False
    )
    assert res["status"] in ("success", "partial")
    
    # Verify that events were appended to events.jsonl or equivalent state
    events = engine.state.get_events(str(temp_project), session_id="test_ses_4")
    assert len(events) > 0

def test_session_commit_lock_failure_identifies_phase(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # Mock materialize_concepts to raise LockTimeoutError
    with patch.object(engine.materialization, "materialize_concepts", side_effect=LockTimeoutError("Locked")):
        res = engine.session_commit(
            str(temp_project),
            conversation_text="- Fix test lock fail",
            session_id="test_ses_5"
        )
        assert res["status"] == "error"
        assert res["failed_step"] == "materialization"
        assert any("lock" in w.lower() for w in res["warnings"])
        assert any("registry" in w.lower() for w in res["warnings"])
        assert any("runtime_events" in w.lower() for w in res["warnings"])
        assert any("state" in w.lower() for w in res["warnings"])
