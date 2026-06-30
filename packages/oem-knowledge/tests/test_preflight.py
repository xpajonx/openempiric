from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from oem_knowledge.preflight.budget import ContextBudget
from oem_knowledge.preflight.router import run_preflight
from oem_knowledge.server import ProjectMismatchError
from oem_knowledge.server import SESSION_TO_PROJECT
from oem_knowledge.source_classifier import SourceType, classify_source


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _create_memory_db(db_path: Path, rows: list[tuple[str, str, dict]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path))
    connection.execute(
        """
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            document TEXT NOT NULL,
            metadata TEXT NOT NULL,
            embedding TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO chunks (id, document, metadata, embedding) VALUES (?, ?, ?, ?)",
        [(row_id, document, json.dumps(metadata), None) for row_id, document, metadata in rows],
    )
    connection.commit()
    connection.close()


def _file_snapshot(path: Path) -> tuple[bool, int | None, int | None, str | None]:
    if not path.exists():
        return False, None, None, None
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return True, stat.st_size, stat.st_mtime_ns, digest


def _tree_snapshot(root: Path) -> dict[str, tuple[bool, int | None, int | None, str | None]]:
    snapshot: dict[str, tuple[bool, int | None, int | None, str | None]] = {}
    if root.exists():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            snapshot[str(path.relative_to(root))] = _file_snapshot(path)
    return snapshot


@pytest.fixture
def preflight_project(tmp_path: Path) -> Path:
    project = tmp_path / "preflight-project"
    oem = project / ".oem"
    (oem / "skills").mkdir(parents=True)
    (oem / "skill_candidates").mkdir(parents=True)
    (oem / "wiki").mkdir(parents=True)
    (oem / "state").mkdir(parents=True)
    _write_json(
        oem / "manifest.json",
        {
            "schema_version": 1,
            "project_id": "preflight-project",
            "memory_root": ".oem",
        },
    )
    _write_json(
        oem / "concept_registry.json",
        {
            "concept_materialization": {
                "canonical_name": "materialization collision",
                "aliases": ["materialization", "collision"],
                "tags": ["materialization", "wiki"],
                "status": "validated",
                "description": "Materialization collision handling rules.",
            },
            "concept_old": {
                "canonical_name": "deprecated copy path",
                "aliases": ["legacy copy"],
                "status": "deprecated",
            },
        },
    )
    _write_skill(
        oem / "skills" / "ccb-calendar-copy.md",
        """
        ---
        type: oem_skill
        id: skill_ccb_calendar_copy
        title: CCB Calendar Copy Style
        status: approved
        triggers:
          - CCB
          - calendar copy
          - SME loans
        tags:
          - copy
          - calendar
        aliases:
          - ccb calendar
        ---

        # CCB Calendar Copy Style

        Use warm partner tone. Avoid product dumping.
        """,
    )
    _write_skill(
        oem / "skills" / "rejected-legacy.md",
        """
        ---
        id: skill_rejected
        title: Rejected Legacy Pattern
        status: rejected
        triggers:
          - legacy migration
        ---

        # Rejected Legacy Pattern

        Never use this.
        """,
    )
    _write_skill(
        oem / "skills" / "legacy-approved.md",
        """
        # Characterization Workflow

        ## Trigger
        production bug

        ## Skill
        Start with characterization tests before implementation.
        """,
    )
    _write_skill(
        oem / "skill_candidates" / "candidate-only.md",
        """
        ---
        id: candidate_only
        title: Candidate Only Pattern
        status: proposed
        triggers:
          - candidate trigger
        ---

        # Candidate Only Pattern

        Candidate content.
        """,
    )
    (oem / "wiki" / "concept_materialization.md").write_text(
        """---
concept_id: concept_materialization
title: Materialization Collision
status: validated
tags:
  - materialization
  - collision
aliases:
  - materialization implementation
summary: Prefer collision-safe materialization behavior.
---

# Materialization Collision

Prefer collision-safe materialization behavior.
""",
        encoding="utf-8",
    )
    _create_memory_db(
        oem / ".local_vector_db" / "vectors.db",
        [
            (
                "memory_1",
                "Document: .oem/wiki/concept_materialization.md\nSection: Materialization Collision\n\nPrefer collision-safe materialization behavior.",
                {"source": ".oem/wiki/concept_materialization.md", "title": "Materialization Collision"},
            ),
        ],
    )
    _write_json(
        oem / "source_manifest.json",
        {
            "version": 1,
            "files": [
                {"rel_path": "packages/oem-knowledge/src/oem_knowledge/materialization.py", "status": "indexed"}
            ],
        },
    )
    return project


def test_preflight_noop_for_trivial_prompt(preflight_project: Path):
    result = run_preflight("hello there", project=str(preflight_project), write_audit=False)

    assert result.decision == "noop"
    assert result.status == "noop"


def test_preflight_required_for_approved_skill_trigger(preflight_project: Path):
    result = run_preflight(
        "buat copy calendar CCB untuk SME loans",
        project=str(preflight_project),
        write_audit=False,
    )

    assert result.decision == "required"
    assert result.matched_skills[0].title == "CCB Calendar Copy Style"


def test_preflight_suggest_for_concept_match(preflight_project: Path):
    result = run_preflight(
        "please review the materialization collision behavior",
        project=str(preflight_project),
        write_audit=False,
    )

    assert result.decision == "suggest"
    assert result.matched_concepts[0].title == "Materialization Collision"


def test_preflight_ignores_rejected_skill(preflight_project: Path):
    result = run_preflight("legacy migration", project=str(preflight_project), write_audit=False)

    assert all(match.title != "Rejected Legacy Pattern" for match in result.matched_skills)


def test_preflight_context_is_bounded(preflight_project: Path):
    result = run_preflight(
        "CCB calendar copy for SME loans and materialization collision",
        project=str(preflight_project),
        write_audit=False,
        budget=ContextBudget(max_context_chars=180, max_skills=3, max_concepts=3, max_memory_items=3, max_source_suggestions=3),
    )

    assert len(result.context) <= 180
    assert any("Context truncated" in warning for warning in result.warnings)


def test_preflight_result_is_deterministic(preflight_project: Path):
    left = run_preflight("CCB calendar copy for SME loans", project=str(preflight_project), write_audit=False)
    right = run_preflight("CCB calendar copy for SME loans", project=str(preflight_project), write_audit=False)

    assert left == right


def test_preflight_uses_active_project_binding(preflight_project: Path):
    SESSION_TO_PROJECT["session-preflight"] = preflight_project.resolve()
    try:
        result = run_preflight("CCB calendar copy", session_id="session-preflight", write_audit=False)
    finally:
        SESSION_TO_PROJECT.clear()

    assert result.project_root == str(preflight_project.resolve())
    assert result.decision == "required"


def test_preflight_never_defaults_to_oem_dev_repo(preflight_project: Path, tmp_path: Path):
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    (other_project / ".oem").mkdir()

    with patch(
        "oem_knowledge.preflight.router.resolve_active_project",
        side_effect=ProjectMismatchError(str(preflight_project), str(other_project)),
    ):
        result = run_preflight("CCB calendar copy", write_audit=False)

    assert result.decision == "blocked"
    assert "project mismatch" in result.reason


def test_preflight_blocks_on_unresolved_project():
    with patch.dict(os.environ, {"PWD": ""}, clear=True):
        with patch("os.getcwd", return_value="/tmp/nonexistent_project_dir_random_123"):
            result = run_preflight("any task", write_audit=False)

    assert result.decision == "blocked"
    assert "project unresolved" in result.reason


def test_preflight_blocks_when_oem_is_missing(tmp_path: Path):
    missing_project = tmp_path / "missing-oem"
    missing_project.mkdir()

    result = run_preflight("any task", project=str(missing_project), write_audit=False)

    assert result.decision == "blocked"
    assert ".oem missing" in result.reason


def test_preflight_does_not_create_concepts(preflight_project: Path):
    before = _file_snapshot(preflight_project / ".oem" / "concept_registry.json")

    run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=False)

    after = _file_snapshot(preflight_project / ".oem" / "concept_registry.json")
    assert before == after


def test_preflight_does_not_write_reflection_events(preflight_project: Path):
    events_file = preflight_project / ".oem" / "events.jsonl"
    runtime_file = preflight_project / ".oem" / "runtime_events.jsonl"
    outcomes_file = preflight_project / ".oem" / "outcomes.jsonl"
    before = (_file_snapshot(events_file), _file_snapshot(runtime_file), _file_snapshot(outcomes_file))

    run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=False)

    after = (_file_snapshot(events_file), _file_snapshot(runtime_file), _file_snapshot(outcomes_file))
    assert before == after


def test_preflight_does_not_materialize_wiki(preflight_project: Path):
    before = _tree_snapshot(preflight_project / ".oem" / "wiki")

    run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=False)

    after = _tree_snapshot(preflight_project / ".oem" / "wiki")
    assert before == after


def test_preflight_audit_log_is_written_when_enabled(preflight_project: Path):
    result = run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=True)
    audit_path = preflight_project / ".oem" / "preflight" / "preflight_events.jsonl"

    assert result.decision == "required"
    assert audit_path.exists()
    event = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["decision"] == "required"
    assert event["matched_skill_ids"] == ["skill_ccb_calendar_copy"]


def test_preflight_audit_log_is_non_ingestion_eligible():
    classification = classify_source(".oem/preflight/preflight_events.jsonl")

    assert classification.source_type == SourceType.OEM_PREFLIGHT
    assert classification.ingestion_eligible is False


def test_preflight_memory_index_is_opened_strictly_read_only(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    before = _file_snapshot(db_path)

    run_preflight("materialization collision", project=str(preflight_project), write_audit=False)

    after = _file_snapshot(db_path)
    assert before == after


def test_preflight_memory_index_missing_warns_and_continues(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    db_path.unlink()

    result = run_preflight("materialization collision", project=str(preflight_project), write_audit=False)

    assert result.decision == "suggest"
    assert any("Memory index unavailable" in warning for warning in result.warnings)


def test_preflight_audit_failure_does_not_change_decision(preflight_project: Path):
    baseline = run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=False)

    with patch("oem_knowledge.preflight.router.write_audit_event", side_effect=OSError("disk full")):
        result = run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=True)

    assert result.decision == baseline.decision
    assert result.context == baseline.context
    assert any("Preflight audit write failed" in warning for warning in result.warnings)


def test_preflight_parses_legacy_skill_without_frontmatter(preflight_project: Path):
    result = run_preflight("production bug", project=str(preflight_project), write_audit=False)

    assert any(match.title == "Characterization Workflow" for match in result.matched_skills)


def test_preflight_no_mutation_snapshot_allows_only_audit_log(preflight_project: Path):
    tracked_files = [
        preflight_project / ".oem" / "concept_registry.json",
        preflight_project / ".oem" / "events.jsonl",
        preflight_project / ".oem" / "runtime_events.jsonl",
        preflight_project / ".oem" / "outcomes.jsonl",
    ]
    tracked_dirs = [
        preflight_project / ".oem" / "wiki",
        preflight_project / ".oem" / "skills",
        preflight_project / ".oem" / "skill_candidates",
    ]
    before_files = {str(path): _file_snapshot(path) for path in tracked_files}
    before_dirs = {str(path): _tree_snapshot(path) for path in tracked_dirs}

    run_preflight("CCB calendar copy", project=str(preflight_project), write_audit=True)

    after_files = {str(path): _file_snapshot(path) for path in tracked_files}
    after_dirs = {str(path): _tree_snapshot(path) for path in tracked_dirs}
    assert before_files == after_files
    assert before_dirs == after_dirs
    assert (preflight_project / ".oem" / "preflight" / "preflight_events.jsonl").exists()


def test_preflight_continuation_prompt_returns_required_with_active_work(preflight_project: Path):
    state_dir = preflight_project / ".oem" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        state_dir / "todos.json",
        [{"content": "Implement active-work resolver", "status": "in_progress"}],
    )

    result = run_preflight("continue working on the project", project=str(preflight_project), write_audit=False)

    assert result.decision == "required"
    assert "active work detected" in result.reason


def test_preflight_continuation_noop_without_any_active_work_signal(preflight_project: Path):
    result = run_preflight("continue", project=str(preflight_project), write_audit=False)

    # No active work files exist in the fixture, so it should be noop
    assert result.decision == "noop"


def test_preflight_continuation_contradiction_adds_warning(preflight_project: Path):
    state_dir = preflight_project / ".oem" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        state_dir / "todos.json",
        [{"content": "Build feature X", "status": "in_progress"}],
    )
    runtime_dir = preflight_project / ".oem" / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text("Working on user authentication\n", encoding="utf-8")
    (state_dir / "session-handoff.md").write_text("Finishing the payment flow\n", encoding="utf-8")

    result = run_preflight("continue where I left off", project=str(preflight_project), write_audit=False)

    assert any("Active-work contradiction" in w for w in result.warnings)


def test_preflight_memory_match_affects_decision(preflight_project: Path):
    result = run_preflight("materialization collision behavior", project=str(preflight_project), write_audit=False)

    assert result.decision == "suggest"
    assert any(m.title == "Materialization Collision" for m in result.matched_memory)


def test_preflight_noop_for_trivial_continuation_without_active_work(preflight_project: Path):
    result = run_preflight("continue", project=str(preflight_project), write_audit=False)

    assert result.decision in ("noop", "suggest")


def test_preflight_frontmatter_beyond_16000_chars_does_not_warn(preflight_project: Path):
    wiki_concept = preflight_project / ".oem" / "wiki" / "concept_large.md"
    long_ids = "source_event_ids:\n  - " + "\n  - ".join([f"ev_{i}" for i in range(2000)])
    content = f"""---
concept_id: concept_large
title: Large Concept
status: validated
{long_ids}
---

# Large Concept

Body content.
"""
    wiki_concept.write_text(content, encoding="utf-8")

    result = run_preflight("large concept", project=str(preflight_project), write_audit=False)

    frontmatter_warnings = [w for w in result.warnings if "frontmatter" in w.lower()]
    assert len(frontmatter_warnings) == 0
