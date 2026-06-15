import json
import pytest
import shutil
import re
from pathlib import Path
from unittest.mock import patch
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.recovery import cmd_recover, diagnose_registry_drift

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine

def test_recover_dry_run_reports_orphan_wiki_concepts(temp_project):
    project_dir, engine = temp_project
    
    # 5 registry concepts
    reg = {}
    for i in range(1, 6):
        cid = f"concept_{i:03d}"
        reg[cid] = {
            "concept_id": cid,
            "canonical_name": f"concept-{i}",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 1
        }
    engine.state._save_registry(reg, str(project_dir))
    
    # Wiki concepts concept_006 through concept_022
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    for i in range(6, 23):
        cid = f"concept_{i:03d}"
        fp = wiki_dir / f"{cid}.md"
        fp.write_text(f"---\nconcept_id: {cid}\ncanonical_name: concept-{i}\nstatus: candidate\n---\nBody", encoding="utf-8")
        
    res = cmd_recover(engine, project=str(project_dir), scope="registry", dry_run=True)
    
    # Reports concept_006 through concept_022 as orphan wiki concepts
    assert len(res["orphans"]) == 17
    assert "concept_006" in res["orphans"]
    assert "concept_022" in res["orphans"]

def test_recover_dry_run_reports_registry_entries_missing_wiki_files(temp_project):
    project_dir, engine = temp_project
    
    # Registry has concept_001 through concept_005
    reg = {}
    for i in range(1, 6):
        cid = f"concept_{i:03d}"
        reg[cid] = {
            "concept_id": cid,
            "canonical_name": f"concept-{i}",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 1
        }
    engine.state._save_registry(reg, str(project_dir))
    
    res = cmd_recover(engine, project=str(project_dir), scope="registry", dry_run=True)
    
    # Reports concept_001 through concept_005 as missing wiki files
    assert len(res["missing_wiki"]) == 5
    assert "concept_001" in res["missing_wiki"]
    assert "concept_005" in res["missing_wiki"]

def test_recover_dry_run_does_not_claim_clean_when_registry_wiki_drift_exists(temp_project, capsys):
    project_dir, engine = temp_project
    
    # Create orphan wiki concept file
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_006.md").write_text("Test", encoding="utf-8")
    
    # Run recovery dry-run under default scope
    cmd_recover(engine, project=str(project_dir), scope=None, dry_run=True)
    
    captured = capsys.readouterr()
    assert "NOT CLEAN" in captured.out
    assert "No unfinished sessions detected" in captured.out

def test_recover_apply_backs_up_before_registry_mutation(temp_project):
    project_dir, engine = temp_project
    
    # Save a concept registry first so it exists to be backed up
    engine.state._save_registry({"concept_001": {"concept_id": "concept_001", "canonical_name": "one", "status": "validated"}}, str(project_dir))
    
    # Create orphan wiki concept file
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_006.md").write_text("Test", encoding="utf-8")
    
    # Run apply with backup=True
    res = cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=True)
    
    assert res["backup_dir"] is not None
    backup_path = Path(res["backup_dir"])
    assert backup_path.exists()
    assert (backup_path / "concept_registry.json").exists()

def test_recover_apply_reattaches_parseable_orphan_wiki_concepts(temp_project):
    project_dir, engine = temp_project
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    
    # Case A: Frontmatter parseable
    (wiki_dir / "concept_006.md").write_text("---\nconcept_id: concept_006\ncanonical_name: parseable-six\nstatus: emerging\n---\nSome body", encoding="utf-8")
    
    # Case B: First H1 heading fallback
    (wiki_dir / "concept_007.md").write_text("\n\n# Heading Seven\nSome body", encoding="utf-8")
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    reg = engine.state._load_registry(str(project_dir))
    assert "concept_006" in reg
    assert reg["concept_006"]["canonical_name"] == "parseable-six"
    assert reg["concept_006"]["status"] == "emerging"
    
    assert "concept_007" in reg
    assert reg["concept_007"]["canonical_name"] == "heading-seven"
    assert reg["concept_007"]["status"] == "unmanaged"

def test_recover_apply_marks_ambiguous_orphans_for_manual_review(temp_project):
    project_dir, engine = temp_project
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_006.md").write_text("No frontmatter and no H1 heading", encoding="utf-8")
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    reg = engine.state._load_registry(str(project_dir))
    assert "concept_006" in reg
    assert reg["concept_006"]["status"] == "unmanaged"
    assert reg["concept_006"]["recovery_status"] == "manual_review_required"

def test_recover_apply_does_not_delete_wiki_files(temp_project):
    project_dir, engine = temp_project
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_006.md").write_text("Sentinel", encoding="utf-8")
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    assert (wiki_dir / "concept_006.md").exists()
    assert (wiki_dir / "concept_006.md").read_text(encoding="utf-8") == "Sentinel"

def test_recover_apply_does_not_delete_registry_entries(temp_project):
    project_dir, engine = temp_project
    
    reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "one",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 1
        }
    }
    engine.state._save_registry(reg, str(project_dir))
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    reg_after = engine.state._load_registry(str(project_dir))
    assert "concept_001" in reg_after
    assert reg_after["concept_001"]["status"] == "missing_file"

def test_recover_apply_preserves_existing_registry_metadata(temp_project):
    project_dir, engine = temp_project
    
    reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "one",
            "status": "validated",
            "confidence": 5,
            "evidence_count": 12,
            "sessions": ["session_alpha"]
        }
    }
    engine.state._save_registry(reg, str(project_dir))
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    reg_after = engine.state._load_registry(str(project_dir))
    assert reg_after["concept_001"]["confidence"] == 5
    assert reg_after["concept_001"]["evidence_count"] == 12
    assert reg_after["concept_001"]["sessions"] == ["session_alpha"]

def test_recover_apply_validates_registry_json(temp_project):
    project_dir, engine = temp_project
    
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_006.md").write_text("Orphan", encoding="utf-8")
    
    # Mock save_registry to write invalid json text to bypass normal serializer
    def mock_save(registry, project, lock=True):
        p = engine._registry_path(project)
        p.write_text("{invalid json", encoding="utf-8")
        
    with patch.object(engine.state, "_save_registry", mock_save):
        from oem_knowledge.runtime.recovery import recover_registry
        with pytest.raises(RuntimeError) as exc:
            recover_registry(engine, project=str(project_dir), apply=True, backup=False)
        assert "validation failed" in str(exc.value)

def test_allocator_after_recovery_uses_next_id_after_highest_wiki_file(temp_project):
    project_dir, engine = temp_project
    
    # Wiki has concept_022.md
    wiki_dir = engine._concepts_dir(str(project_dir))
    wiki_dir.mkdir(parents=True, exist_ok=True)
    (wiki_dir / "concept_022.md").write_text("Drift", encoding="utf-8")
    
    cmd_recover(engine, project=str(project_dir), scope="registry", apply=True, backup=False)
    
    # Allocate new concept
    from oem_knowledge.concept_id import allocate_concept_id
    registry = engine.state._load_registry(str(project_dir))
    next_id = allocate_concept_id(registry, wiki_dir)
    
    assert next_id == "concept_023"

def test_recover_reports_duplicate_concept_titles(temp_project):
    project_dir, engine = temp_project
    
    reg = {
        "concept_001": {
            "concept_id": "concept_001",
            "canonical_name": "duplicate-title",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 1
        },
        "concept_002": {
            "concept_id": "concept_002",
            "canonical_name": "duplicate-title",
            "status": "validated",
            "confidence": 3,
            "evidence_count": 1
        }
    }
    engine.state._save_registry(reg, str(project_dir))
    
    res = cmd_recover(engine, project=str(project_dir), scope="registry", dry_run=True)
    assert len(res["duplicates"]) > 0
    assert "duplicate-title" in res["duplicates"]
