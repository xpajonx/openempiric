from __future__ import annotations

import tempfile
import shutil
from pathlib import Path
import pytest

from harness_knowledge.vault import GlobalVault
from harness_knowledge.engine import KnowledgeEngine, HARNESS_DIR


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def tmp_vault_dir():
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d)


def test_global_vault_syndication(tmp_vault_dir):
    vault = GlobalVault(vault_dir=tmp_vault_dir)
    
    concept_data = {
        "concept_id": "global_auth",
        "canonical_name": "global-auth",
        "aliases": ["auth", "login"],
        "status": "canonical"
    }
    wiki_content = "# Global Auth\nThis concept is global."
    
    vault.syndicate_concept("global_auth", concept_data, wiki_content)
    
    registry = vault._load_registry()
    assert "global_auth" in registry
    assert registry["global_auth"]["canonical_name"] == "global-auth"
    
    wiki_file = tmp_vault_dir / "wiki" / "global_auth.md"
    assert wiki_file.is_file()
    assert wiki_file.read_text(encoding="utf-8") == wiki_content
    
    context = vault.get_global_context()
    assert len(context) == 1
    assert context[0]["concept_id"] == "global_auth"
    assert context[0]["canonical_name"] == "global-auth"
    assert context[0]["wiki_path"] == str(wiki_file)


def test_local_registry_sync(tmp_proj, tmp_vault_dir):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)
    
    registry = eng._load_registry(tmp_proj)
    registry["global_concept"] = {
        "concept_id": "global_concept",
        "canonical_name": "global-concept",
        "aliases": ["shared"],
        "global": True
    }
    eng._save_registry(registry, tmp_proj)
    
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"
    wiki_file = concepts_dir / "global_concept.md"
    wiki_file.write_text("# Shared Concept\nBody content.", encoding="utf-8")
    
    vault = GlobalVault(vault_dir=tmp_vault_dir)
    vault.sync_from_registry(registry, concepts_dir)
    
    global_context = vault.get_global_context()
    assert len(global_context) == 1
    assert global_context[0]["concept_id"] == "global_concept"
    assert global_context[0]["canonical_name"] == "global-concept"
    
    global_wiki = tmp_vault_dir / "wiki" / "global_concept.md"
    assert global_wiki.is_file()
    assert "Shared Concept" in global_wiki.read_text(encoding="utf-8")


def test_gated_vault_promotion(tmp_proj, tmp_vault_dir, monkeypatch):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # Mock find_all_projects to return the current project directory twice to simulate two projects
    # mapping to the same concept (cross-project usage)
    from harness_knowledge import engine
    monkeypatch.setattr(engine, "find_all_projects", lambda: [Path(tmp_proj), Path(tmp_proj)])

    registry = eng._load_registry(tmp_proj)
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "canonical_name": "gated-concept",
        "status": "canonical",
        "evidence_count": 5,
        "confidence": 4
    }
    eng._save_registry(registry, tmp_proj)

    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"
    wiki_file = concepts_dir / "concept_001.md"
    wiki_file.write_text("---\nstatus: canonical\n---\n# Gated Concept\nSome details.", encoding="utf-8")

    vault = GlobalVault(vault_dir=tmp_vault_dir)

    # 1. Verify it appears as a candidate
    candidates = vault.vault_candidates(tmp_proj)
    assert len(candidates) == 1
    assert candidates[0]["concept_id"] == "concept_001"

    # 2. Promote to Global
    vault.promote_to_global("concept_001", tmp_proj)

    # Verify status in local registry and markdown updated to global
    updated_local_reg = eng._load_registry(tmp_proj)
    assert updated_local_reg["concept_001"]["status"] == "global"
    assert updated_local_reg["concept_001"]["global"] is True
    assert "status: global" in wiki_file.read_text(encoding="utf-8")

    # Verify syndicated to global vault registry and wiki
    global_reg = vault._load_registry()
    assert "concept_001" in global_reg
    assert global_reg["concept_001"]["status"] == "global"
    assert (tmp_vault_dir / "wiki" / "concept_001.md").is_file()

    # 3. Demote from Global
    vault.demote_from_global("concept_001", tmp_proj)

    # Verify status reverted back to canonical
    demoted_local_reg = eng._load_registry(tmp_proj)
    assert demoted_local_reg["concept_001"]["status"] == "canonical"
    assert demoted_local_reg["concept_001"]["global"] is False
    assert "status: canonical" in wiki_file.read_text(encoding="utf-8")

    # Verify deleted from global registry and wiki
    assert "concept_001" not in vault._load_registry()
    assert not (tmp_vault_dir / "wiki" / "concept_001.md").exists()
