from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

SCOPES = ("project", "user", "session")

KNOWN_MEMORY_TYPES = (
    "decision", "failure", "observation", "outcome", "preference",
    "workaround", "technical_handoff", "debug_note",
    "search_log", "command_log", "source_dump",
)

_PROMOTABLE_FIELDS = (
    "scope", "memory_type", "timestamp", "created_at",
    "updated_at", "source", "project", "session_id",
)


def _parse_iso(value: str) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_window_value(value):
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def parse_iso_window(since: str | None, until: str | None) -> tuple[datetime | None, datetime | None, str | None]:
    """Parse an ISO-8601 since/until window. Never raises.

    Empty, whitespace-only, or non-string values are treated as absent.
    Errors are reported per side.
    Returns (since_dt, until_dt, error_message).
    """
    since = _clean_window_value(since)
    until = _clean_window_value(until)
    errors = []
    since_dt = None
    until_dt = None
    if since:
        since_dt = _parse_iso(since)
        if since_dt is None:
            errors.append(f"invalid since: {since!r}")
    if until:
        until_dt = _parse_iso(until)
        if until_dt is None:
            errors.append(f"invalid until: {until!r}")
    return since_dt, until_dt, ("; ".join(errors) or None)


def record_scope_matches(scope: str | None, record_scope: str | None) -> bool:
    """None scope matches any record (caller keeps default behavior)."""
    if scope is None:
        return True
    return scope == record_scope


def record_in_window(timestamp: str | None, since_dt: datetime | None, until_dt: datetime | None) -> bool:
    """True when timestamp is inside [since_dt, until_dt].

    Missing, non-string, or invalid timestamps default to True (compatibility
    behavior); callers that need strict semantics must surface a warning
    themselves.
    """
    if not isinstance(timestamp, str) or not timestamp:
        return True
    dt = _parse_iso(timestamp)
    if dt is None:
        return True
    if since_dt is not None and dt < since_dt:
        return False
    if until_dt is not None and dt > until_dt:
        return False
    return True


def normalize_record_fields(record: dict) -> dict:
    """Shallow copy with scope/type/time/source fields promoted from metadata.

    Promotion happens only when the field is absent at the top level.
    The input record is never mutated.
    """
    normalized = dict(record)
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    for field in _PROMOTABLE_FIELDS:
        if field not in normalized and field in metadata:
            normalized[field] = metadata[field]
    return normalized


def is_well_formed_record(record: dict) -> tuple[bool, list[str]]:
    """Validate the normalized record shape.

    Checks: non-empty id; document is str; metadata is dict; scope and
    memory_type in known vocabularies; timestamp parses as ISO-8601
    (invalid timestamp is fatal). A metadata scope conflicting with the
    top-level scope is reported as a non-fatal warning (top-level wins).
    """
    fatal_issues: list[str] = []
    warnings: list[str] = []
    if not record.get("id"):
        fatal_issues.append("missing id")
    if not isinstance(record.get("document"), str):
        fatal_issues.append("document must be str")
    if not isinstance(record.get("metadata", {}), dict):
        fatal_issues.append("metadata must be dict")
    scope = record.get("scope")
    if scope is not None and scope not in SCOPES:
        fatal_issues.append(f"invalid scope: {scope!r}")
    memory_type = record.get("memory_type")
    if memory_type is not None and memory_type not in KNOWN_MEMORY_TYPES:
        fatal_issues.append(f"invalid memory_type: {memory_type!r}")
    timestamp = record.get("timestamp")
    if timestamp is not None:
        if _parse_iso(timestamp) is None:
            fatal_issues.append(f"invalid timestamp: {timestamp!r}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict) and scope is not None and metadata.get("scope") is not None and metadata.get("scope") != scope:
        warnings.append(f"conflicting scope in metadata: {metadata['scope']!r} vs top-level {scope!r}")
    issues = fatal_issues + warnings
    return (not fatal_issues, issues)
