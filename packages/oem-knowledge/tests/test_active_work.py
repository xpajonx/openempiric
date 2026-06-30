from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_knowledge.runtime.active_work import (
    is_continuation_prompt,
    resolve_active_work,
    resolve_active_project,
    _normalize_project_identity,
    _parse_project_from_handoff_md,
    _parse_project_from_context_md,
    _parse_project_from_handoff_json,
    _parse_project_from_outcome,
    ActiveWorkResult,
    ActiveProjectResult,
    SOURCE_HANDOFF_JSON,
    SOURCE_HANDOFF_MD,
    SOURCE_STATE_HANDOFF_MD,
    SOURCE_RUNTIME_CONTEXT,
    SOURCE_LATEST_OUTCOME,
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


def test_resolve_active_work_ignores_topic_text_difference(tmp_path: Path):
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

    assert result.contradictions == []


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


# ---------------------------------------------------------------------------
# Resolve active project tests
# ---------------------------------------------------------------------------

class TestResolveActiveProject:
    def test_root_json_over_markdown(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text(json.dumps({
            "schema_version": "1.0.0",
            "project_root": "/home/user/my-project",
            "updated_at": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Primary objective: something else\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert result.latest_project is not None
        assert result.selected_source == SOURCE_HANDOFF_JSON
        assert _normalize_project_identity(result.latest_project) == _normalize_project_identity("/home/user/my-project")

    def test_keeps_state_handoff_compatibility(self, tmp_path: Path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        md_path = state_dir / "session-handoff.md"
        md_path.write_text("Project: state-project\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert result.latest_project is not None
        assert result.selected_source == SOURCE_STATE_HANDOFF_MD

    def test_primary_objective_not_used_as_project_identity(self, tmp_path: Path):
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Primary objective: fix onboarding copy\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert result.selected_source != SOURCE_HANDOFF_MD or result.latest_project is None

    def test_explicit_project_marker_used_over_primary_objective(self, tmp_path: Path):
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text(
            "Project: the-real-project\n"
            "Primary objective: fix onboarding copy\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)
        assert result.latest_project == "the-real-project"
        assert result.selected_source == SOURCE_HANDOFF_MD

    def test_normalizes_trailing_slash_paths(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text(json.dumps({
            "project_root": "/home/user/my-project/",
        }), encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Project: /home/user/my-project\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert len(result.conflicts) == 0

    def test_does_not_false_positive_same_project_path(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text(json.dumps({
            "project_root": "/home/user/project-alpha",
        }), encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Project: /home/user/project-beta\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert len(result.conflicts) >= 1

    def test_malformed_json_falls_back_to_markdown_with_warning(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text("not valid json {{", encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Project: markdown-project\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        assert len(result.warnings) >= 1
        assert result.warnings[0]["reason"] == "malformed_handoff_json"
        assert result.latest_project == "markdown-project"

    def test_2way_high_confidence_conflict(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text(json.dumps({"project_root": "/project/alpha"}), encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Project: /project/beta\n", encoding="utf-8")
        ctx_dir = tmp_path / ".runtime"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "context.md").write_text("Project: /project/alpha\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        has_2way = any(c.severity == "warning" for c in result.conflicts)
        assert has_2way

    def test_3way_high_confidence_conflict(self, tmp_path: Path):
        json_path = tmp_path / "session-handoff.json"
        json_path.write_text(json.dumps({"project_root": "/project/alpha"}), encoding="utf-8")
        md_path = tmp_path / "session-handoff.md"
        md_path.write_text("Project: /project/beta\n", encoding="utf-8")
        ctx_dir = tmp_path / ".runtime"
        ctx_dir.mkdir(parents=True)
        (ctx_dir / "context.md").write_text("Project: /project/gamma\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)
        has_3way = any(c.severity == "error" for c in result.conflicts)
        assert has_3way

    def test_health_extracts_project_from_session_handoff_json(self, tmp_path: Path):
        (tmp_path / "session-handoff.json").write_text(
            json.dumps({"project_root": "2_Essay/expertise-debt/Essay_ID.md"}),
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.latest_project == "2_Essay/expertise-debt/Essay_ID.md"
        assert result.selected_source == SOURCE_HANDOFF_JSON
        source = result.sources[0]
        assert source.confidence == "high"
        assert source.evidence == "project_root"

    def test_health_extracts_project_from_runtime_context_marker(self, tmp_path: Path):
        runtime_dir = tmp_path / ".runtime"
        runtime_dir.mkdir()
        (runtime_dir / "context.md").write_text(
            "# OEM Runtime Context\n\nActive project: x-becoming-television\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.latest_project == "x-becoming-television"
        assert result.selected_source == SOURCE_RUNTIME_CONTEXT
        source = result.sources[0]
        assert source.confidence == "high"
        assert source.evidence == "Active project:"

    def test_health_ignores_markdown_header_as_project(self, tmp_path: Path):
        (tmp_path / "session-handoff.md").write_text(
            "# Session Handoff\n\n## Historical Context\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.latest_project is None
        assert result.conflicts == []
        assert result.sources[0].confidence == "low"
        assert result.sources[0].project is None

    def test_health_does_not_compare_session_handoff_header(self, tmp_path: Path):
        runtime_dir = tmp_path / ".runtime"
        runtime_dir.mkdir()
        (runtime_dir / "context.md").write_text(
            "# OEM Runtime Context\n\nOpenEmpiric project memory is active for this repository.\n",
            encoding="utf-8",
        )
        (tmp_path / "session-handoff.md").write_text(
            "# Session Handoff\n\nInitialized project layout.\n",
            encoding="utf-8",
        )

        active_work = resolve_active_work(tmp_path)
        active_project = resolve_active_project(tmp_path)

        assert active_project.conflicts == []
        assert active_work.contradictions == []

    def test_health_next_action_without_path_not_project_identity(self, tmp_path: Path):
        (tmp_path / "session-handoff.md").write_text(
            "Next action: continue writing\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.latest_project is None
        assert result.conflicts == []

    def test_health_next_action_with_file_path_is_project_signal(self, tmp_path: Path):
        (tmp_path / "session-handoff.md").write_text(
            "Next action: polish 2_Essay/expertise-debt/Essay_ID.md\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.latest_project == "2_Essay/expertise-debt/Essay_ID.md"
        assert result.sources[0].confidence == "medium"
        assert result.sources[0].evidence == "Next action:"

    def test_health_source_confidence_high_for_explicit_active_project_marker(self, tmp_path: Path):
        (tmp_path / "session-handoff.md").write_text(
            "Active project: x-becoming-television\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.sources[0].confidence == "high"
        assert result.sources[0].evidence == "Active project:"

    def test_health_source_confidence_low_for_header_ignored(self, tmp_path: Path):
        (tmp_path / "session-handoff.md").write_text(
            "# Session Handoff\n",
            encoding="utf-8",
        )

        result = resolve_active_project(tmp_path)

        assert result.sources[0].confidence == "low"
        assert result.sources[0].evidence == "no_explicit_project_marker"

    def test_malformed_handoff_json_warns_even_when_markdown_fallback_succeeds(self, tmp_path: Path):
        (tmp_path / "session-handoff.json").write_text("not valid json {{", encoding="utf-8")
        (tmp_path / "session-handoff.md").write_text("Project: markdown-project\n", encoding="utf-8")

        result = resolve_active_project(tmp_path)

        assert result.latest_project == "markdown-project"
        assert result.selected_source == SOURCE_HANDOFF_MD
        assert any(w.get("reason") == "malformed_handoff_json" for w in result.warnings)


class TestParseProjectFromHandoffMd:
    def test_project_marker(self):
        project, conf = _parse_project_from_handoff_md("Project: my-app\n")
        assert project == "my-app"
        assert conf == "high"

    def test_active_project_marker(self):
        project, conf = _parse_project_from_handoff_md("- Active project: my-app\n")
        assert project == "my-app"
        assert conf == "high"

    def test_project_root_marker(self):
        project, conf = _parse_project_from_handoff_md("- Project root: /home/user/my-app\n")
        assert project == "/home/user/my-app"
        assert conf == "high"

    def test_primary_objective_low_confidence(self):
        project, conf = _parse_project_from_handoff_md("Primary objective: fix bug\n")
        assert project is None
        assert conf == "low"


class TestParseProjectFromContextMd:
    def test_open_project_pattern(self):
        project, conf = _parse_project_from_context_md(
            "- 2_Essay/expertise-debt/Essay_ID.md is the open project.\n"
        )
        assert project is not None
        assert "expertise-debt" in project
        assert conf == "high"


class TestNormalizeProjectIdentity:
    def test_strips_trailing_slash(self):
        n1 = _normalize_project_identity("/home/user/project/")
        n2 = _normalize_project_identity("/home/user/project")
        assert n1 == n2

    def test_handles_none(self):
        assert _normalize_project_identity(None) == ""

    def test_handles_empty(self):
        assert _normalize_project_identity("") == ""


class TestParseProjectFromHandoffJson:
    def test_project_root(self):
        data = {"project_root": "/home/user/app"}
        proj, conf, label = _parse_project_from_handoff_json(data)
        assert proj == "/home/user/app"
        assert conf == "high"

    def test_project_label(self):
        data = {"project_root": "/home/user/app", "project_label": "My App"}
        proj, conf, evidence = _parse_project_from_handoff_json(data)
        assert proj == "/home/user/app"
        assert conf == "high"
        assert evidence == "project_root"

    def test_project_label_without_root(self):
        data = {"project_label": "My App"}
        proj, conf, evidence = _parse_project_from_handoff_json(data)
        assert proj == "My App"
        assert conf == "high"
        assert evidence == "project_label"

    def test_non_dict(self):
        proj, conf, label = _parse_project_from_handoff_json("string")
        assert proj is None
        assert conf == "low"


class TestParseProjectFromOutcome:
    def test_project_field(self):
        proj, conf = _parse_project_from_outcome({"project": "my-app", "outcome": "success"})
        assert proj == "my-app"
        assert conf == "high"

    def test_no_project_field(self):
        proj, conf = _parse_project_from_outcome({"outcome": "success"})
        assert proj is None
        assert conf == "low"


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
