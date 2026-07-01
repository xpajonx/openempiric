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
    try:
        engine.search.vector_store.close()
    except Exception:
        pass
    try:
        shutil.rmtree(project_dir)
    except FileNotFoundError:
        pass


def test_oem_wiki_chunks_remain_indexed_with_ingestion_metadata(temp_project):
    engine = KnowledgeEngine(temp_project)

    stats = engine.search.index_all(force=True)

    assert stats["status"] == "success"
    chunks = engine.search.vector_store.all_chunks()
    wiki_chunks = [
        chunk for chunk in chunks
        if chunk["metadata"].get("rel_path", "").startswith(".oem/wiki/")
    ]
    assert wiki_chunks

    wiki_meta = wiki_chunks[0]["metadata"]
    assert wiki_meta["source_type"] == "oem_wiki"
    assert wiki_meta["ingestion_eligible"] is False
    assert wiki_meta["source_path"].startswith(".oem/wiki/")

    results = engine.search.search("LinkedConcept", k=3, hybrid=False)
    assert any(
        result["metadata"].get("rel_path", "").startswith(".oem/wiki/")
        for result in results
    )

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


# ---------------------------------------------------------------------------
# Chunking + frontmatter integration
# ---------------------------------------------------------------------------


def test_chunker_does_not_reparse_body_chunks_as_frontmatter(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    wiki_dir = engine._concepts_dir(str(temp_project))
    wiki_dir.mkdir(parents=True, exist_ok=True)

    concept_path = wiki_dir / "concept_010.md"
    concept_path.write_text(
        """---
concept_id: concept_010
status: validated
---

# Learnings

- Decision: use SQLite for local vector store

---

Another section with a horizontal rule in body.

```text
---
code block with dashes
---
```""",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        stats = engine.search.index_all(force=True)
        assert stats["status"] == "success"

    chunks = engine.search.vector_store.all_chunks()
    wiki_chunks = [
        c for c in chunks
        if c["metadata"].get("rel_path", "").endswith("concept_010.md")
    ]
    assert len(wiki_chunks) > 0

    # No chunk should contain the frontmatter YAML
    for c in wiki_chunks:
        doc_text = c.get("document", "")
        assert "concept_id: concept_010" not in doc_text
        assert "status: validated" not in doc_text

    # Body horizontal rules and code blocks must be preserved
    body_texts = [c.get("document", "") for c in wiki_chunks]
    combined = "\n".join(body_texts)
    assert "Another section with a horizontal rule" in combined
    assert "code block with dashes" in combined

    # No false frontmatter warnings from chunking
    chunk_warnings = [
        r.message for r in caplog.records
        if "Frontmatter warning" in r.message and "concept_010" in r.message
    ]
    assert len(chunk_warnings) == 0


def test_concept_metadata_inherited_by_chunks(temp_project):
    engine = KnowledgeEngine(temp_project)
    wiki_dir = engine._concepts_dir(str(temp_project))
    wiki_dir.mkdir(parents=True, exist_ok=True)

    concept_path = wiki_dir / "concept_050.md"
    concept_path.write_text(
        """---
concept_id: concept_050
status: validated
confidence: 3
---

# Testing Metadata Inheritance

This concept tests metadata propagation to chunks.
""",
        encoding="utf-8",
    )

    engine.search.index_all(force=True)
    chunks = engine.search.vector_store.all_chunks()
    wiki_chunks = [
        c for c in chunks
        if c["metadata"].get("rel_path", "").endswith("concept_050.md")
    ]
    assert len(wiki_chunks) > 0

    for c in wiki_chunks:
        meta = c["metadata"]
        assert meta.get("concept_id") == "concept_050"
        assert meta.get("concept_status") == "validated"


def test_malformed_frontmatter_warning_emitted_once_per_file(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    wiki_dir = engine._concepts_dir(str(temp_project))
    wiki_dir.mkdir(parents=True, exist_ok=True)

    concept_path = wiki_dir / "concept_bad_fm.md"
    concept_path.write_text(
        """---
concept_id: concept_bad_fm
status: validated

# Missing closing delimiter

## Section A
Content.
""",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        stats = engine.search.index_all(force=True)

    # Chunking still proceeds
    chunks = engine.search.vector_store.all_chunks()
    wiki_chunks = [
        c for c in chunks
        if c["metadata"].get("rel_path", "").endswith("concept_bad_fm.md")
    ]
    assert len(wiki_chunks) > 0

    # Warning emitted exactly once for this file
    chunk_warnings = [
        r.message for r in caplog.records
        if "Frontmatter warning" in r.message and "concept_bad_fm" in r.message
    ]
    assert len(chunk_warnings) == 1


def test_retrieval_preserves_decision_failure_observation_chunks_from_large_concept(temp_project):
    engine = KnowledgeEngine(temp_project)
    wiki_dir = engine._concepts_dir(str(temp_project))
    wiki_dir.mkdir(parents=True, exist_ok=True)

    entries = "\n".join(
        f"- **{kind}**: Entry {i} about {kind.lower()} patterns in the system.\n"
        for kind in ("Decision", "Failure", "Observation")
        for i in range(20)
    )

    concept_path = wiki_dir / "concept_099.md"
    concept_path.write_text(
        f"""---
concept_id: concept_099
status: validated
---

# Large Concept

{entries}

## Summary

This concept stores many learnings.
""",
        encoding="utf-8",
    )

    stats = engine.search.index_all(force=True)
    assert stats["status"] == "success"

    # All chunks retrievable
    chunks = engine.search.vector_store.all_chunks()
    wiki_chunks = [
        c for c in chunks
        if c["metadata"].get("rel_path", "").endswith("concept_099.md")
    ]
    # Should have at least 2 chunks (Introduction + Summary)
    assert len(wiki_chunks) >= 2

    # Verify Decision, Failure, Observation entries exist in chunks
    combined = "\n".join(c.get("document", "") for c in wiki_chunks)
    assert "**Decision**: Entry 0" in combined
    assert "**Failure**: Entry 5" in combined
    assert "**Observation**: Entry 10" in combined

    # Frontmatter must not appear in any chunk
    assert "concept_id: concept_099" not in combined


def test_record_concept_references_is_best_effort_for_search(temp_project):
    engine = KnowledgeEngine(temp_project)
    candidates = [
        {
            "id": "concept_001#chunk_0",
            "document": "Document: .oem/wiki/concept_001.md\n\nAI safety notes",
            "metadata": {"concept_id": "concept_001", "rel_path": ".oem/wiki/concept_001.md"},
            "score": 1.0,
        }
    ]

    with patch.object(engine.search, "_collect_raw_candidates", return_value=candidates):
        with patch("oem_knowledge.services.search.rank_search_results", return_value=candidates):
            with patch.object(engine.state, "record_concept_references", side_effect=RuntimeError("lock failed")):
                results = engine.search.search("AI safety", k=1, hybrid=False)

    assert len(results) == 1
    assert results[0]["metadata"]["concept_id"] == "concept_001"


def test_search_records_only_returned_results_not_raw_candidates(temp_project):
    engine = KnowledgeEngine(temp_project)
    candidates = [
        {
            "id": "concept_001#chunk_0",
            "document": "Document: .oem/wiki/concept_001.md\n\nFirst returned concept",
            "metadata": {"concept_id": "concept_001", "rel_path": ".oem/wiki/concept_001.md"},
            "score": 1.0,
        },
        {
            "id": "concept_002#chunk_0",
            "document": "Document: .oem/wiki/concept_002.md\n\nRaw candidate only",
            "metadata": {"concept_id": "concept_002", "rel_path": ".oem/wiki/concept_002.md"},
            "score": 0.9,
        },
    ]
    recorded = []

    def capture(concept_ids, **kwargs):
        recorded.extend(concept_ids)
        return {"status": "success", "updated": len(concept_ids), "concept_ids": list(concept_ids)}

    with patch.object(engine.search, "_collect_raw_candidates", return_value=candidates):
        with patch("oem_knowledge.services.search.rank_search_results", return_value=candidates):
            with patch.object(engine.state, "record_concept_references", side_effect=capture):
                results = engine.search.search("returned", k=1, hybrid=False)

    assert [r["metadata"]["concept_id"] for r in results] == ["concept_001"]
    assert recorded == ["concept_001"]


def test_debug_ranking_does_not_update_reference_metadata(temp_project):
    engine = KnowledgeEngine(temp_project)
    candidates = [
        {
            "id": "concept_001#chunk_0",
            "document": "Document: .oem/wiki/concept_001.md\n\nDebug concept",
            "metadata": {"concept_id": "concept_001", "rel_path": ".oem/wiki/concept_001.md"},
            "score": 1.0,
        }
    ]

    with patch.object(engine.search, "_collect_raw_candidates", return_value=candidates):
        with patch.object(engine.state, "record_concept_references") as record_refs:
            report = engine.search.debug_ranking("debug concept", k=1, hybrid=False)

    assert report["candidate_pool_size"] == 1
    record_refs.assert_not_called()


def test_record_concept_references_never_raises_to_search(temp_project):
    engine = KnowledgeEngine(temp_project)
    candidates = [
        {
            "id": "concept_001#chunk_0",
            "document": "Document: .oem/wiki/concept_001.md\n\nAI safety notes",
            "metadata": {"concept_id": "concept_001", "rel_path": ".oem/wiki/concept_001.md"},
            "score": 1.0,
        }
    ]

    # record_concept_references now handles errors internally,
    # so even a lock failure should never propagate to search
    with patch.object(engine.search, "_collect_raw_candidates", return_value=candidates):
        with patch("oem_knowledge.services.search.rank_search_results", return_value=candidates):
            with patch.object(
                engine.state, "record_concept_references",
                return_value={"status": "error", "updated": 0, "concept_ids": [], "error": "simulated failure"},
            ):
                results = engine.search.search("AI safety", k=1, hybrid=False)

    assert len(results) == 1
    assert results[0]["metadata"]["concept_id"] == "concept_001"
