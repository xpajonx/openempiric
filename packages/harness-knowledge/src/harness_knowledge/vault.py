from __future__ import annotations

import json
import shutil
from pathlib import Path


class GlobalVault:
    def __init__(self, vault_dir: Path | None = None):
        if vault_dir is None:
            self.vault_dir = Path.home() / ".oem"
        else:
            self.vault_dir = Path(vault_dir)

        self.registry_file = self.vault_dir / "registry.json"
        self.wiki_dir = self.vault_dir / "wiki"
        self._bootstrap()

    def _bootstrap(self):
        """Create global .oem vault directories if they do not exist."""
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_file.exists():
            self.registry_file.write_text("{}", encoding="utf-8")

    def _load_registry(self) -> dict:
        if self.registry_file.exists():
            try:
                return json.loads(self.registry_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_registry(self, registry: dict):
        self.registry_file.write_text(
            json.dumps(registry, indent=2), encoding="utf-8"
        )

    def syndicate_concept(self, concept_id: str, concept_data: dict, wiki_content: str | None = None):
        """Syndicate a concept from local project to the global vault."""
        registry = self._load_registry()
        # Merge concept data
        registry[concept_id] = concept_data
        self._save_registry(registry)

        if wiki_content:
            wiki_file = self.wiki_dir / f"{concept_id}.md"
            wiki_file.write_text(wiki_content, encoding="utf-8")

    def get_global_context(self) -> list[dict]:
        """Retrieve all syndicated global concepts for preloading context."""
        registry = self._load_registry()
        concepts = []
        for cid, data in registry.items():
            wiki_file = self.wiki_dir / f"{cid}.md"
            concepts.append({
                "concept_id": cid,
                "canonical_name": data.get("canonical_name", cid),
                "aliases": data.get("aliases", []),
                "description": data.get("description", ""),
                "wiki_path": str(wiki_file) if wiki_file.exists() else None
            })
        return concepts

    def sync_from_registry(self, local_registry: dict, local_wiki_dir: Path):
        """Scan local registry for global concepts and syndicate them."""
        for cid, data in local_registry.items():
            if data.get("global") is True or "global" in data.get("tags", []):
                # Load local wiki content if exists
                wiki_file = local_wiki_dir / f"{cid}.md"
                wiki_content = None
                if wiki_file.exists():
                    wiki_content = wiki_file.read_text(encoding="utf-8")
                
                self.syndicate_concept(cid, data, wiki_content)
