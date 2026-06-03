import json
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine


def test_positive_and_negative_consolidation(tmp_path):
    # 1. Initialize engine on temporary path
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_consolidate_proj")

    # Access registry and concepts directory
    registry = engine._load_registry()
    concepts_dir = engine._concepts_dir()

    # 2. Add two duplicate concepts with lexical overlap (should merge)
    # primary: concept_001 (higher quality: status validated, evidence count 5)
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "canonical_name": "python-linter-rules",
        "aliases": ["python linter rules", "linting python"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 5,
        "sessions": ["session_1"],
    }
    # secondary: concept_002 (lower quality: status candidate, evidence count 1)
    registry["concept_002"] = {
        "concept_id": "concept_002",
        "canonical_name": "python-linter-guidelines",
        "aliases": ["python linter guidelines"],
        "status": "candidate",
        "confidence": 1,
        "evidence_count": 1,
        "sessions": ["session_1"],
    }

    # Write files for duplicate concepts
    (concepts_dir / "concept_001.md").write_text("# Python Linter Rules\nSome rules.\n", encoding="utf-8")
    (concepts_dir / "concept_002.md").write_text("# Python Linter Guidelines\nSome guidelines.\n", encoding="utf-8")

    # 3. Add two completely different concepts (should not merge)
    registry["concept_003"] = {
        "concept_id": "concept_003",
        "canonical_name": "rust-compiler-errors",
        "aliases": ["rust compiler errors"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 2,
        "sessions": ["session_1"],
    }
    (concepts_dir / "concept_003.md").write_text("# Rust Compiler Errors\nRust error details.\n", encoding="utf-8")

    # 4. Add two concepts that are semantically close but share NO common words (fails 2nd validation)
    # (For bge-small-en-v1.5, "artificial intelligence safety guidelines" and "machine learning ethical regulations"
    # have high similarity but share no words and sequence matcher ratio < 0.4)
    registry["concept_004"] = {
        "concept_id": "concept_004",
        "canonical_name": "artificial-intelligence-safety-rules",
        "aliases": ["artificial intelligence safety rules"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 2,
        "sessions": ["session_1"],
    }
    registry["concept_005"] = {
        "concept_id": "concept_005",
        "canonical_name": "machine-learning-ethical-regulations",
        "aliases": ["machine learning ethical regulations"],
        "status": "validated",
        "confidence": 3,
        "evidence_count": 2,
        "sessions": ["session_1"],
    }
    (concepts_dir / "concept_004.md").write_text("# Artificial Intelligence Safety Rules\nAI safety rules.\n", encoding="utf-8")
    (concepts_dir / "concept_005.md").write_text("# Machine Learning Ethical Regulations\nML ethics.\n", encoding="utf-8")

    # Save registry to simulate actual state
    engine._save_registry(registry)

    # Index all to sync vector db
    engine.index_all(force=True)

    # 5. Run consolidation
    res = engine.consolidate()
    assert res["status"] == "success"

    # Verify duplicates merged (concept_002 merged into concept_001)
    assert "Merged concept_002 -> concept_001" in res["merged"] or "Merged concept_001 -> concept_002" in res["merged"]
    
    # Reload registry
    registry_after = engine._load_registry()
    assert "concept_002" not in registry_after
    assert "concept_001" in registry_after
    
    # Verify primary concept has higher quality rules (status 'validated' and evidence count 5)
    # concept_001 is validated (status rank 4, ev_count 5) vs concept_002 candidate (status rank 2, ev_count 1),
    # so concept_001 should be the primary keeper.
    assert registry_after["concept_001"]["canonical_name"] == "python-linter-rules"

    # Verify that different concepts were NOT merged
    assert "concept_003" in registry_after

    # Verify that semantic similarity with no lexical overlap was NOT merged due to 2nd validation
    assert "concept_004" in registry_after
    assert "concept_005" in registry_after
