from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Legacy data structures (kept for backward compatibility)
# ---------------------------------------------------------------------------

@dataclass
class ActiveWorkItem:
    source: str
    key: str
    detail: str
    score: float = 0.0


@dataclass
class ActiveWorkResult:
    has_active_work: bool
    items: list[ActiveWorkItem] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    score: float = 0.0


# ---------------------------------------------------------------------------
# New data structures for active-project resolution
# ---------------------------------------------------------------------------

@dataclass
class ActiveProjectSource:
    source: str
    project: str | None
    raw_value: str | None
    confidence: str  # "high" or "low"


@dataclass
class ActiveProjectConflict:
    type: str  # "active_project_mismatch"
    sources: list[str]
    severity: str  # "error" or "warning"


@dataclass
class ActiveProjectResult:
    latest_project: str | None
    selected_source: str | None
    active_projects_by_source: dict[str, str | None]
    sources: list[ActiveProjectSource]
    conflicts: list[ActiveProjectConflict]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Continuation prompt detection
# ---------------------------------------------------------------------------

CONTINUATION_TRIGGERS = frozenset({
    "continue", "resume", "what next", "what now",
    "what did we do", "what have we done", "what is the current state",
    "where were we", "pick up where we left", "session start",
})


def is_continuation_prompt(task: str) -> bool:
    normalized = task.strip().casefold()
    for trigger in CONTINUATION_TRIGGERS:
        if trigger in normalized:
            return True
    return False


# ---------------------------------------------------------------------------
# Safe file readers
# ---------------------------------------------------------------------------

def _read_text_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _read_json_safe(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl_tail(path: Path, n: int) -> list[str]:
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
        return [l for l in (lines[-n:] if len(lines) > n else lines) if l.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Project identity normalization
# ---------------------------------------------------------------------------

def _normalize_project_identity(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    v = os.path.expanduser(v)
    if v.startswith("/"):
        try:
            v = str(Path(v).resolve())
        except Exception:
            pass
    if len(v) > 1 and v.endswith("/"):
        v = v.rstrip("/")
    if not v.startswith("/") and not v.startswith("~"):
        v = " ".join(v.split())
    return v


def _projects_match(p1: str | None, p2: str | None) -> bool:
    if p1 is None or p2 is None:
        return False
    n1 = _normalize_project_identity(p1)
    n2 = _normalize_project_identity(p2)
    if not n1 or not n2:
        return False
    return n1 == n2


# ---------------------------------------------------------------------------
# Per-source project parsers
# ---------------------------------------------------------------------------

_HANDOFF_MD_PROJECT_MARKERS = [
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+project\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s+root\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Memory\s+root\s*:\s*(.+?)\s*$"),
]

_CONTEXT_MD_PROJECT_MARKERS = [
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+project\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s+root\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)^\s*(?:[-*]\s+)?Memory\s+root\s*:\s*(.+?)\s*$"),
    re.compile(r"(?im)(.+?)\s+is\s+the\s+open\s+project"),
]


def _parse_project_from_handoff_md(text: str) -> tuple[str | None, str]:
    for pattern in _HANDOFF_MD_PROJECT_MARKERS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip(), "high"
    return None, "low"


def _parse_project_from_context_md(text: str) -> tuple[str | None, str]:
    for pattern in _CONTEXT_MD_PROJECT_MARKERS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip(), "high"
    return None, "low"


def _parse_project_from_handoff_json(data: Any) -> tuple[str | None, str, str | None]:
    if not isinstance(data, dict):
        return None, "low", None
    project_root = data.get("project_root")
    if project_root and isinstance(project_root, str):
        return project_root.strip(), "high", data.get("project_label")
    project_label = data.get("project_label")
    if project_label and isinstance(project_label, str):
        return project_label.strip(), "high", None
    project = data.get("project")
    if project and isinstance(project, str):
        return project.strip(), "high", None
    return None, "low", None


def _parse_project_from_outcome(record: dict) -> tuple[str | None, str]:
    project = record.get("project")
    if project and isinstance(project, str):
        return project.strip(), "high"
    project_root = record.get("project_root")
    if project_root and isinstance(project_root, str):
        return project_root.strip(), "high"
    return None, "low"


# ---------------------------------------------------------------------------
# Canonical active-project resolver
# ---------------------------------------------------------------------------

SOURCE_HANDOFF_JSON = "session_handoff_json"
SOURCE_HANDOFF_MD = "session_handoff_md"
SOURCE_STATE_HANDOFF_MD = "state_session_handoff_md"
SOURCE_RUNTIME_CONTEXT = "runtime_context_md"
SOURCE_LATEST_OUTCOME = "latest_outcome"


def resolve_active_project(memory_root: Path) -> ActiveProjectResult:
    sources: list[ActiveProjectSource] = []
    warnings: list[str] = []

    # 1. session-handoff.json (canonical)
    json_path = memory_root / "session-handoff.json"
    json_data = _read_json_safe(json_path)
    if json_data is not None:
        project, conf, label = _parse_project_from_handoff_json(json_data)
        sources.append(ActiveProjectSource(
            source=SOURCE_HANDOFF_JSON,
            project=project,
            raw_value=project,
            confidence=conf,
        ))
    elif json_path.exists():
        warnings.append(f"Malformed session-handoff.json at {json_path}, falling back to Markdown")
        sources.append(ActiveProjectSource(
            source=SOURCE_HANDOFF_JSON,
            project=None,
            raw_value=None,
            confidence="low",
        ))

    # 2. session-handoff.md (root level)
    md_path = memory_root / "session-handoff.md"
    md_text = _read_text_safe(md_path)
    if md_text:
        project, conf = _parse_project_from_handoff_md(md_text)
        sources.append(ActiveProjectSource(
            source=SOURCE_HANDOFF_MD,
            project=project,
            raw_value=project,
            confidence=conf,
        ))

    # 3. state/session-handoff.md (legacy compat)
    state_md_path = memory_root / "state" / "session-handoff.md"
    state_md_text = _read_text_safe(state_md_path)
    if state_md_text:
        project, conf = _parse_project_from_handoff_md(state_md_text)
        sources.append(ActiveProjectSource(
            source=SOURCE_STATE_HANDOFF_MD,
            project=project,
            raw_value=project,
            confidence=conf,
        ))

    # 4. .runtime/context.md
    context_path = memory_root / ".runtime" / "context.md"
    context_text = _read_text_safe(context_path)
    if context_text:
        project, conf = _parse_project_from_context_md(context_text)
        sources.append(ActiveProjectSource(
            source=SOURCE_RUNTIME_CONTEXT,
            project=project,
            raw_value=project,
            confidence=conf,
        ))

    # 5. outcomes.jsonl tail
    outcomes_path = memory_root / "state" / "outcomes.jsonl"
    recent_outcomes = _read_jsonl_tail(outcomes_path, 3)
    if recent_outcomes:
        for line in reversed(recent_outcomes):
            try:
                rec = json.loads(line)
                project, conf = _parse_project_from_outcome(rec)
                if project:
                    sources.append(ActiveProjectSource(
                        source=SOURCE_LATEST_OUTCOME,
                        project=project,
                        raw_value=project,
                        confidence=conf,
                    ))
                    break
            except json.JSONDecodeError:
                pass

    # Build active_projects_by_source dict
    active_projects_by_source: dict[str, str | None] = {}
    for s in sources:
        active_projects_by_source[s.source] = s.project

    # Determine latest_project using precedence
    precedence = [
        SOURCE_HANDOFF_JSON,
        SOURCE_HANDOFF_MD,
        SOURCE_STATE_HANDOFF_MD,
        SOURCE_RUNTIME_CONTEXT,
        SOURCE_LATEST_OUTCOME,
    ]
    latest_project: str | None = None
    selected_source: str | None = None
    for src_name in precedence:
        for s in sources:
            if s.source == src_name and s.project:
                latest_project = s.project
                selected_source = src_name
                break
        if latest_project:
            break

    # Detect conflicts
    conflicts = _detect_project_conflicts(sources)

    return ActiveProjectResult(
        latest_project=latest_project,
        selected_source=selected_source,
        active_projects_by_source=active_projects_by_source,
        sources=sources,
        conflicts=conflicts,
        warnings=warnings,
    )


def _detect_project_conflicts(sources: list[ActiveProjectSource]) -> list[ActiveProjectConflict]:
    seen: dict[str, str] = {}
    for s in sources:
        if s.project is None:
            continue
        norm = _normalize_project_identity(s.project)
        if not norm:
            continue
        if norm not in seen:
            seen[norm] = s.source
        elif seen[norm] != s.source:
            pass

    unique_norms = list(seen.keys())
    if len(unique_norms) <= 1:
        return []

    conflicting_sources: list[str] = []
    high_confidence_count = 0
    for s in sources:
        if s.project is None:
            continue
        norm = _normalize_project_identity(s.project)
        if norm and norm != unique_norms[0]:
            if s.source not in conflicting_sources:
                conflicting_sources.append(s.source)
                if s.confidence == "high":
                    high_confidence_count += 1
        else:
            if s.source not in conflicting_sources and s.confidence == "high":
                if s.source not in conflicting_sources:
                    conflicting_sources.append(s.source)

    all_conflict_sources: list[str] = []
    for s in sources:
        if s.project is not None and s.source not in all_conflict_sources:
            all_conflict_sources.append(s.source)

    high_conflict_sources = [
        s.source for s in sources
        if s.project is not None and s.confidence == "high"
    ]

    unique_high = set()
    for s in sources:
        if s.project is not None and s.confidence == "high":
            unique_high.add(_normalize_project_identity(s.project))

    if len(unique_high) >= 3:
        return [ActiveProjectConflict(
            type="active_project_mismatch",
            sources=high_conflict_sources,
            severity="error",
        )]
    elif len(unique_high) >= 2:
        return [ActiveProjectConflict(
            type="active_project_mismatch",
            sources=high_conflict_sources,
            severity="warning",
        )]
    elif len(unique_norms) >= 2:
        return [ActiveProjectConflict(
            type="active_project_mismatch",
            sources=all_conflict_sources,
            severity="warning",
        )]
    return []


# ---------------------------------------------------------------------------
# Legacy resolve_active_work (updated to use new resolver for contradictions)
# ---------------------------------------------------------------------------

def resolve_active_work(memory_root: Path) -> ActiveWorkResult:
    items: list[ActiveWorkItem] = []

    # 1. todos.json — high signal
    todos_path = memory_root / "state" / "todos.json"
    todos_data = _read_json_safe(todos_path)
    if isinstance(todos_data, list):
        for t in todos_data:
            if not isinstance(t, dict):
                continue
            content = str(t.get("content", "unknown"))
            status = str(t.get("status", "")).casefold()
            if status == "in_progress":
                items.append(ActiveWorkItem(
                    source="todos.json",
                    key=content,
                    detail=f"In-progress: {content}",
                    score=3.0,
                ))
            elif status == "pending":
                items.append(ActiveWorkItem(
                    source="todos.json",
                    key=content,
                    detail=f"Pending: {content}",
                    score=1.0,
                ))

    # 2. context.md — medium signal
    context_path = memory_root / ".runtime" / "context.md"
    context_text = _read_text_safe(context_path)
    context_topic: str | None = None
    if context_text:
        for line in context_text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("*"):
                context_topic = stripped[:120]
                items.append(ActiveWorkItem(
                    source="context.md",
                    key=context_topic,
                    detail=stripped[:200],
                    score=2.0,
                ))
                break

    # 3. session-handoff.md — check root level first, then state level
    handoff_path = memory_root / "session-handoff.md"
    handoff_text = _read_text_safe(handoff_path)
    if not handoff_text:
        handoff_path = memory_root / "state" / "session-handoff.md"
        handoff_text = _read_text_safe(handoff_path)
    handoff_topic: str | None = None
    if handoff_text:
        first_line = handoff_text.strip().split("\n", 1)[0][:80]
        handoff_topic = first_line
        items.append(ActiveWorkItem(
            source="session-handoff.md",
            key=first_line,
            detail=handoff_text[:200].strip(),
            score=2.5,
        ))

    # 4. events.jsonl tail — low signal
    events_path = memory_root / "events" / "events.jsonl"
    recent = _read_jsonl_tail(events_path, 5)
    if recent:
        for line in reversed(recent):
            try:
                ev = json.loads(line)
                event_type = ev.get("type", ev.get("event", "unknown"))
                items.append(ActiveWorkItem(
                    source="events.jsonl",
                    key=event_type,
                    detail=f"Recent event: {event_type}",
                    score=0.5,
                ))
            except json.JSONDecodeError:
                pass

    # 5. outcomes.jsonl tail — low signal
    outcomes_path = memory_root / "state" / "outcomes.jsonl"
    recent_outcomes = _read_jsonl_tail(outcomes_path, 3)
    if recent_outcomes:
        items.append(ActiveWorkItem(
            source="outcomes.jsonl",
            key=f"{len(recent_outcomes)} recent outcomes",
            detail="Recent outcomes recorded",
            score=1.0,
        ))

    # Contradiction detection — topic-level text comparison (legacy)
    contradictions: list[str] = []
    if context_topic and handoff_topic:
        if context_topic[:40] != handoff_topic[:40]:
            contradictions.append(
                f"context.md topic `{context_topic[:60]}...` vs "
                f"session-handoff.md topic `{handoff_topic[:60]}...`"
            )
    # Also collect project-level contradictions from canonical resolver
    proj_result = resolve_active_project(memory_root)
    for c in proj_result.conflicts:
        contradictions.append(
            f"active project mismatch: {', '.join(c.sources)} "
            f"(severity: {c.severity})"
        )
    for w in proj_result.warnings:
        if w not in contradictions:
            contradictions.append(w)

    has_active_work = len(items) > 0
    score = min(sum(item.score for item in items), 10.0)

    return ActiveWorkResult(
        has_active_work=has_active_work,
        items=items,
        contradictions=contradictions,
        score=score,
    )
