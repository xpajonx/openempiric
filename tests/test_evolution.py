from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
import pytest

from harness_knowledge.engine import KnowledgeEngine, HARNESS_DIR
from harness_knowledge.evolution import ConceptEvolutionEngine, ContradictionDetector


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_concept_revision_logging(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = concepts_dir / "concept_test.md"
    
    initial_content = """---
concept_id: concept_test
canonical_name: concept-test
status: candidate
confidence: 1
---
# Concept Test
This is the initial version of the concept.
"""
    eng._safe_write_concept_file(file_path, initial_content, tmp_proj)
    
    history = eng.get_concept_history("concept_test", tmp_proj)
    assert len(history) == 1
    assert history[0]["concept_id"] == "concept_test"
    assert history[0]["diff"] == ""
    
    modified_content = """---
concept_id: concept_test
canonical_name: concept-test
status: emerging
confidence: 2
---
# Concept Test
This is the updated version of the concept with changes.
"""
    eng._safe_write_concept_file(file_path, modified_content, tmp_proj)
    
    history2 = eng.get_concept_history("concept_test", tmp_proj)
    assert len(history2) == 2
    assert history2[1]["concept_id"] == "concept_test"
    assert "updated version" in history2[1]["diff"]


def test_concept_evolution_engine(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"

    file_path = concepts_dir / "concept_001.md"
    content = """---
concept_id: concept_001
canonical_name: test-concept
status: validated
confidence: 3
evidence_count: 2
session_count: 1
aliases: []
---
# Test Concept

## Learnings
- **Observation**: Learn REST APIs are simple.
- **Observation**: Learn REST APIs are simple.
- **Validation**: REST APIs are very reliable.
"""
    file_path.write_text(content, encoding="utf-8")

    evolve_engine = ConceptEvolutionEngine(eng)
    res = evolve_engine.evolve_concept("concept_001", tmp_proj)

    assert res["status"] == "success"
    assert res["learnings_count"] == 2  # duplicate REST bullet removed

    updated_content = file_path.read_text(encoding="utf-8")
    assert updated_content.count("REST APIs are simple.") == 1


def test_contradiction_detection(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"

    # Add concept A advocating for REST
    file_a = concepts_dir / "concept_001.md"
    file_a.write_text("""---
concept_id: concept_001
canonical_name: protocol-rest
status: validated
confidence: 3
evidence_count: 1
---
# Protocol REST
We must use REST for communication.
""", encoding="utf-8")

    # Add concept B advocating for gRPC
    file_b = concepts_dir / "concept_002.md"
    file_b.write_text("""---
concept_id: concept_002
canonical_name: protocol-grpc
status: validated
confidence: 3
evidence_count: 1
---
# Protocol GRPC
We should use gRPC for communication.
""", encoding="utf-8")

    # Save to registry
    reg = eng._load_registry(tmp_proj)
    reg["concept_001"] = {"concept_id": "concept_001", "canonical_name": "protocol-rest"}
    reg["concept_002"] = {"concept_id": "concept_002", "canonical_name": "protocol-grpc"}
    eng._save_registry(reg, tmp_proj)

    detector = ContradictionDetector(eng)
    contradictions = detector.detect_contradictions(tmp_proj)

    assert len(contradictions) >= 1
    conflict = contradictions[0]
    assert conflict["type"] == "architectural_conflict"
    assert "REST vs gRPC" in conflict["description"]
