import pytest
import os
import json
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine, OEM_DIR

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_knowledge_reflect_structured_events_no_llm_called(engine, tmp_path, monkeypatch):
    called = False
    def mock_llm(*args, **kwargs):
        nonlocal called
        called = True
        return []
    
    monkeypatch.setattr(engine.reflection, "_run_llm_extraction", mock_llm)
    
    events_data = [
        {
            "event_type": "observation",
            "summary": "Structured events fast path works.",
            "evidence": "Observed fast path test passing."
        }
    ]
    
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert not called
    assert res["status"] == "success"
    assert res["events_written"] == 1

def test_knowledge_reflect_marker_events_no_llm_called(engine, tmp_path, monkeypatch):
    def mock_llm(*args, **kwargs):
        raise AssertionError("LLM should not be called in marker mode")
        
    monkeypatch.setattr(engine.reflection, "_run_llm_extraction", mock_llm)
    
    convo = "Observation: Simple line-based marker works."
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text=convo,
        extraction_mode="markers"
    )
    
    assert res["status"] == "success"
    assert res["events_written"] == 1
    assert res["canonical_events"][0]["event_type"] == "observation"

def test_knowledge_reflect_llm_timeout_returns_partial_not_raw_timeout(engine, tmp_path):
    # Enable the slow mock in reflect_session
    engine.reflection._mock_slow = True
    
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="slow_extraction_mock",
        extraction_mode="llm",
        timeout_seconds=0.01
    )
    
    assert res["status"] == "partial"
    assert res["failed_step"] == "llm_extraction"
    assert "LLM extraction timed out" in res["message"]
    assert res["events_written"] == 0

def test_knowledge_reflect_empty_extraction_returns_empty_with_suggestion(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="This contains no recognized heuristics.",
        extraction_mode="llm"
    )
    
    assert res["status"] == "empty"
    assert res["events_written"] == 0
    assert "suggestion" in res
    assert res["suggestion"] is not None

def test_knowledge_reflect_writes_events_through_state_service(engine, tmp_path):
    events_data = [
        {
            "event_type": "decision",
            "summary": "StateService handles structured events.",
            "evidence": "Event saved in file."
        }
    ]
    
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert res["status"] == "success"
    
    # Verify events file exists and contains the event
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    assert events_file.exists()
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event_obj = json.loads(lines[0])
    assert event_obj["event_type"] == "decision"
    assert event_obj["summary"] == "StateService handles structured events."

def test_knowledge_reflect_updates_session_without_orphan_empty_files(engine, tmp_path):
    sessions_dir = Path(tmp_path) / OEM_DIR / "sessions"
    
    # 1. Success case should create a report file
    res_ok = engine.reflection.reflect_session(
        project=str(tmp_path),
        events=[{"event_type": "observation", "summary": "Valid event.", "evidence": "evidence"}],
        extraction_mode="structured"
    )
    assert res_ok["status"] == "success"
    assert len(list(sessions_dir.glob("*.md"))) == 1
    
    # 2. Timeout case should NOT create any new report file
    res_timeout = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="slow_extraction_mock",
        extraction_mode="llm",
        timeout_seconds=0.01
    )
    assert res_timeout["status"] == "partial"
    assert len(list(sessions_dir.glob("*.md"))) == 1 # still 1
    
    # 3. Empty case should NOT create any new report file
    res_empty = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="No metrics",
        extraction_mode="llm"
    )
    assert res_empty["status"] == "empty"
    assert len(list(sessions_dir.glob("*.md"))) == 1 # still 1

def test_knowledge_reflect_rejects_invalid_event_schema(engine, tmp_path):
    events_data = [
        {
            "event_type": "decision",
            # missing summary (invalid)
            "evidence": "missing summary field"
        }
    ]
    
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert res["status"] == "empty"
    assert res["events_written"] == 0
    assert res["events_rejected"] == 1
    assert any("Event rejected" in w for w in res["warnings"])

def test_knowledge_reflect_preserves_valid_jsonl(engine, tmp_path):
    # Write some valid events
    events_data = [
        {"event_type": "observation", "summary": "First event.", "evidence": "evidence"},
        {"event_type": "decision", "summary": "Second event.", "evidence": "evidence"}
    ]
    
    engine.reflection.reflect_session(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    content = events_file.read_text(encoding="utf-8")
    lines = content.strip().splitlines()
    assert len(lines) == 2
    
    # Assert each line parses as valid JSON
    for line in lines:
        parsed = json.loads(line)
        assert "event_id" in parsed
        assert "summary" in parsed

def test_knowledge_reflect_structured_events_written_once(engine, tmp_path):
    events_data = [
        {"event_type": "observation", "summary": "Written once event.", "evidence": "evidence"}
    ]
    
    # Call commit/reflect via engine.session_commit to verify it writes exactly once
    res = engine.session_commit(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert res["status"] == "success"
    
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

def test_knowledge_reflect_partial_invalid_batch_writes_valid_events(engine, tmp_path):
    events_data = [
        {"event_type": "observation", "summary": "Valid structured event.", "evidence": "evidence"},
        {"event_type": "decision", "evidence": "Invalid since summary is missing."}
    ]
    
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert res["status"] == "partial"
    assert res["events_written"] == 1
    assert res["events_rejected"] == 1
    assert len(res["warnings"]) >= 1
    
    # Only valid event should be written to jsonl
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["summary"] == "Valid structured event."
