"""Session files — session state serialization wrapper.

Thin wrapper over SessionState for session I/O.
No protocol defined yet — this is a convenience wrapper.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class SessionFiles:
    """Thin wrapper for session state persistence."""

    def __init__(self, engine: "KnowledgeEngine"):
        self._engine = engine

    def load_active_session(self, project: str | None = None) -> dict | None:
        """Load the active session state from disk."""
        harness = self._engine._resolve_harness(project)
        active_file = harness / "state" / "active_session.json"
        if not active_file.exists():
            return None
        from oem_knowledge.runtime import SessionState
        try:
            state = SessionState.load(active_file)
            return state.to_dict() if state else None
        except Exception:
            return None

    def unlink_active_session(self, project: str | None = None):
        """Remove the active session file."""
        harness = self._engine._resolve_harness(project)
        active_file = harness / "state" / "active_session.json"
        if active_file.exists():
            active_file.unlink()
