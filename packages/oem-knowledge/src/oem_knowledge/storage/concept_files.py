"""Concept files — wiki/*.md write wrapper.

Implements ConceptFilesProtocol by delegating to MaterializationService methods.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class ConceptFiles:
    """Thin wrapper over MaterializationService for concept file I/O."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _materialization(self):
        return self._engine.materialization

    def safe_write_concept_file(
        self,
        file_path: Path,
        content: str,
        project: str | None = None,
    ) -> bool:
        """Write a concept file atomically, handling collision."""
        return self._materialization.safe_write_concept_file(file_path, content, project)

    def get_concept_history(
        self,
        concept_id: str,
        project: str | None = None,
    ) -> list[dict]:
        """Get the modification history of a concept file."""
        return self._materialization.get_concept_history(concept_id, project)
