import pytest
import shutil
import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.search import SearchService

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    
    # Create test markdown files in the actual concepts/wiki directory
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept1.md").write_text("# Concept 1\n\nSome body text [[LinkedConcept]]")
    (wiki_dir / "concept2.md").write_text("# Concept 2\n\nSome other text")
    
    yield project_dir
    shutil.rmtree(project_dir)

def test_vector_store_write_failure_during_index_all(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Mock upsert_batch on vector_store to raise sqlite3.Error
    store = engine.search.vector_store
    with patch.object(store, "upsert_batch", side_effect=sqlite3.Error("Disk full / DB Locked")):
        with caplog.at_level(logging.ERROR):
            stats = engine.search.index_all(force=True)
            assert stats["status"] == "error"
            assert "Disk full / DB Locked" in stats["error"]
            assert any("Batch upsert error" in r.message for r in caplog.records)

def test_search_fallback_logs_warning_on_primary_retrieval_failure(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Pre-populate some concept in registry so fallback has something to search
    reg_path = engine.state.engine._registry_path(str(temp_project))
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps({
        "concept_001": {
            "canonical_name": "ai-safety",
            "aliases": ["artificial intelligence safety"],
            "status": "validated",
            "confidence": 3,
            "evidence_count": 2
        }
    }))
    
    # Mock all_chunks to raise exception
    store = engine.search.vector_store
    with patch.object(store, "all_chunks", side_effect=sqlite3.Error("Mock read error")):
        with caplog.at_level(logging.WARNING):
            results = engine.search.search("ai-safety", k=3)
            # Should fallback to registry search
            assert len(results) > 0
            assert results[0]["metadata"]["title"] == "ai-safety"
            
            # Verify warning was logged
            warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
            assert any("Vector database search failed, falling back to registry-only" in w for w in warnings)

def test_session_commit_reports_partial_if_index_update_partial(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # Mock index_all to return partial status
    with patch.object(engine.search, "index_all", return_value={"status": "partial", "error": "Mock partial error"}):
        res = engine.session_commit(
            str(temp_project),
            conversation_text="Hypothesis: AI safety is important",
            session_id="session_test_1"
        )
        assert res["status"] == "partial"
        assert any("indexing failed: Mock partial error" in w for w in res["warnings"])

def test_session_commit_reports_error_if_index_update_fails(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # Mock index_all to return error status
    with patch.object(engine.search, "index_all", return_value={"status": "error", "error": "Mock DB error"}):
        res = engine.session_commit(
            str(temp_project),
            conversation_text="Hypothesis: AI safety is important",
            session_id="session_test_2"
        )
        assert res["status"] == "error"
        assert res["failed_step"] == "indexing"
        assert "Mock DB error" in res["message"]

def test_failed_files_and_chunks_counted_in_index_stats(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Mock chunk_markdown to raise exception for concept1.md
    original_chunk_markdown = engine.search.chunk_markdown
    def side_effect(fp, rel_path):
        if "concept1.md" in str(fp):
            raise OSError("Mock read failure")
        return original_chunk_markdown(fp, rel_path)
        
    with patch.object(engine.search, "chunk_markdown", side_effect=side_effect):
        with caplog.at_level(logging.WARNING):
            stats = engine.search.index_all(force=True)
            assert stats["status"] == "partial"
            assert stats["failed"] == 1
            assert any("concept1.md" in f for f in stats["failed_files"])
            assert any("Failed to chunk file" in r.message for r in caplog.records)
