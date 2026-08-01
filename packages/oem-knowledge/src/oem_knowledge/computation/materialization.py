"""Materialization computation — concept file generation wrapper.

Delegates to MaterializationService. Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class MaterializationComputation:
    """Thin wrapper over MaterializationService."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _materialization(self):
        return self._engine.materialization

    def materialize_concepts(self, project: str | None = None) -> dict:
        return self._materialization.materialize_concepts(project)

    def update_graph(self, project: str | None = None) -> dict:
        return self._materialization.update_graph(project)
