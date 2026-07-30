"""Evolution computation — concept decay, merge, promotion wrapper.

Delegates to EvolutionService. Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class EvolutionComputation:
    """Thin wrapper over EvolutionService."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _evolution(self):
        return self._engine.evolution

    def apply_decay(self, project: str | None = None) -> dict:
        return self._evolution.apply_decay(project)

    def promote_concepts(self, project: str | None = None) -> dict:
        return self._evolution.promote_concepts(project)
