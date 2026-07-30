"""Preflight computation — preflight routing wrapper.

Implements PreflightProtocol by delegating to engine.preflight.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class PreflightComputation:
    """Thin wrapper over engine's preflight method."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    def run_preflight(
        self,
        task: str,
        project: str | None = None,
        limit: int = 8,
        write_audit: bool = True,
    ) -> dict:
        return self._engine.preflight(
            task=task,
            project=project,
            limit=limit,
            write_audit=write_audit,
        )