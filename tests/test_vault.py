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
        "global": True
    }
    wiki_content = "# Global Auth\nThis concept is global."
    
    vault.syndicate_concept("global_auth", concept_data, wiki_content)
    
    # Verify in registry
    registry = vault._load_registry()
    assert "global_auth" in registry
    assert registry["global_auth"]["canonical_name"] == "global-auth"
    
    # Verify wiki file
    wiki_file = tmp_vault_dir / "wiki" / "global_auth.md"
    assert wiki_file.is_file()
    assert wiki_file.read_text(encoding="utf-8") == wiki_content
    
    # Verify get_global_context
    context = vault.get_global_context()
    assert len(context) == 1
    assert context[0]["concept_id"] == "global_auth"
    assert context[0]["canonical_name"] == "global-auth"
    assert context[0]["wiki_path"] == str(wiki_file)


def test_local_registry_sync(tmp_proj, tmp_vault_dir):
    eng = KnowledgeEngine(tmp_proj)
    # Initialize project
    eng.init_project(tmp_proj)
    
    # Add a global concept to local registry
    registry = eng._load_registry(tmp_proj)
    registry["global_concept"] = {
        "concept_id": "global_concept",
        "canonical_name": "global-concept",
        "aliases": ["shared"],
        "global": True
    }
    eng._save_registry(registry, tmp_proj)
    
    # Write local wiki file
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"
    wiki_file = concepts_dir / "global_concept.md"
    wiki_file.write_text("# Shared Concept\nBody content.", encoding="utf-8")
    
    # Sync using mocked/custom vault dir
    vault = GlobalVault(vault_dir=tmp_vault_dir)
    vault.sync_from_registry(registry, concepts_dir)
    
    global_context = vault.get_global_context()
    assert len(global_context) == 1
    assert global_context[0]["concept_id"] == "global_concept"
    assert global_context[0]["canonical_name"] == "global-concept"
    
    global_wiki = tmp_vault_dir / "wiki" / "global_concept.md"
    assert global_wiki.is_file()
    assert "Shared Concept" in global_wiki.read_text(encoding="utf-8")
