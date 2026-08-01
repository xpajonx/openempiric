"""Search computation — hybrid search wrapper.

Implements SearchProtocol by delegating to SearchService.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class SearchComputation:
    """Thin wrapper over SearchService for search operations."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _search(self):
        return self._engine.search

    def search(
        self,
        query: str,
        k: int = 3,
        scope: str | None = None,
        memory_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        hybrid: bool = True,
    ) -> list[dict]:
        return self._search.search(
            query=query,
            k=k,
            scope=scope,
            memory_type=memory_type,
            since=since,
            until=until,
            hybrid=hybrid,
        )
