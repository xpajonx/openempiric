import os
import json
import yaml
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.reflection import llm_extraction_available
from oem_knowledge.health import build_runtime_health
from oem_knowledge.runtime.instructions import OEM_MEMORY_INSTRUCTIONS

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def write_reflection_config(tmp_path, config_data):
    oem_dir = tmp_path / ".oem"
    cfg_dir = oem_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "reflection.yml"
    with open(cfg_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({"reflection": config_data}, f)

def test_reflection_config_path_layout(engine, tmp_path):
    layout = engine.layout(str(tmp_path))
    assert layout.reflection_config_path == tmp_path / ".oem" / "config" / "reflection.yml"

def test_init_project_creates_default_config(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    layout = eng.layout(str(tmp_path))
    assert layout.reflection_config_path.exists()
    
    # Read config
    with open(layout.reflection_config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert "reflection" in data
    assert data["reflection"]["dense"]["enabled"] is False

def test_init_project_does_not_overwrite_existing_config(tmp_path):
    oem_dir = tmp_path / ".oem"
    cfg_dir = oem_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "reflection.yml"
    cfg_file.write_text("reflection:\n  dense:\n    enabled: true\n")
    
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    
    # Assert custom config preserved
    with open(cfg_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["reflection"]["dense"]["enabled"] is True

def test_runner_shutdown_does_not_call_dense_reflection_when_disabled(engine, tmp_path, monkeypatch):
    # Set config to dense disabled
    write_reflection_config(tmp_path, {
        "dense": {"enabled": False}
    })
    
    # Mock LLM availability to False
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    # Run session end
    res = engine.session_end(str(tmp_path), conversation_text="Decision: we refactored", session_id="test_sess")
    
    # Verify it skipped dense cleanly and succeeded/markers used
    assert res["status"] in ("success", "empty")
    diag = res.get("reflection", {})
    assert diag["dense"]["status"] == "skipped"
    assert diag["dense"]["reason"] == "dense_disabled"

def test_runner_shutdown_skips_dense_when_llm_unavailable(engine, tmp_path, monkeypatch):
    # Set config to dense enabled
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": False}
    })
    
    # Mock LLM availability to False
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    # Run session end with text that doesn't trigger markers
    res = engine.session_end(str(tmp_path), conversation_text="some text without markers", session_id="test_sess")
    
    # Status should be warn because dense is enabled but unavailable
    assert res["status"] == "warn"
    diag = res.get("reflection", {})
    assert diag["dense"]["status"] == "skipped"
    assert diag["dense"]["reason"] == "dense_llm_unavailable"
    assert diag["dense"]["severity"] == "warning"

def test_runner_shutdown_still_closes_session_without_dense_llm(engine, tmp_path, monkeypatch):
    # Setup active session file
    harness = engine._resolve_harness(str(tmp_path))
    active_file = harness / "state" / "active_session.json"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    active_file.write_text(json.dumps({
        "session_id": "test_sess",
        "status": "running",
        "agent": "opencode"
    }))
    
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": False}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    # Run session end
    res = engine.session_end(str(tmp_path), conversation_text="", session_id="test_sess")
    
    # Verify session file was unlinked and closed successfully (not stuck)
    assert not active_file.exists()
    assert res["status"] in ("warn", "empty", "success")

def test_runner_shutdown_does_not_create_pending_dense_backlog_by_default(engine, tmp_path, monkeypatch):
    # dense.enabled=true, queue_pending=false (default is false)
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": False}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    engine.session_end(str(tmp_path), conversation_text="some chat text", session_id="test_sess")
    
    layout = engine.layout(str(tmp_path))
    assert not layout.pending_dense_reflections_path.exists()

def test_runner_shutdown_creates_pending_dense_backlog_when_opt_in(engine, tmp_path, monkeypatch):
    # opt-in queue_pending=true
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": True, "max_pending_items": 5}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    engine.session_end(str(tmp_path), conversation_text="some chat text", session_id="test_sess")
    
    layout = engine.layout(str(tmp_path))
    assert layout.pending_dense_reflections_path.exists()
    
    # Read backlog
    with open(layout.pending_dense_reflections_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1
    item = json.loads(lines[0])
    assert item["session_id"] == "test_sess"
    assert item["conversation_text"] == "some chat text"

def test_pending_backlog_pruning_bounds(engine, tmp_path, monkeypatch):
    # max pending items = 2
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": True, "max_pending_items": 2}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    # Add 3 items
    engine.session_end(str(tmp_path), conversation_text="chat 1", session_id="sess_1")
    engine.session_end(str(tmp_path), conversation_text="chat 2", session_id="sess_2")
    engine.session_end(str(tmp_path), conversation_text="chat 3", session_id="sess_3")
    
    layout = engine.layout(str(tmp_path))
    with open(layout.pending_dense_reflections_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # Should only keep 2 newest items
    assert len(lines) == 2
    item1 = json.loads(lines[0])
    item2 = json.loads(lines[1])
    assert item1["session_id"] == "sess_2"
    assert item2["session_id"] == "sess_3"

def test_agent_instructions_contain_retry_guidelines():
    assert "dense_llm_unavailable" in OEM_MEMORY_INSTRUCTIONS
    assert "do not repeatedly retry dense reflection" in OEM_MEMORY_INSTRUCTIONS

def test_health_check_reports_dense_disabled_as_healthy(engine, tmp_path, monkeypatch):
    write_reflection_config(tmp_path, {
        "dense": {"enabled": False}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    health = build_runtime_health(str(tmp_path))
    diag = health["reflection_diagnostic"]
    assert diag["dense_llm"] == "not configured"
    assert diag["status"] == "healthy"

def test_health_check_warns_when_pending_dense_backlog_exists(engine, tmp_path, monkeypatch):
    write_reflection_config(tmp_path, {
        "dense": {"enabled": True, "queue_pending": True}
    })
    monkeypatch.setattr("oem_knowledge.services.reflection.llm_extraction_available", lambda: False)
    
    # Trigger queue writing
    engine.session_end(str(tmp_path), conversation_text="some chat", session_id="sess_1")
    
    health = build_runtime_health(str(tmp_path))
    diag = health["reflection_diagnostic"]
    assert diag["pending_count"] == 1
    assert diag["status"] == "warning"
