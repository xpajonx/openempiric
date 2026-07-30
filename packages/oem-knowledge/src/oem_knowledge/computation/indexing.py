"""Indexing computation — chunk, embed, index wrapper.

Implements IndexingProtocol by delegating to SearchService.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class IndexingComputation:
    """Thin wrapper over SearchService for indexing operations."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _search(self):
        return self._engine.search

    def index_all(
        self,
        force: bool = False,
        progress_callback: object = None,
        budget_seconds: float | None = None,
    ) -> dict:
        return self._search.index_all(
            force=force,
            progress_callback=progress_callback,
            budget_seconds=budget_seconds,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._search.embed(texts)
