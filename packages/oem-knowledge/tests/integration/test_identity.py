from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.identity_resolver import SemanticIdentityResolver


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_semantic_identity_resolution(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    registry = eng.state._load_registry(tmp_proj)
    # Create two duplicate-like concepts
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "canonical_name": "database-migration-tool",
        "aliases": ["db migration", "schema upgrade"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 3
    }
    registry["concept_002"] = {
        "concept_id": "concept_002",
        "canonical_name": "db-schema-upgrader",
        "aliases": ["db migration", "schema migration"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 3
    }
    # A non-duplicate concept
    registry["concept_003"] = {
        "concept_id": "concept_003",
        "canonical_name": "authentication-handler",
        "aliases": ["user auth", "oauth"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 3
    }
    eng.state._save_registry(registry, tmp_proj)

    resolver = SemanticIdentityResolver(eng)
    duplicates = resolver.scan_duplicates(tmp_proj, threshold=0.70)

    # We expect concept_001 and concept_002 to be identified as potential duplicates
    assert len(duplicates) >= 1
    pair = duplicates[0]
    assert {pair["concept_a"], pair["concept_b"]} == {"concept_001", "concept_002"}


def test_semantic_identity_resolver_empty_registry(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    resolver = SemanticIdentityResolver(eng)
    
    # Empty registry should return empty list gracefully
    duplicates = resolver.scan_duplicates(tmp_proj)
    assert duplicates == []


def test_semantic_identity_resolver_extreme_thresholds(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    registry = eng.state._load_registry(tmp_proj)
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "canonical_name": "database-migration-tool",
        "aliases": ["db migration"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 3
    }
    registry["concept_002"] = {
        "concept_id": "concept_002",
        "canonical_name": "db-schema-upgrader",
        "aliases": ["db migration"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 3
    }
    eng.state._save_registry(registry, tmp_proj)
    resolver = SemanticIdentityResolver(eng)

    # Extreme threshold 1.0 (should not match unless identical embedding, unlikely for slightly different texts)
    duplicates_high = resolver.scan_duplicates(tmp_proj, threshold=1.0)
    assert len(duplicates_high) == 0

    # Extreme threshold 0.0 (should match everything)
    duplicates_low = resolver.scan_duplicates(tmp_proj, threshold=0.0)
    assert len(duplicates_low) == 1


def test_semantic_identity_resolver_missing_fields(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    registry = eng.state._load_registry(tmp_proj)
    # Missing canonical_name and aliases fields completely
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "status": "validated"
    }
    registry["concept_002"] = {
        "concept_id": "concept_002",
        "status": "validated"
    }
    eng.state._save_registry(registry, tmp_proj)
    resolver = SemanticIdentityResolver(eng)

    # Should not crash and return gracefully
    duplicates = resolver.scan_duplicates(tmp_proj, threshold=0.70)
    assert isinstance(duplicates, list)

