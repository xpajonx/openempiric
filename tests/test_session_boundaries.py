from __future__ import annotations
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.context import _compile_oem_context

@pytest.fixture
def temp_project(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    eng.init_project(str(tmp_path))
    return eng, tmp_path

def test_session_markers_detected_when_present(temp_project):
    engine, tmp_path = temp_project
    
    # Check both "session start" and "session end"
    res_start = engine.reflect_session(
        project=str(tmp_path),
        conversation_text="Session start: Let's refactor the search module.",
        session_id="test_start",
    )
    assert res_start["explainability"]["session_markers_detected"] is True

    res_end = engine.reflect_session(
        project=str(tmp_path),
        conversation_text="We completed everything. Session end.",
        session_id="test_end",
    )
    assert res_end["explainability"]["session_markers_detected"] is True

def test_session_markers_not_detected_when_absent(temp_project):
    engine, tmp_path = temp_project
    
    res = engine.reflect_session(
        project=str(tmp_path),
        conversation_text="This is a general chat without any special session markers.",
        session_id="test_absent",
    )
    assert res["explainability"]["session_markers_detected"] is False

def test_runtime_notice_contains_guidelines(temp_project):
    engine, tmp_path = temp_project
    
    ctx = _compile_oem_context(engine)
    notice = ctx.get("memory_context", "")
    
    assert "Session boundary markers may appear naturally" in notice
    assert "- Session start" in notice
    assert "- Session end" in notice
    assert "Treat these as conversational context signals" in notice
    assert "Do not instruct users to run lifecycle commands" in notice
