"""Event store — append-only event log wrapper.

Implements EventStoreProtocol by delegating to StateService methods.
Thin wrapper, zero behavioral change.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class EventStore:
    """Thin wrapper over StateService for event log I/O."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    @property
    def _state(self):
        return self._engine.state

    def append_event(self, event: dict, project: str | None = None) -> None:
        """Append a single event to the event log."""
        self._state.append_event(event, project)

    def append_events(self, events: list[dict], project: str | None = None) -> None:
        """Append multiple events atomically."""
        self._state.append_events(events, project)

    def load_events(
        self,
        project: str | None = None,
        include_user: bool = False,
    ) -> list[dict]:
        """Load all events from the event log."""
        return self._state.load_events(project, include_user=include_user)

    def get_user_events_path(self) -> Path | None:
        """Return the path to the user events file."""
        from oem_knowledge.services.state import get_user_events_path
        return get_user_events_path()

    def migrate_event_schema(self, event: dict) -> dict:
        """Upcast an event to the latest schema version."""
        from oem_knowledge.services.event_migration import EventMigrator
        migrator = EventMigrator(self._engine)
        return migrator.upcast(event)
