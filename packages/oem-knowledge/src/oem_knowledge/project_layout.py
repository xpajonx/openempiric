"""
oem_knowledge.project_layout — Project path value object

ProjectLayout is a pure @dataclass over an already-resolved .oem root directory.
It computes child paths as lazy properties.

Resolution stays in KnowledgeEngine.layout(project), which calls _resolve_harness()
to produce the root Path, then wraps it in ProjectLayout.

Usage:
    layout = engine.layout(project)
    registry = layout.registry_path
    concepts = layout.concepts_dir
    db = layout.vector_db_path
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProjectLayout:
    root: Path  # already-resolved .oem directory

    @property
    def registry_path(self) -> Path:
        return self.root / "concept_registry.json"

    @property
    def events_path(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def concepts_dir(self) -> Path:
        return self.root / "wiki"

    @property
    def vector_db_path(self) -> Path:
        return self.root / ".local_vector_db"

    @property
    def source_indexes_dir(self) -> Path:
        return self.root / "indexes"

    @property
    def source_index_db_path(self) -> Path:
        return self.source_indexes_dir / "source_index.sqlite"

    @property
    def source_manifest_path(self) -> Path:
        return self.root / "source_manifest.json"

    @property
    def source_config_path(self) -> Path:
        return self.root / "source_index_config.yml"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def graph_dir(self) -> Path:
        return self.root / "graph"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def skill_candidates_dir(self) -> Path:
        return self.root / "skill_candidates"

    @property
    def skill_promotions_path(self) -> Path:
        return self.root / "skill_promotions.jsonl"

    def wiki_paths(self) -> dict:
        return {
            "inbox": self.concepts_dir / "inbox.md",
            "concepts": self.concepts_dir,
            "variant": "wiki",
        }
