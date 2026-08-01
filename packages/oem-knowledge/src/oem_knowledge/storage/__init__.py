"""
oem_knowledge.storage — Storage layer protocol definitions.

These protocols define the contracts that the Computation layer depends on.
The Storage layer implements them with real file I/O.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class EventStoreProtocol(Protocol):
    """Append-only event log with schema migration support."""

    def append_event(self, event: dict, project: str | None = None) -> None:
        """Append a single event to the event log under FileLock."""
        ...

    def append_events(self, events: list[dict], project: str | None = None) -> None:
        """Append multiple events atomically under FileLock."""
        ...

    def load_events(
        self,
        project: str | None = None,
        include_user: bool = False,
    ) -> list[dict]:
        """Load all events from the event log, optionally including user events."""
        ...


@runtime_checkable
class RegistryStoreProtocol(Protocol):
    """Registry JSON read/write under FileLock."""

    def load_registry(
        self,
        project: str | None = None,
        lock: bool = True,
    ) -> dict:
        """Load the concept registry from disk.

        Args:
            project: Project directory path.
            lock: Whether to acquire the FileLock. Default True (matching
                  the implementation's default for safety).
        """
        ...

    def save_registry(
        self,
        registry: dict,
        project: str | None = None,
        lock: bool = False,
    ) -> None:
        """Save the concept registry to disk under FileLock."""
        ...


@runtime_checkable
class ConceptFilesProtocol(Protocol):
    """wiki/*.md file write with collision handling."""

    def safe_write_concept_file(
        self,
        file_path: Path,
        content: str,
        project: str | None = None,
    ) -> bool:
        """Write a concept file atomically, handling collision via rename.

        Returns True if the write succeeded, False otherwise.
        """
        ...

    def get_concept_history(
        self,
        concept_id: str,
        project: str | None = None,
    ) -> list[dict]:
        """Get the modification history of a concept file."""
        ...
