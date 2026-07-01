from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_knowledge.runtime.active_work import (
    is_continuation_prompt,
    resolve_active_work,
    resolve_active_project,
    resolve_active_work_identity,
    classify_active_work_value,
    _normalize_project_identity,
    _parse_project_from_handoff_md,
    _parse_project_from_context_md,
    _parse_project_from_handoff_json,
    _parse_project_from_outcome,
    _is_conservative_project_identifier,
    ActiveWorkResult,
    ActiveProjectResult,
    SOURCE_HANDOFF_JSON,
    SOURCE_HANDOFF_MD,
    SOURCE_STATE_HANDOFF_MD,
    SOURCE_RUNTIME_CONTEXT,
    SOURCE_LATEST_OUTCOME,
    SOURCE_ACTIVE_SESSION,
)
from oem_knowledge.preflight.router import run_preflight


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
        # Legacy wrapper may surface None for header-only sources; accept both
        assert result.sources[0].evidence in (None, "no_explicit_project_marker")

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


# ---------------------------------------------------------------------------
# P0 Semantic model tests
# ---------------------------------------------------------------------------

def test_active_session_project_classified_as_workspace_root(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "active_session.json").write_text(json.dumps({"project": "/home/xpajonx/projects/X_autoresearch"}), encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.workspace_root == "/home/xpajonx/projects/X_autoresearch"
    assert ident.active_work_item is None


def test_runtime_context_file_classified_as_active_work_item(tmp_path: Path):
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: 2_Essay/expertise-debt/Essay_ID.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item == "2_Essay/expertise-debt/Essay_ID.md"


def test_workspace_root_not_compared_to_active_work_item(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({"workspace_root": "/home/xpajonx/projects/X_autoresearch"}), encoding="utf-8")
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: 2_Essay/expertise-debt/Essay_ID.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert len(ident.conflicts) == 0
    assert ident.workspace_root == "/home/xpajonx/projects/X_autoresearch"
    assert ident.active_work_item == "2_Essay/expertise-debt/Essay_ID.md"


def test_memory_root_not_compared_to_active_work_item(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({
        "workspace_root": "/home/xpajonx/projects/X_autoresearch",
        "memory_root": "/home/xpajonx/projects/X_autoresearch/.oem"
    }), encoding="utf-8")
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: 2_Essay/expertise-debt/Essay_ID.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert len(ident.conflicts) == 0


def test_same_field_active_work_item_conflict_warns(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({"active_work_item": "A.md"}), encoding="utf-8")
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: B.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert any(c.semantic_field == "active_work_item" and c.severity in ("warning", "error") for c in ident.conflicts)


def test_three_way_active_work_item_conflict_errors(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({"active_work_item": "A.md"}), encoding="utf-8")
    (tmp_path / "session-handoff.md").write_text("Active work item: B.md\n", encoding="utf-8")
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: C.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert any(c.semantic_field == "active_work_item" and c.severity == "error" for c in ident.conflicts)


def test_markdown_header_ignored_as_active_work(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text("# Session Handoff\n\nSome text\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item is None
    assert ident.active_topic is None


def test_legacy_project_field_classified_by_value_shape(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({"project": "2_Essay/foo/Essay.md"}), encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item == "2_Essay/foo/Essay.md"


def test_legacy_active_project_alias_does_not_fallback_to_workspace_root(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "active_session.json").write_text(json.dumps({"project": "/home/xpajonx/projects/X_autoresearch"}), encoding="utf-8")
    rt = tmp_path / ".runtime"
    rt.mkdir()
    (rt / "context.md").write_text("Active work item: 2_Essay/expertise-debt/Essay_ID.md\n", encoding="utf-8")
    result = resolve_active_project(tmp_path)
    # legacy alias must be the work item, never the workspace root
    assert result.latest_project == "2_Essay/expertise-debt/Essay_ID.md"
    assert "/home/xpajonx/projects/X_autoresearch" not in (result.latest_project or "")


def test_resolve_active_project_wrapper_uses_active_work_model(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({"workspace_root": "/r", "active_work_item": "foo.md"}), encoding="utf-8")
    legacy = resolve_active_project(tmp_path)
    ident = resolve_active_work_identity(tmp_path)
    assert legacy.latest_project == "foo.md"
    assert ident.active_work_item == "foo.md"


def test_field_confidence_can_differ_within_same_source(tmp_path: Path):
    workspace_root = "/tmp/oem-ci-nonexistent/X_autoresearch"
    (tmp_path / "session-handoff.md").write_text(
        f"Workspace root: {workspace_root}\n"
        "Next action: polish 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    # workspace high, active_work_item from Next action should be medium
    ws = None
    awi = None
    for s in ident.sources:
        if s.source == "session_handoff_md":
            ws = s.fields.get("workspace_root")
            awi = s.fields.get("active_work_item")
    assert ws is not None and ws.confidence == "high"
    # Next action path -> medium per parser
    assert awi is not None and awi.confidence in ("medium", "high")
    assert ident.workspace_root == workspace_root


def test_nonexistent_relative_md_path_classified_as_active_work_item(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text("Active work item: 2_Essay/expertise-debt/final_revision.md\n", encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item == "2_Essay/expertise-debt/final_revision.md"


def test_preflight_reason_uses_active_work_resolved(tmp_path: Path):
    # Minimal preflight fixture inline
    project = tmp_path / "pp"
    oem = project / ".oem"
    oem.mkdir(parents=True)
    (oem / "skills").mkdir()
    (oem / "skill_candidates").mkdir()
    (oem / "wiki").mkdir()
    (oem / "state").mkdir()
    import json
    (oem / "manifest.json").write_text(json.dumps({"schema_version": 1, "project_id": "pp"}), encoding="utf-8")
    (oem / "concept_registry.json").write_text("{}", encoding="utf-8")
    (oem / "source_manifest.json").write_text(json.dumps({"version": 1, "files": []}), encoding="utf-8")
    # Seed with workspace + active work item
    (oem / "session-handoff.json").write_text(json.dumps({
        "workspace_root": str(project),
        "active_work_item": "2_Essay/expertise-debt/Essay_ID.md"
    }), encoding="utf-8")
    result = run_preflight("continue working on the current project", project=str(project), write_audit=False)
    assert result.decision in ("suggest", "required")
    assert "active_work" in (result.reason or "")


def test_health_contradiction_type_is_field_specific(tmp_path: Path):
    # Place files under proper .oem harness so resolver finds them
    oem_dir = tmp_path / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "session-handoff.json").write_text(json.dumps({"active_work_item": "A.md"}), encoding="utf-8")
    rt = oem_dir / ".runtime"
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "context.md").write_text("Active work item: B.md\n", encoding="utf-8")
    from oem_knowledge.health import build_health_report
    rep = build_health_report(str(tmp_path), include_daemon_runtime=False)
    types = [c.get("type") for c in rep.get("contradictions", [])]
    # Field-specific type (or legacy_type present)
    assert any((t and "active_work_item" in str(t)) or (c.get("legacy_type") == "active_project_mismatch")
               for c in rep.get("contradictions", []) for t in [c.get("type")])


# ---------------------------------------------------------------------------
# P1 Bug #5: Active work markdown source parsing regression
# ---------------------------------------------------------------------------


def test_active_work_parses_root_session_handoff_md_current_project_state(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\n"
        "## Current Project State\n\n"
        "Primary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    md = next((s for s in ident.sources if s.source == SOURCE_HANDOFF_MD), None)
    assert md is not None
    assert md.fields != {}
    assert ident.active_topic == "x-becoming-television"


def test_active_work_parses_runtime_context_md_active_project(tmp_path: Path):
    rt = tmp_path / ".runtime"
    rt.mkdir(parents=True)
    (rt / "context.md").write_text(
        "# OEM Runtime Context\n\n"
        "Active project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    md = next((s for s in ident.sources if s.source == SOURCE_RUNTIME_CONTEXT), None)
    assert md is not None
    assert md.fields != {}
    assert ident.active_work_item == "2_Essay/expertise-debt/Essay_ID.md"


def test_active_work_json_does_not_short_circuit_markdown_sources(tmp_path: Path):
    (tmp_path / "session-handoff.json").write_text(json.dumps({
        "workspace_root": "/home/xpajonx/projects/X_autoresearch",
        "active_topic": "X_autoresearch"
    }), encoding="utf-8")
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\n"
        "## Current Project State\n\n"
        "Primary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    rt = tmp_path / ".runtime"
    rt.mkdir(parents=True)
    (rt / "context.md").write_text(
        "# OEM Runtime Context\n\n"
        "Active project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    sources = {s.source: s for s in ident.sources}
    assert SOURCE_HANDOFF_JSON in sources
    assert SOURCE_HANDOFF_MD in sources
    assert SOURCE_RUNTIME_CONTEXT in sources
    assert sources[SOURCE_HANDOFF_JSON].fields != {}
    assert sources[SOURCE_HANDOFF_MD].fields != {}
    assert sources[SOURCE_RUNTIME_CONTEXT].fields != {}


def test_active_work_sources_include_md_fields(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\nPrimary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    rt = tmp_path / ".runtime"
    rt.mkdir(parents=True)
    (rt / "context.md").write_text(
        "Active project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    md_sources = [s for s in ident.sources if s.source in (SOURCE_HANDOFF_MD, SOURCE_RUNTIME_CONTEXT)]
    for s in md_sources:
        assert len(s.fields) > 0, f"{s.source} has empty fields"


def test_health_detects_session_handoff_vs_runtime_context_conflict(tmp_path: Path):
    oem_dir = tmp_path / ".oem"
    oem_dir.mkdir(parents=True, exist_ok=True)
    (oem_dir / "session-handoff.md").write_text(
        "# Session Handoff\n\nPrimary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    rt = oem_dir / ".runtime"
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "context.md").write_text(
        "Active project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    from oem_knowledge.health import build_health_report
    rep = build_health_report(str(tmp_path), include_daemon_runtime=False)
    types = [c.get("type") for c in rep.get("contradictions", [])]
    assert "active_work_source_disagreement" in types


def test_active_session_project_remains_workspace_root_not_active_work_item(tmp_path: Path):
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "active_session.json").write_text(json.dumps({
        "project": "/home/xpajonx/projects/X_autoresearch"
    }), encoding="utf-8")
    ident = resolve_active_work_identity(tmp_path)
    assert ident.workspace_root == "/home/xpajonx/projects/X_autoresearch"
    assert ident.active_work_item is None
    src = next((s for s in ident.sources if s.source == SOURCE_ACTIVE_SESSION), None)
    assert src is not None
    assert "workspace_root" in src.fields
    assert src.fields["workspace_root"].value == "/home/xpajonx/projects/X_autoresearch"


def test_markdown_headers_are_not_used_as_project_identity(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\n"
        "## Current Project State\n\n"
        "Some description here.\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item is None
    assert ident.active_topic is None
    md = next((s for s in ident.sources if s.source == SOURCE_HANDOFF_MD), None)
    if md is not None:
        assert md.fields == {}


def test_primary_objective_classified_as_topic_or_task_not_workspace_root(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\nPrimary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_topic == "x-becoming-television"
    assert ident.workspace_root is None


def test_primary_objective_path_classified_as_active_work_item(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\nPrimary objective: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item == "2_Essay/expertise-debt/Essay_ID.md"
    assert ident.active_topic is None


def test_open_project_sentence_ignores_generic_prose(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\nThe open project should feel simple and clear.\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    assert ident.active_work_item is None
    assert ident.active_topic is None


def test_cross_field_markdown_disagreement_is_warning_not_error(tmp_path: Path):
    (tmp_path / "session-handoff.md").write_text(
        "# Session Handoff\n\nPrimary objective: x-becoming-television\n",
        encoding="utf-8"
    )
    rt = tmp_path / ".runtime"
    rt.mkdir(parents=True)
    (rt / "context.md").write_text(
        "Active project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8"
    )
    ident = resolve_active_work_identity(tmp_path)
    disagreements = [c for c in ident.conflicts if c.type == "active_work_source_disagreement"]
    assert len(disagreements) >= 1
    for d in disagreements:
        assert d.severity == "warning"


def test_handoff_writer_does_not_invent_active_work_item_from_workspace(tmp_path: Path):
    from oem_knowledge.services.state import StateService
    # simulate harness
    harness = tmp_path / ".oem"
    harness.mkdir(parents=True, exist_ok=True)
    # engine stub with minimal surface
    class _Eng:
        def _resolve_harness(self, p=None):
            return harness
    svc = StateService(_Eng())  # type: ignore
    svc._update_structured_handoff(harness, "/home/xpajonx/projects/X_autoresearch", "s1")
    data = json.loads((harness / "session-handoff.json").read_text(encoding="utf-8"))
    assert data.get("workspace_root") is not None
    # Must not have invented an active_work_item
    assert data.get("active_work_item") in (None, "", "null")
    assert "2_Essay" not in str(data)
