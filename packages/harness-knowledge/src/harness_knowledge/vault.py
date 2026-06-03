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
                wiki_file = local_wiki_dir / f"{cid}.md"
                wiki_content = None
                if wiki_file.exists():
                    wiki_content = wiki_file.read_text(encoding="utf-8")
                self.syndicate_concept(cid, data, wiki_content)

    def vault_candidates(self, project: str | Path | None = None) -> list[dict]:
        """Find all local concepts that are eligible for Global Vault promotion."""
        from harness_knowledge.engine import KnowledgeEngine, find_all_projects

        engine = KnowledgeEngine(project)
        local_reg = engine._load_registry()
        global_reg = self._load_registry()

        # Find all projects
        all_projects = find_all_projects()
        project_registries = []
        for proj in all_projects:
            try:
                p_engine = KnowledgeEngine(proj)
                project_registries.append(p_engine._load_registry())
            except Exception:
                pass

        candidates = []
        for cid, data in local_reg.items():
            # Check status locally is canonical or cross_project or global
            local_status = data.get("status", "candidate")
            if local_status not in ("canonical", "cross-project", "global"):
                continue

            # Check evidence count >= 5
            evidence_count = data.get("evidence_count", 0)
            if evidence_count < 5:
                continue

            # Check cross-project usage: occurrence in >= 2 projects
            c_name = data.get("canonical_name", "").lower()
            proj_occurrences = 0
            for reg in project_registries:
                for other_cid, other_data in reg.items():
                    if other_data.get("canonical_name", "").lower() == c_name:
                        proj_occurrences += 1
                        break

            # Must occur in at least 2 distinct projects
            if proj_occurrences >= 2:
                # If not already syndicated globally, it's a promotion candidate
                if cid not in global_reg:
                    candidates.append({
                        "concept_id": cid,
                        "canonical_name": data.get("canonical_name", ""),
                        "evidence_count": evidence_count,
                        "project_occurrences": proj_occurrences,
                    })

        return candidates

    def promote_to_global(self, concept_id: str, project: str | Path | None = None):
        """Promote a local concept to the global vault and update status."""
        from harness_knowledge.engine import KnowledgeEngine
        engine = KnowledgeEngine(project)
        local_reg = engine._load_registry()
        if concept_id not in local_reg:
            raise KeyError(f"Concept {concept_id} not found in local registry")

        data = local_reg[concept_id]
        data["status"] = "global"
        data["global"] = True

        # Read local wiki content
        wiki_file = engine._concepts_dir() / f"{concept_id}.md"
        wiki_content = None
        if wiki_file.exists():
            wiki_content = wiki_file.read_text(encoding="utf-8")

            # Update status in the local markdown frontmatter
            import re
            content = wiki_content
            content = re.sub(r"status:\s*[^\n\r]+", "status: global", content)
            if "global:" not in content:
                content = content.replace("---", "---\nglobal: true", 1)
            engine._safe_write_concept_file(wiki_file, content)
            wiki_content = content

        # Save local registry update
        engine._save_registry(local_reg)
        engine._log_action(f"Vault | Promoted concept {concept_id} to global vault")

        # Syndicate to global vault
        self.syndicate_concept(concept_id, data, wiki_content)

    def demote_from_global(self, concept_id: str, project: str | Path | None = None):
        """Demote a concept from global vault back to local canonical status."""
        # Remove from global registry and wiki
        global_reg = self._load_registry()
        if concept_id in global_reg:
            del global_reg[concept_id]
            self._save_registry(global_reg)

        global_wiki = self.wiki_dir / f"{concept_id}.md"
        if global_wiki.exists():
            global_wiki.unlink()

        # Update local registry if present
        from harness_knowledge.engine import KnowledgeEngine
        engine = KnowledgeEngine(project)
        local_reg = engine._load_registry()
        if concept_id in local_reg:
            data = local_reg[concept_id]
            data["status"] = "canonical"
            data["global"] = False
            engine._save_registry(local_reg)

            # Update markdown file frontmatter
            wiki_file = engine._concepts_dir() / f"{concept_id}.md"
            if wiki_file.exists():
                import re
                content = wiki_file.read_text(encoding="utf-8")
                content = re.sub(r"status:\s*[^\n\r]+", "status: canonical", content)
                content = re.sub(r"global:\s*[^\n\r]+\n", "", content)
                engine._safe_write_concept_file(wiki_file, content)

            engine._log_action(f"Vault | Demoted concept {concept_id} from global vault")
