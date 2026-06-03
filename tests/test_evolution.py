from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
import pytest

from harness_knowledge.engine import KnowledgeEngine, HARNESS_DIR


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
    
    # 1. First write (initial revision)
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
    assert history[0]["diff"] == ""  # No diff on first revision
    
    # 2. Second write (modified revision)
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
