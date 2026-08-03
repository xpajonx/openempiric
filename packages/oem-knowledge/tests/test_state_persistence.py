import builtins
import pytest
import shutil
import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.state import StateCorruptionError, StateService
from oem_knowledge.models import KnowledgeEvent

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

def test_get_events_filter_normalizes_candidates(temp_project):
    engine = KnowledgeEngine(temp_project)
    event = {
        "event_id": "ev_filter_001",
        "timestamp": "2026-07-23T00:00:00Z",
        "project": "test_project",
        "session_id": "sess_filter",
        "event_type": "observation",
        "concept_candidates": ["general learning"],
        "summary": "Filter normalization test",
        "evidence": "Test evidence",
        "source": "test",
        "schema_version": 1,
        "confidence": 1,
    }
    engine.state.append_event(event, str(temp_project))
    results = engine.state.get_events(str(temp_project), concept="general learning")
    assert len(results) == 1
    assert results[0]["event_id"] == "ev_filter_001"

def test_get_events_concept_id_lookup(temp_project):
    engine = KnowledgeEngine(temp_project)
    # Create a concept in the registry with canonical_name "general-learning"
    registry = engine.state._load_registry(str(temp_project), lock=True)
    cdata = {
        "concept_id": "concept_001",
        "canonical_name": "general-learning",
        "aliases": ["general learning"],
        "confidence": 1,
        "status": "validated",
        "evidence_count": 0,
        "session_count": 1,
        "source_event_ids": [],
    }
    registry["concept_001"] = cdata
    engine.state._save_registry(registry, str(temp_project), lock=True)
    # Write an event referencing the concept by its natural name
    event = {
        "event_id": "ev_cid_001",
        "timestamp": "2026-07-23T00:00:00Z",
        "project": "test_project",
        "session_id": "sess_cid",
        "event_type": "observation",
        "concept_candidates": ["general learning"],
        "summary": "Concept ID resolution test",
        "evidence": "Test evidence",
        "source": "test",
        "schema_version": 1,
        "confidence": 1,
    }
    engine.state.append_event(event, str(temp_project))
    # Query by concept_id "concept_001" — should resolve to canonical_name "general-learning"
    # which normalizes to "general-learning" and matches "general learning" in candidates
    results = engine.state.get_events(str(temp_project), concept="concept_001")
    assert len(results) == 1
    assert results[0]["event_id"] == "ev_cid_001"

def test_resolve_auto_mode_hybrid_when_fastembed_available(temp_project, monkeypatch):
    """Auto mode resolves to 'hybrid' only when fastembed is importable AND
    embedding_cache_ready() is True; otherwise it resolves to 'bm25'."""
    engine = KnowledgeEngine(temp_project)
    fake_fastembed = MagicMock()
    fake_fastembed.TextEmbedding = MagicMock()

    # (a) fastembed importable + embedding cache ready -> hybrid
    with patch.dict("sys.modules", {"fastembed": fake_fastembed}):
        with patch(
            "oem_knowledge.engine.KnowledgeEngine.embedding_cache_ready",
            return_value=True,
        ):
            result = engine.search.resolve_retrieval_mode()
            assert result == "hybrid"

    # (b) fastembed importable + embedding cache NOT ready -> bm25
    with patch.dict("sys.modules", {"fastembed": fake_fastembed}):
        with patch(
            "oem_knowledge.engine.KnowledgeEngine.embedding_cache_ready",
            return_value=False,
        ):
            result = engine.search.resolve_retrieval_mode()
            assert result == "bm25"

    # (c) fastembed NOT importable -> bm25 (None in sys.modules raises ImportError)
    monkeypatch.setitem(sys.modules, "fastembed", None)
    result = engine.search.resolve_retrieval_mode()
    assert result == "bm25"

def test_resolve_auto_mode_bm25_without_fastembed(temp_project):
    engine = KnowledgeEngine(temp_project)
    saved = sys.modules.pop("fastembed", None)
    try:
        original_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "fastembed":
                raise ImportError("No module named fastembed")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=mock_import):
            result = engine.search.resolve_retrieval_mode()
            assert result == "bm25"
    finally:
        if saved is not None:
            sys.modules["fastembed"] = saved
