from __future__ import annotations
import json
import time
from pathlib import Path
import pytest
from oem_knowledge.engine import KnowledgeEngine

@pytest.fixture
def temp_project(tmp_path):
    eng = KnowledgeEngine(tmp_path)
    eng.init_project(str(tmp_path))
    return eng, tmp_path

def test_noop_commit_does_not_reembed_everything(temp_project):
    engine, tmp_path = temp_project

    # Create a test concept file
    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    cfile = concepts_dir / "concept_abc.md"
    cfile.write_text("""---
concept_id: concept_abc
canonical_name: test-incremental
status: validated
confidence: 3
evidence_count: 1
session_count: 1
aliases: []
---
# Test Incremental

Hello world learning text.""", encoding="utf-8")

    # Initial index build (force=True)
    res_init = engine.search.index_all(force=True)
    assert res_init["new"] > 0
    assert res_init["unchanged"] == 0

    # Second build without force (no-op)
    res_noop = engine.search.index_all(force=False)
    assert res_noop["new"] == 0
    assert res_noop["updated"] == 0
    assert res_noop["unchanged"] > 0

def test_incremental_index_updates_changed_docs_only(temp_project):
    engine, tmp_path = temp_project

    # Create two concepts
    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    c1 = concepts_dir / "concept_001.md"
    c1.write_text("---\nconcept_id: concept_001\ncanonical_name: first-concept\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# First\nHello.", encoding="utf-8")
    
    c2 = concepts_dir / "concept_002.md"
    c2.write_text("---\nconcept_id: concept_002\ncanonical_name: second-concept\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# Second\nWorld.", encoding="utf-8")

    # Build initial index
    engine.search.index_all(force=True)

    # Modify ONLY concept_002
    c2.write_text("---\nconcept_id: concept_002\ncanonical_name: second-concept\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# Second\nWorld Modified Content.", encoding="utf-8")

    # Incremental update
    res = engine.search.index_all(force=False)
    
    # Verify only concept_002 was updated, and concept_001 was unchanged
    assert res["new"] == 0
    assert res["updated"] == 1
    assert res["unchanged"] >= 1

def test_search_results_stable_after_incremental_update(temp_project):
    engine, tmp_path = temp_project

    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    cfile = concepts_dir / "concept_abc.md"
    cfile.write_text("""---
concept_id: concept_abc
canonical_name: search-stability
status: validated
confidence: 3
evidence_count: 1
session_count: 1
aliases: []
---
# Search Stability

This document has unique keywords like antigravityengine.""", encoding="utf-8")

    # Initial index
    engine.search.index_all(force=True)
    
    # Query before update
    results_before = engine.search.search("antigravityengine", k=1)
    assert len(results_before) == 1
    doc_before = results_before[0]["document"]

    # Trigger no-op incremental index
    engine.search.index_all(force=False)

    # Query after update
    results_after = engine.search.search("antigravityengine", k=1)
    assert len(results_after) == 1
    doc_after = results_after[0]["document"]

    # Verify document and scores are identical/stable
    assert doc_before == doc_after
    assert results_before[0]["score"] == pytest.approx(results_after[0]["score"])

def test_deleted_files_are_pruned_from_index(temp_project):
    engine, tmp_path = temp_project

    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    cfile = concepts_dir / "concept_to_delete.md"
    cfile.write_text("""---
concept_id: concept_to_delete
canonical_name: to-delete
status: validated
confidence: 3
evidence_count: 1
session_count: 1
aliases: []
---
# To Delete

This has keywords like deletableitem.""", encoding="utf-8")

    # Index
    engine.search.index_all(force=True)
    
    # Verify it exists in search
    results_before = engine.search.search("deletableitem", k=1)
    assert len(results_before) == 1

    # Delete the file
    cfile.unlink()

    # Incremental update should prune it
    engine.search.index_all(force=False)

    # Verify search no longer finds it
    results_after = engine.search.search("deletableitem", k=1)
    # The collection search might fall back or return empty
    assert len(results_after) == 0 or "deletableitem" not in results_after[0]["document"]

    # Verify it is removed from the file registry JSON
    reg_path = tmp_path / ".oem" / "state" / "file_registry.json"
    assert reg_path.exists()
    registry = json.loads(reg_path.read_text())
    rel_cfile = str(cfile.relative_to(tmp_path))
    assert rel_cfile not in registry


def test_retrieval_mode_resolves_to_bm25_by_default(temp_project):
    """Verify that auto retrieval mode resolves to bm25 by default."""
    engine, _ = temp_project
    assert engine.search.get_retrieval_mode() == "auto"
    assert engine.search.resolve_retrieval_mode() == "bm25"


def test_incremental_indexing_statistics(temp_project):
    """Verify chunk-level indexing stats (new_chunks, updated_chunks, unchanged_chunks)."""
    engine, tmp_path = temp_project

    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Create first document
    c1 = concepts_dir / "concept_001.md"
    c1.write_text("---\nconcept_id: concept_001\ncanonical_name: test-one\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# Test One\nHello context.", encoding="utf-8")

    # Index first document
    stats1 = engine.search.index_all(force=True)
    assert stats1["new_chunks"] > 0
    assert stats1["updated_chunks"] == 0
    assert stats1["unchanged_chunks"] == 0
    assert stats1["index_time_ms"] >= 0

    # 2. Re-index without modifications (no-op)
    stats2 = engine.search.index_all(force=False)
    assert stats2["new_chunks"] == 0
    assert stats2["updated_chunks"] == 0
    assert stats2["unchanged_chunks"] == stats1["new_chunks"]

    # 3. Update the document
    c1.write_text("---\nconcept_id: concept_001\ncanonical_name: test-one\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# Test One\nHello context modified.", encoding="utf-8")
    stats3 = engine.search.index_all(force=False)
    assert stats3["new_chunks"] == 0
    assert stats3["updated_chunks"] > 0
    assert stats3["unchanged_chunks"] > 0


def test_one_time_migration_warning(temp_project, capsys):
    """Verify that old absolute path keys trigger a one-time migration warning and full reindex."""
    engine, tmp_path = temp_project
    concepts_dir = tmp_path / ".oem" / "wiki" / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    c1 = concepts_dir / "concept_001.md"
    c1.write_text("---\nconcept_id: concept_001\ncanonical_name: test-one\nstatus: validated\nconfidence: 3\nevidence_count: 1\nsession_count: 1\naliases: []\n---\n# Test One\nHello.", encoding="utf-8")

    # Initial index
    engine.search.index_all(force=False)

    # Manually overwrite the registry to simulate old absolute paths
    reg_path = tmp_path / ".oem" / "state" / "file_registry.json"
    registry = {
        str(c1.resolve()): "somehash"
    }
    reg_path.write_text(json.dumps(registry), encoding="utf-8")

    # Run index_all again; it should detect absolute path and print migration message
    stats = engine.search.index_all(force=False)
    captured = capsys.readouterr()
    assert "Migrating search index registry to relative paths" in captured.out
    assert stats["new"] > 0 or stats["updated"] > 0

