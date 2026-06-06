from __future__ import annotations
import json
import tempfile
import shutil
import sys
from pathlib import Path
import pytest
from unittest.mock import patch

from oem_knowledge.cli import main
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.context import _compile_oem_context

@pytest.fixture
def tmp_workspace():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)

def test_release_multi_project_isolation(tmp_workspace):
    # Create projects A, B, and C
    project_a = tmp_workspace / "project-a"
    project_b = tmp_workspace / "project-b"
    project_c = tmp_workspace / "project-c"
    
    project_a.mkdir()
    project_b.mkdir()
    project_c.mkdir()
    
    # Initialize all three projects via CLI
    with patch.object(sys, "argv", ["oem", "init", str(project_a)]):
        main()
    with patch.object(sys, "argv", ["oem", "init", str(project_b)]):
        main()
    with patch.object(sys, "argv", ["oem", "init", str(project_c)]):
        main()
        
    eng_a = KnowledgeEngine(str(project_a))
    eng_b = KnowledgeEngine(str(project_b))
    eng_c = KnowledgeEngine(str(project_c))
    
    # Seed PROJECT_A_SECRET in project-a registry
    reg_a = eng_a.state._load_registry()
    reg_a["concept_secret_a"] = {
        "concept_id": "concept_secret_a",
        "canonical_name": "PROJECT_A_SECRET",
        "status": "canonical"
    }
    eng_a.state._save_registry(reg_a)
    
    # Seed PROJECT_B_SECRET in project-b registry
    reg_b = eng_b.state._load_registry()
    reg_b["concept_secret_b"] = {
        "concept_id": "concept_secret_b",
        "canonical_name": "PROJECT_B_SECRET",
        "status": "canonical"
    }
    eng_b.state._save_registry(reg_b)
    
    # Seed PROJECT_C_SECRET in project-c registry
    reg_c = eng_c.state._load_registry()
    reg_c["concept_secret_c"] = {
        "concept_id": "concept_secret_c",
        "canonical_name": "PROJECT_C_SECRET",
        "status": "canonical"
    }
    eng_c.state._save_registry(reg_c)
    
    # Compile context for all three projects
    context_a = _compile_oem_context(eng_a)
    context_b = _compile_oem_context(eng_b)
    context_c = _compile_oem_context(eng_c)
    
    # Verify Project A context contains only its own secret and not B or C
    a_concepts = [c["name"] for c in context_a["active_concepts"]]
    assert "PROJECT_A_SECRET" in a_concepts
    assert "PROJECT_B_SECRET" not in a_concepts
    assert "PROJECT_C_SECRET" not in a_concepts
    
    # Verify Project B context contains only its own secret and not A or C
    b_concepts = [c["name"] for c in context_b["active_concepts"]]
    assert "PROJECT_B_SECRET" in b_concepts
    assert "PROJECT_A_SECRET" not in b_concepts
    assert "PROJECT_C_SECRET" not in b_concepts
    
    # Verify Project C context contains only its own secret and not A or B
    c_concepts = [c["name"] for c in context_c["active_concepts"]]
    assert "PROJECT_C_SECRET" in c_concepts
    assert "PROJECT_A_SECRET" not in c_concepts
    assert "PROJECT_B_SECRET" not in c_concepts
