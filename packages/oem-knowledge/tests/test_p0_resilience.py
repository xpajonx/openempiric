import pytest
import os
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.runtime.session import SessionState
from oem_knowledge.services.reflection import llm_extraction_available

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_session_commit_structured_events_no_llm_success(engine, tmp_path, monkeypatch):
    # Disable LLM
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    mock_llm = MagicMock(side_effect=AssertionError("LLM should not be called"))
    monkeypatch.setattr(engine.reflection, "_run_llm_extraction", mock_llm)
    
    events_data = [
        {
            "event_type": "observation",
            "summary": "Structured event works.",
            "evidence": "evidence"
        }
    ]
    
    res = engine.session_commit(
        project=str(tmp_path),
        events=events_data,
        extraction_mode="structured"
    )
    
    assert res["status"] == "success"
    assert res["events_written"] == 1
    mock_llm.assert_not_called()

def test_session_commit_marker_events_no_llm_success(engine, tmp_path, monkeypatch):
    # Disable LLM
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    mock_llm = MagicMock(side_effect=AssertionError("LLM should not be called"))
    monkeypatch.setattr(engine.reflection, "_run_llm_extraction", mock_llm)
    
    convo = "Observation: Marker works without LLM."
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text=convo,
        extraction_mode="markers"
    )
    
    assert res["status"] == "success"
    assert res["events_written"] == 1
    mock_llm.assert_not_called()

def test_session_commit_dense_no_llm_returns_warn_not_error(engine, tmp_path, monkeypatch):
    # Disable LLM
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense transcript only.",
        extraction_mode="llm"
    )
    
    assert res["status"] == "warn"
    assert any("LLM extraction unavailable" in w for w in res["warnings"])
    assert res["events_written"] == 0

def test_session_commit_dense_no_llm_skips_materialization_and_index(engine, tmp_path, monkeypatch):
    # Disable LLM
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense transcript.",
        extraction_mode="llm"
    )
    
    assert res["status"] == "warn"
    assert res.get("materialization_skipped") is True
    assert res.get("index_skipped") is True

def test_session_commit_llm_timeout_returns_partial_not_fatal(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    engine.reflection._mock_slow = True
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="slow_extraction_mock",
        extraction_mode="llm",
        timeout_seconds=0.01
    )
    
    assert res["status"] == "partial"
    assert res["failed_step"] == "llm_extraction"
    assert "LLM extraction timed out" in res["message"]
    assert res["events_written"] == 0

def test_session_commit_empty_reflection_closes_session_safely(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    
    # Setup active session state
    harness = Path(tmp_path) / OEM_DIR
    active_file = harness / "state" / "active_session.json"
    session_state = SessionState.create(
        session_id="test_session",
        agent="opencode",
        project=str(tmp_path),
        transcript_path=str(harness / "state" / "chat_test_session.md"),
        context_path=str(harness / "state" / "oem_runtime_context.json"),
        temp_instructions=str(harness / "state" / "oem_temp_instructions.md")
    )
    session_state.save(active_file)
    
    # Touch transient files
    Path(session_state.context_path).touch()
    Path(session_state.temp_instructions).touch()
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="No metrics",
        session_id="test_session",
        extraction_mode="llm"
    )
    
    assert res["status"] == "empty"
    assert not active_file.exists()
    assert not Path(session_state.context_path).exists()
    assert not Path(session_state.temp_instructions).exists()

def test_session_commit_missing_api_key_does_not_prompt(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    # If it tries to prompt (e.g. input()), it will raise OSError/EOFError in test env.
    # We assert it exits/returns safely with warn and no prompt error/exception.
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text.",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"

def test_session_commit_panel_uses_warn_for_llm_unavailable(engine, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text.",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    
    # We inspect the stdout output from CommitProgressSupervisor
    captured = capsys.readouterr()
    assert "Dense LLM Reflection Skipped" in captured.out
    assert "No local/remote LLM provider configured" in captured.out
    assert "!" in captured.out or "Dense LLM Reflection Skipped" in captured.out

def test_session_commit_panel_uses_skipped_for_no_events(engine, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="No metrics",
        extraction_mode="llm"
    )
    assert res["status"] == "empty"
    
    captured = capsys.readouterr()
    assert "Materialization Skipped" in captured.out
    assert "Search Index Skipped" in captured.out
    assert "-" in captured.out or "Skipped" in captured.out

def test_session_commit_fails_only_on_write_path_error(engine, tmp_path, monkeypatch):
    # Structured events with structured mode, but append_events fails (write path error)
    def mock_append_failed(*args, **kwargs):
        raise OSError("Permission denied on events.jsonl")
    
    monkeypatch.setattr(engine.state, "append_events", mock_append_failed)
    
    events_data = [
        {"event_type": "observation", "summary": "valid event", "evidence": "evidence"}
    ]
    
    with pytest.raises(OSError, match="Permission denied"):
        engine.session_commit(
            project=str(tmp_path),
            events=events_data,
            extraction_mode="structured"
        )

def test_knowledge_session_end_missing_llm_warns_not_errors(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    res = engine.session_end(
        project=str(tmp_path),
        conversation_text="Some text",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    assert any("LLM extraction unavailable" in w for w in res["warnings"])

def test_session_commit_warn_path_cleans_temp_context_files(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    harness = Path(tmp_path) / OEM_DIR
    active_file = harness / "state" / "active_session.json"
    session_state = SessionState.create(
        session_id="warn_session",
        agent="opencode",
        project=str(tmp_path),
        transcript_path=str(harness / "state" / "chat_warn_session.md"),
        context_path=str(harness / "state" / "oem_runtime_context.json"),
        temp_instructions=str(harness / "state" / "oem_temp_instructions.md")
    )
    session_state.save(active_file)
    
    Path(session_state.context_path).touch()
    Path(session_state.temp_instructions).touch()
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text only.",
        session_id="warn_session",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    assert not Path(session_state.context_path).exists()
    assert not Path(session_state.temp_instructions).exists()

def test_session_commit_warn_path_unlinks_active_session_file(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    harness = Path(tmp_path) / OEM_DIR
    active_file = harness / "state" / "active_session.json"
    session_state = SessionState.create(
        session_id="warn_session",
        agent="opencode",
        project=str(tmp_path),
        transcript_path=str(harness / "state" / "chat_warn_session.md"),
        context_path=str(harness / "state" / "oem_runtime_context.json"),
        temp_instructions=str(harness / "state" / "oem_temp_instructions.md")
    )
    session_state.save(active_file)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text only.",
        session_id="warn_session",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    assert not active_file.exists()

def test_session_commit_warn_path_records_warning_outcome_not_clean_success(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text only.",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    
    # Read outcomes.jsonl and check recorded status
    outcomes_file = Path(tmp_path) / OEM_DIR / "state" / "outcomes.jsonl"
    assert outcomes_file.exists()
    lines = outcomes_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    outcome_obj = json.loads(lines[0])
    assert outcome_obj["outcome"] == "success_with_warnings"

def test_no_events_does_not_call_materialization(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    mock_mat = MagicMock()
    monkeypatch.setattr(engine.materialization, "materialize_concepts", mock_mat)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text only.",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    mock_mat.assert_not_called()

def test_no_events_does_not_call_index_update(engine, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    mock_idx = MagicMock()
    monkeypatch.setattr(engine.search, "index_all", mock_idx)
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Dense text only.",
        extraction_mode="llm"
    )
    assert res["status"] == "warn"
    mock_idx.assert_not_called()

def test_session_commit_llm_timeout_worker_does_not_write_late_events(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    engine.reflection._mock_slow = True
    
    # Mock StateService append_events to track calls
    mock_append = MagicMock(wraps=engine.state.append_events)
    monkeypatch.setattr(engine.state, "append_events", mock_append)
    
    # We run with timeout. Since it times out, the main thread returns 'partial' and does not append events.
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="slow_extraction_mock",
        extraction_mode="llm",
        timeout_seconds=0.01
    )
    
    assert res["status"] == "partial"
    assert res["failed_step"] == "llm_extraction"
    
    # Wait for the slow daemon worker thread to finish sleeping and executing fallback extract (which takes 2s)
    time.sleep(2.2)
    
    # Ensure that even after worker thread completed, no events were written to the state service
    mock_append.assert_not_called()
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    assert not events_file.exists()
