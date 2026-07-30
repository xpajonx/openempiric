"""Registry store — concept registry JSON read/write wrapper.

Implements RegistryStoreProtocol by delegating to StateService methods.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class RegistryStore:
    """Thin wrapper over StateService for registry I/O."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _state(self):
        return self._engine.state

    def load_registry(
        self,
        project: str | None = None,
        lock: bool = True,
    ) -> dict:
        """Load the concept registry from disk."""
        return self._state.load_registry(project, lock=lock)

    def save_registry(
        self,
        registry: dict,
        project: str | None = None,
        lock: bool = False,
    ) -> None:
        """Save the concept registry to disk under FileLock."""
        self._state.save_registry(registry, project, lock=lock)
