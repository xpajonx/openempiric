import json
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.recovery import cmd_recover

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_recover_dry_run_detects_orphan_wiki_concept(temp_project):
    project_dir, engine = temp_project
    
    # Create orphan wiki concept file
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_001.md"
    orphan_file.write_text("---\nconcept_id: concept_001\ncanonical_name: orphan-concept\nstatus: materialized\n---\nSome text", encoding="utf-8")
    
    # Run recovery in dry-run mode
    res = cmd_recover(engine, project=str(project_dir), scope="registry", dry_run=True)
    
    assert "concept_001" in res["orphans"]
    assert "concept_001" not in engine.state._load_registry(str(project_dir))

def test_recover_apply_reattaches_orphan_wiki_when_safe(temp_project):
    project_dir, engine = temp_project
    
    # Create orphan wiki concept file with metadata
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_001.md"
    orphan_file.write_text("---\nconcept_id: concept_001\ncanonical_name: orphan-concept\nstatus: materialized\nconfidence: 3\nevidence_count: 5\n---\nSome text", encoding="utf-8")
    
    # Run recovery apply
    res = cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    assert "concept_001" in res["orphans"]
    reg = engine.state._load_registry(str(project_dir))
    assert "concept_001" in reg
    assert reg["concept_001"]["canonical_name"] == "orphan-concept"
    assert reg["concept_001"]["status"] == "materialized"
    assert reg["concept_001"]["confidence"] == 3
    assert reg["concept_001"]["evidence_count"] == 5

def test_recover_apply_does_not_delete_or_overwrite_wiki_files(temp_project):
    project_dir, engine = temp_project
    
    # Create orphan wiki concept file
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_001.md"
    content = "---\nconcept_id: concept_001\ncanonical_name: orphan-concept\nstatus: materialized\n---\nSome unique text content."
    orphan_file.write_text(content, encoding="utf-8")
    
    # Run recovery apply
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    # Verify file is intact and not modified or deleted
    assert orphan_file.exists()
    assert orphan_file.read_text(encoding="utf-8") == content

def test_recover_after_concept_id_collision_allows_next_session_end(temp_project):
    project_dir, engine = temp_project
    
    # 1. Simulate drift collision setup: concept_001.md exists on disk as orphan, but not in registry.
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = wiki_dir / "concept_001.md"
    orphan_file.write_text("---\nconcept_id: concept_001\ncanonical_name: first\nstatus: materialized\n---\nFirst content", encoding="utf-8")
    
    # 2. Run recover to reattach the concept_001.md
    res_recover = cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    assert "concept_001" in res_recover["orphans"]
    
    # 3. Add a new event that should trigger materialization of a new concept "second"
    sessions_dir = engine._sessions_dir(str(project_dir))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "sess_1.md"
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "second", "evidence": "e1"},
            {"type": "observation", "concept": "second", "evidence": "e2"},
            {"type": "observation", "concept": "second", "evidence": "e3"}
        ]
    }) + "\n```"
    report_file.write_text(report_content, encoding="utf-8")
    
    # 4. Materialization should now allocate concept_002 safely
    res_mat = engine.materialization.materialize_concepts(str(project_dir))
    assert res_mat["status"] == "success"
    
    reg = engine.state._load_registry(str(project_dir))
    assert "concept_001" in reg
    assert "concept_002" in reg
    assert reg["concept_002"]["canonical_name"] == "second"
    assert (wiki_dir / "concept_002.md").exists()
