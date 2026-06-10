import pytest
from pathlib import Path
from oem_knowledge.concept_id import allocate_concept_id

def test_allocate_concept_id_uses_max_existing_registry_id():
    """Verify that allocation starts based on the max existing registry ID, not len(registry)."""
    registry = {
        "concept_001": {},
        "concept_004": {}
    }
    # Should allocate concept_005 since 4 is the maximum
    result = allocate_concept_id(registry=registry)
    assert result == "concept_005"

def test_allocate_concept_id_considers_orphan_wiki_files(tmp_path):
    """Verify that orphan wiki files are scanned and block their IDs from being allocated."""
    registry = {
        "concept_001": {}
    }
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    
    # Create an orphan wiki file concept_002.md
    (wiki_dir / "concept_002.md").write_text("content", encoding="utf-8")
    
    # Should allocate concept_003 because concept_002 exists on disk
    result = allocate_concept_id(registry=registry, wiki_dir=wiki_dir)
    assert result == "concept_003"

def test_allocate_concept_id_considers_reserved_ids():
    """Verify that reserved/in-flight IDs from the same run are not reused."""
    registry = {
        "concept_001": {}
    }
    reserved = {"concept_002"}
    
    # Should allocate concept_003 because concept_002 is reserved
    result = allocate_concept_id(registry=registry, reserved_ids=reserved)
    assert result == "concept_003"

def test_allocate_concept_id_ignores_non_numeric_for_sequence():
    """Verify that non-numeric/custom IDs are ignored for numeric sequence calculation but occupied."""
    registry = {
        "concept_001": {},
        "concept_custom": {}
    }
    # Should allocate concept_002 (ignoring concept_custom for sequencing)
    result = allocate_concept_id(registry=registry)
    assert result == "concept_002"

def test_allocate_concept_id_never_returns_existing_exact_id():
    """Verify that the allocator loops to avoid any exact collisions, even with custom IDs."""
    registry = {
        "concept_001": {},
        "concept_002": {}
    }
    reserved = {"concept_003"}
    
    # If custom ID concept_004 exists, allocator should skip it and return concept_005
    registry["concept_004"] = {}
    result = allocate_concept_id(registry=registry, reserved_ids=reserved)
    assert result == "concept_005"
