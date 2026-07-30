"""
oem_knowledge.computation — Computation layer protocol definitions.

These protocols define the contracts that the API layer depends on.
The Computation layer implements them with pure logic over Storage data.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SnapshotProtocol(Protocol):
    """Replay events and rebuild the full concept registry from scratch."""

    def rebuild_registry(self, project: str | None = None) -> dict:
        """Replay all events and produce a complete concept_registry.json.

        This is the PRIMARY snapshot computation path. Every session_end
        rebuilds the registry from the event log. The registry is a
        derived materialized view, not a separate source of truth.

        Returns:
            dict with keys: status, message, materialized (int count)
        """
        ...

    def consolidate(self, project: str | None = None, threshold: float = 0.82) -> dict:
        """Find and merge near-duplicate concepts in the registry.

        Returns:
            dict with keys: status, message, merged (list of merge records)
        """
        ...


@runtime_checkable
class ReflectionProtocol(Protocol):
    """Event extraction from conversation text and structured inputs."""

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
        """Extract structured knowledge events from a session.

        Supports structured, marker, and dense (LLM) extraction modes.
        Returns a dict with status, knowledge_events, canonical_events, etc.
        """
        ...

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
        """Add an inline memory during active work without session end.

        Returns:
            dict with status, event_id, auto_accepted, flagged
        """
        ...


@runtime_checkable
class IndexingProtocol(Protocol):
    """Chunk, embed, and index concepts into the vector store."""

    def index_all(
        self,
        force: bool = False,
        progress_callback: object = None,
        budget_seconds: float | None = None,
    ) -> dict:
        """Index all eligible files into the vector store.

        Returns:
            dict with keys: status, scanned, new, updated, unchanged, failed
        """
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a list of text strings.

        Args:
            texts: List of text strings to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        ...


@runtime_checkable
class SearchProtocol(Protocol):
    """Hybrid search (BM25 + dense) with relevance ranking."""

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
        """Search the vector store for relevant concepts.

        Args:
            query: Search query string.
            k: Number of results to return (default 3).
            scope: Filter by memory scope ('project', 'user', 'session').
            memory_type: Filter by memory type ('decision', 'failure', etc.).
            since: ISO 8601 timestamp — only return entries after this date.
            until: ISO 8601 timestamp — only return entries before this date.
            hybrid: If True, use hybrid (BM25 + dense) search; if False, BM25 only.

        Returns:
            list of dicts with keys: id, document, score, metadata, memory_type
        """
        ...


@runtime_checkable
class PreflightProtocol(Protocol):
    """Preflight routing: match a task against memory, concepts, and skills."""

    def run_preflight(
        self,
        task: str,
        project: str | None = None,
        limit: int = 8,
        write_audit: bool = True,
    ) -> dict:
        """Run preflight and return matches with relevance scoring.

        Returns:
            dict with keys: decision, matched_memory, matched_concepts,
            matched_skills, source_suggestions, context, warnings
        """
        ...
