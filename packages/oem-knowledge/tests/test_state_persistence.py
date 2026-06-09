import pytest
import shutil
import json
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.state import StateCorruptionError, StateService

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)

def test_missing_registry_returns_empty_dict(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    # Ensure registry path does not exist
    registry_path = engine._registry_path(str(temp_project))
    if registry_path.exists():
        registry_path.unlink()
        
    with caplog.at_level(logging.WARNING):
        registry = engine.state._load_registry(str(temp_project))
        assert registry == {}
        # Ensure no warnings or errors were logged for expected missing registry
        assert len(caplog.records) == 0

def test_corrupt_registry_raises_state_corruption_error(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    registry_path = engine._registry_path(str(temp_project))
    
    # Write invalid JSON content
    sfs = engine._sfs(str(temp_project))
    sfs.write_text(registry_path, "{invalid_json}", force_allow_truncation=True)
    
    with caplog.at_level(logging.ERROR):
        with pytest.raises(StateCorruptionError) as exc_info:
            engine.state._load_registry(str(temp_project))
        
        assert "Corrupt concept registry" in str(exc_info.value)
        # Ensure error was logged
        assert any("Corrupt concept registry" in r.message for r in caplog.records)

def test_registry_permission_error_is_surfaced(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Patch StateService._sfs method so the loaded service uses our mock sfs
    with patch.object(engine.state, "_sfs") as mock_sfs_method:
        mock_sfs = MagicMock()
        mock_sfs.exists.return_value = True
        mock_sfs.read_text.side_effect = PermissionError("Permission denied")
        mock_sfs_method.return_value = mock_sfs

        with caplog.at_level(logging.ERROR):
            with pytest.raises(OSError) as exc_info:
                engine.state._load_registry(str(temp_project))
            
            assert "Permission denied" in str(exc_info.value)
            # Ensure error was logged
            assert any("Failed to read concept registry" in r.message for r in caplog.records)

def test_missing_events_file_returns_empty_list(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    events_path = engine._events_path(str(temp_project))
    if events_path.exists():
        events_path.unlink()
        
    with caplog.at_level(logging.WARNING):
        events = engine.state._load_events(str(temp_project))
        assert events == []
        # Ensure no warnings or errors were logged for expected missing events log
        assert len(caplog.records) == 0

def test_corrupt_event_line_is_skipped_and_logged(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    events_path = engine._events_path(str(temp_project))
    
    # Write events JSONL with one corrupt line
    sfs = engine._sfs(str(temp_project))
    valid_event = {
        "event_id": "ev_001",
        "timestamp": "2026-06-09T00:00:00Z",
        "project": "test_project",
        "session_id": "sess_1",
        "event_type": "observation",
        "concept_candidates": ["ai-safety"],
        "summary": "This is a summary",
        "evidence": "Some evidence here",
        "source": "test",
        "schema_version": 1
    }
    content = json.dumps(valid_event) + "\n" + "{corrupt_line}\n" + json.dumps(valid_event) + "\n"
    sfs.write_text(events_path, content, force_allow_truncation=True)
    
    with caplog.at_level(logging.WARNING):
        events = engine.state._load_events(str(temp_project))
        # It should skip the corrupt line (line 2) and parse the 2 valid events
        assert len(events) == 2
        assert events[0]["event_id"] == "ev_001"
        assert events[1]["event_id"] == "ev_001"
        
        # Verify specific explicit warning is logged
        warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("Skipping corrupt event line 2" in w for w in warnings)

def test_event_append_write_error_is_surfaced(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Patch StateService._sfs method to return mock sfs that raises write error
    with patch.object(engine.state, "_sfs") as mock_sfs_method:
        mock_sfs = MagicMock()
        mock_sfs.append_text.side_effect = OSError("Disk full")
        mock_sfs_method.return_value = mock_sfs

        valid_event = {
            "event_id": "ev_001",
            "timestamp": "2026-06-09T00:00:00Z",
            "project": "test_project",
            "session_id": "sess_1",
            "event_type": "observation",
            "concept_candidates": ["ai-safety"],
            "summary": "This is a summary",
            "evidence": "Some evidence here",
            "source": "test",
            "schema_version": 1
        }

        with caplog.at_level(logging.ERROR):
            with pytest.raises(OSError) as exc_info:
                engine.state._append_event(valid_event, str(temp_project))
            
            assert "Disk full" in str(exc_info.value)
            assert any("Failed to append event" in r.message for r in caplog.records)

def test_caller_propagation_bubbles_corruption_error(temp_project):
    engine = KnowledgeEngine(temp_project)
    registry_path = engine._registry_path(str(temp_project))
    
    # Write invalid JSON content to concept registry
    sfs = engine._sfs(str(temp_project))
    sfs.write_text(registry_path, "{invalid_json}", force_allow_truncation=True)
    
    # Calling rebuild_registry or explain_concept should bubble up StateCorruptionError
    with pytest.raises(StateCorruptionError):
        engine.state.rebuild_registry(str(temp_project))
        
    with pytest.raises(StateCorruptionError):
        engine.state.explain_concept(str(temp_project), "concept_001")
