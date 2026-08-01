"""Snapshot computation — event replay -> registry + concepts.

Implements SnapshotProtocol by delegating to StateService.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class SnapshotComputation:
    """Thin wrapper over StateService for snapshot computation."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _state(self):
        return self._engine.state

    def rebuild_registry(self, project: str | None = None) -> dict:
        return self._state.rebuild_registry(project)

    def consolidate(self, project: str | None = None, threshold: float = 0.82) -> dict:
        return self._state.consolidate(project, threshold)
