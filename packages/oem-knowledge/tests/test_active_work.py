from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_knowledge.runtime.active_work import (
    is_continuation_prompt,
    resolve_active_work,
    ActiveWorkResult,
)


def test_is_continuation_prompt_matches_variants():
    assert is_continuation_prompt("continue") is True
    assert is_continuation_prompt("continue working") is True
    assert is_continuation_prompt("what did we do so far") is True
    assert is_continuation_prompt("where were we") is True
    assert is_continuation_prompt("session start oem and continue") is True


def test_is_continuation_prompt_noop_for_unrelated():
    assert is_continuation_prompt("hello there") is False
    assert is_continuation_prompt("fix the login bug") is False
    assert is_continuation_prompt("") is False


def test_resolve_active_work_empty(tmp_path: Path):
    result = resolve_active_work(tmp_path)
    assert result.has_active_work is False
    assert result.items == []
    assert result.score == 0.0


def test_resolve_active_work_finds_todos(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "todos.json").write_text(
        json.dumps([{"content": "Fix login bug", "status": "in_progress"}]),
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert result.has_active_work is True
    assert any(item.source == "todos.json" for item in result.items)


def test_resolve_active_work_finds_context(tmp_path: Path):
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "context.md").write_text(
        "Working on user authentication flow\n",
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert result.has_active_work is True
    assert any(item.source == "context.md" for item in result.items)


def test_resolve_active_work_finds_handoff(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "session-handoff.md").write_text(
        "Finishing the payment module\n",
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert result.has_active_work is True
    assert any(item.source == "session-handoff.md" for item in result.items)


def test_resolve_active_work_detects_contradiction(tmp_path: Path):
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "context.md").write_text(
        "Working on user authentication\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "session-handoff.md").write_text(
        "Finishing the payment flow\n",
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert len(result.contradictions) >= 1


def test_resolve_active_work_no_contradiction_when_same(tmp_path: Path):
    runtime_dir = tmp_path / ".runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "context.md").write_text(
        "Working on user authentication\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "session-handoff.md").write_text(
        "Working on user authentication\n",
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert len(result.contradictions) == 0


def test_resolve_active_work_events_tail(tmp_path: Path):
    events_dir = tmp_path / "events"
    events_dir.mkdir(parents=True)
    with (events_dir / "events.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "observation", "summary": "Found bug"}) + "\n")
        f.write(json.dumps({"type": "decision", "summary": "Fixed bug"}) + "\n")

    result = resolve_active_work(tmp_path)

    assert result.has_active_work is True
    assert any(item.source == "events.jsonl" for item in result.items)


def test_resolve_active_work_outcomes_tail(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    with (state_dir / "outcomes.jsonl").open("w", encoding="utf-8") as f:
        f.write(json.dumps({"outcome": "success", "session_id": "s1"}) + "\n")
        f.write(json.dumps({"outcome": "success", "session_id": "s2"}) + "\n")

    result = resolve_active_work(tmp_path)

    assert result.has_active_work is True
    assert any(item.source == "outcomes.jsonl" for item in result.items)


def test_resolve_active_work_score_capped(tmp_path: Path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "todos.json").write_text(
        json.dumps([{"content": "Task 1", "status": "in_progress"},
                     {"content": "Task 2", "status": "in_progress"},
                     {"content": "Task 3", "status": "in_progress"},
                     {"content": "Task 4", "status": "in_progress"}]),
        encoding="utf-8",
    )

    result = resolve_active_work(tmp_path)

    assert result.score <= 10.0
