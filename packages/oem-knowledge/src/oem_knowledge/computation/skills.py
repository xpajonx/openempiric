"""Skills computation — skill candidate management + promotion wrapper.

Delegates to SkillService and SkillPromotionService.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class SkillsComputation:
    """Thin wrapper over SkillService + SkillPromotionService."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _skills(self):
        return self._engine.skills

    @property
    def _skill_promotion(self):
        return self._engine.skill_promotion

    def list_skill_candidates(self, project: str | None = None) -> list:
        return self._skills.list_skill_candidates(project)

    def get_skill_candidate(self, slug: str, project: str | None = None) -> dict | None:
        return self._skills.get_skill_candidate(slug, project)

    def evaluate_candidates(self, project: str | None = None) -> list[dict]:
        return self._skill_promotion.evaluate_candidates(project)
