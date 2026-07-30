"""Fitness computation — concept fitness scoring wrapper.

Delegates to FitnessService. Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.models import ConceptFitness


class FitnessComputation:
    """Thin wrapper over FitnessService."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _fitness(self):
        return self._engine.fitness

    def calculate_fitness(
        self, project: str | None = None
    ) -> dict[str, "ConceptFitness"]:
        return self._fitness.calculate_fitness(project)
