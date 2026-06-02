from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolEvent:
    tool: str
    call_id: str
    status: str
    input: dict[str, Any]
    output: str | None
    exit_code: int | None
    truncated: bool
    duration_ms: int | None
    title: str | None


@dataclass
class StepFinishEvent:
    tokens: dict[str, Any]
    cost: float
    reason: str | None


@dataclass
class SessionTranscript:
    session_id: str
    tool_calls: list[ToolEvent] = field(default_factory=list)
    steps: list[StepFinishEvent] = field(default_factory=list)
    total_tokens: dict[str, int] = field(default_factory=dict)
    total_cost: float = 0.0


def parse_stream(line: str, transcript: SessionTranscript | None = None) -> SessionTranscript | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return transcript

    typ = event.get("type")
    sid = event.get("sessionID")

    if transcript is None:
        transcript = SessionTranscript(session_id=sid or "")

    if typ == "tool_use":
        part = event.get("part", {})
        state = part.get("state", {})
        inp = state.get("input", {})
        meta = state.get("metadata", {})
        tim = state.get("time", {}) or {}
        tool_event = ToolEvent(
            tool=part.get("tool", ""),
            call_id=part.get("callID", ""),
            status=state.get("status", ""),
            input=inp,
            output=state.get("output"),
            exit_code=meta.get("exit"),
            truncated=meta.get("truncated", False),
            duration_ms=(tim.get("end", 0) - tim.get("start", 0)) if tim.get("start") else None,
            title=state.get("title"),
        )
        transcript.tool_calls.append(tool_event)

    elif typ == "step_finish":
        part = event.get("part", {})
        tokens = part.get("tokens", {})
        finish = StepFinishEvent(
            tokens=tokens,
            cost=part.get("cost", 0.0),
            reason=part.get("reason"),
        )
        transcript.steps.append(finish)
        transcript.total_cost += finish.cost

        for key in ("total", "input", "output", "reasoning"):
            val = tokens.get(key, 0)
            transcript.total_tokens[key] = transcript.total_tokens.get(key, 0) + val

        for cache_key in ("write", "read"):
            cache_tokens = tokens.get("cache", {}).get(cache_key, 0)
            ck = f"cache_{cache_key}"
            transcript.total_tokens[ck] = transcript.total_tokens.get(ck, 0) + cache_tokens

    return transcript


def parse_events(text: str) -> SessionTranscript:
    transcript: SessionTranscript | None = None
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        transcript = parse_stream(line, transcript)
    return transcript or SessionTranscript(session_id="")
