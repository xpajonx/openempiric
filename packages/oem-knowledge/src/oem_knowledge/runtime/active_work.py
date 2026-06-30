from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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

    # 3. session-handoff.md — medium-high signal
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

    contradictions: list[str] = []
    if context_topic and handoff_topic:
        if context_topic[:40] != handoff_topic[:40]:
            contradictions.append(
                f"context.md topic `{context_topic[:60]}...` vs "
                f"session-handoff.md topic `{handoff_topic[:60]}...`"
            )

    has_active_work = len(items) > 0
    score = min(sum(item.score for item in items), 10.0)

    return ActiveWorkResult(
        has_active_work=has_active_work,
        items=items,
        contradictions=contradictions,
        score=score,
    )
