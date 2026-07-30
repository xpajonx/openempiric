"""Reflection computation — event extraction wrapper.

Implements ReflectionProtocol by delegating to ReflectionService.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class ReflectionComputation:
    """Thin wrapper over ReflectionService."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _reflection(self):
        return self._engine.reflection

    def extract_session_events(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
        telemetry: dict | None = None,
        session_started_at: float | None = None,
        progress_callback: object = None,
    ) -> dict:
        return self._reflection.extract_session_events(
            project=project,
            conversation_text=conversation_text,
            session_id=session_id,
            events=events,
            extraction_mode=extraction_mode,
            timeout_seconds=timeout_seconds,
            telemetry=telemetry,
            session_started_at=session_started_at,
            progress_callback=progress_callback,
        )

    def add_inline_memory(
        self,
        memory_type: str,
        content: str,
        scope: str = "project",
        confidence: int = 3,
        evidence: str = "",
        session_id: str = "",
        project: str | None = None,
    ) -> dict:
        return self._reflection.add_inline_memory(
            memory_type=memory_type,
            content=content,
            scope=scope,
            confidence=confidence,
            evidence=evidence,
            session_id=session_id,
            project=project,
        )
