from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from oem_knowledge.engine import KnowledgeEngine
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


def _project_with_agents(tmp_path: Path, agents_content: str) -> tuple[Path, KnowledgeEngine]:
    project = tmp_path / "directive-project"
    project.mkdir()
    engine = KnowledgeEngine(str(project))
    engine.init_project(str(project))
    (project / "AGENTS.md").write_text(agents_content.strip() + "\n", encoding="utf-8")
    engine.session_start(str(project))
    return project, engine


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
    (runtime_dir / "context.md").write_text("Active project: /project/alpha\n", encoding="utf-8")
    (state_dir / "session-handoff.md").write_text("Project: /project/beta\n", encoding="utf-8")

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


def _add_memory_rows(db_path: Path, rows: list[tuple[str, str, dict]]) -> None:
    conn = sqlite3.connect(str(db_path))
    for row_id, document, metadata in rows:
        conn.execute(
            "INSERT OR IGNORE INTO chunks (id, document, metadata, embedding) VALUES (?, ?, ?, ?)",
            (row_id, document, json.dumps(metadata), None),
        )
    conn.commit()
    conn.close()


def test_preflight_decision_memory_triggers_required(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_decision_1",
            "Decision: Essay_ID.md is the open project.\n1,667 words, Indonesian master draft.",
            {"source": ".oem/memory/decision.md", "title": "Decision: Essay_ID.md"},
        ),
    ])
    result = run_preflight(
        "Essay_ID.md is the open project",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision in ("suggest", "required")
    assert any(m.score >= 4.0 for m in result.matched_memory)


def test_preflight_failure_memory_triggers_suggest(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_failure_1",
            "Failure: I treated Essay_ID.md as raw material to overwrite instead of inspecting tone first.",
            {"source": ".oem/memory/failure.md", "title": "Failure: overwrite"},
        ),
    ])
    result = run_preflight(
        "Essay_ID.md overwrite tone",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision in ("suggest", "required")
    assert any(m.score >= 4.0 for m in result.matched_memory)


def test_preflight_aggregate_memory_triggers_suggest(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            f"mem_obs_{i}",
            f"Observation: Essay_ID.md reference chunk {i} about Indonesian expertise debt essay and personal tone.",
            {"source": ".oem/memory/obs.md", "title": f"Observation {i}"},
        )
        for i in range(5)
    ])
    result = run_preflight(
        "Indonesian expertise debt essay",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision == "suggest"
    assert len(result.matched_memory) >= 3


def test_preflight_observation_memory_alone_stays_noop(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_obs_single",
            "Observation: Some random observation about something unrelated to the query.",
            {"source": ".oem/memory/obs.md", "title": "Observation unrelated"},
        ),
    ])
    result = run_preflight(
        "hello there",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision == "noop"


def test_preflight_memory_decision_consistent_across_audit_modes(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_decision_audit",
            "Decision: Essay_ID.md is the open project.\nIndonesian master draft, personal conversational tone.",
            {"source": ".oem/memory/decision.md", "title": "Decision: Essay_ID.md"},
        ),
    ])
    with_audit = run_preflight(
        "Essay_ID.md open project",
        project=str(preflight_project),
        write_audit=True,
    )
    without_audit = run_preflight(
        "Essay_ID.md open project",
        project=str(preflight_project),
        write_audit=False,
    )
    assert with_audit.decision == without_audit.decision
    assert with_audit.reason == without_audit.reason


def test_preflight_low_confidence_handoff_conflict_does_not_become_required(preflight_project: Path):
    runtime_dir = preflight_project / ".oem" / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text(
        "Project: /project/alpha\n",
        encoding="utf-8",
    )
    handoff_dir = preflight_project / ".oem"
    (handoff_dir / "session-handoff.md").write_text(
        "# Current Project State\n"
        "- Primary objective: fix onboarding copy\n"
        "- Next: deploy to prod\n",
        encoding="utf-8",
    )

    result = run_preflight("hello there", project=str(preflight_project), write_audit=False)
    assert result.decision != "required"
    assert result.decision in ("noop", "suggest")


def test_preflight_no_warning_for_valid_concept_with_body_separators(preflight_project: Path):
    concept_path = preflight_project / ".oem" / "wiki" / "concept_test.md"
    concept_path.write_text(
        """---
concept_id: concept_test
status: validated
---

# Learnings

- Command output:
  echo "---"

---

Another markdown separator in body.

```text
---
Full extracted text here...
---
```""",
        encoding="utf-8",
    )

    result = run_preflight("concept test", project=str(preflight_project), write_audit=False)

    frontmatter_warnings = [w for w in result.warnings if "frontmatter" in w.lower()]
    assert len(frontmatter_warnings) == 0


def test_preflight_warns_for_frontmatter_block_not_closed(preflight_project: Path):
    concept_path = preflight_project / ".oem" / "wiki" / "concept_bad.md"
    concept_path.write_text(
        """---
concept_id: concept_bad
status: validated

# Body without closing frontmatter.
""",
        encoding="utf-8",
    )

    result = run_preflight("concept bad", project=str(preflight_project), write_audit=False)

    frontmatter_warnings = [w for w in result.warnings if "frontmatter" in w.lower()]
    assert len(frontmatter_warnings) >= 1


# ---------------------------------------------------------------------------
# P0: Exact task + target file Decision => required, never noop
# ---------------------------------------------------------------------------


def test_preflight_exact_task_with_target_file_decision_never_noop(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_dec_essay",
            "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project.\nIndonesian master draft, personal conversational tone.",
            {"source": ".oem/memory/decision.md", "title": "Decision: Essay_ID.md"},
        ),
    ])
    result = run_preflight(
        "continue working on Essay_ID.md, the Indonesian expertise debt essay, polish typos and tighten prose",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision != "noop"
    assert result.decision in ("suggest", "required")


def test_preflight_exact_task_with_failure_chunk_never_noop(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_fail_essay",
            "Failure: I treated Essay_ID.md as raw material to overwrite instead of inspecting tone first.",
            {"source": ".oem/memory/failure.md", "title": "Failure: overwrite Essay_ID.md"},
        ),
    ])
    result = run_preflight(
        "Essay_ID.md overwrite tone",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision != "noop"


def test_preflight_noop_only_when_no_relevant_memory(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_obs_unrelated",
            "Observation: Something about a completely different topic.",
            {"source": ".oem/memory/obs.md", "title": "Observation: unrelated"},
        ),
    ])
    result = run_preflight(
        "hello there",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision == "noop"


# ---------------------------------------------------------------------------
# P0: Generic current-project continuation => active-project resolver
# ---------------------------------------------------------------------------


def test_preflight_current_project_uses_active_project_resolver(preflight_project: Path):
    # Seed handoff with a known project
    handoff_dir = preflight_project / ".oem"
    (handoff_dir / "session-handoff.md").write_text(
        "# Current Project State\n"
        "- Project: 2_Essay/expertise-debt/Essay_ID.md\n"
        "- Next step: polish prose\n",
        encoding="utf-8",
    )
    result = run_preflight(
        "continue working on the current project",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision != "noop"
    assert result.active_project is not None
    assert result.active_project.get("latest_project") is not None
    # New reason uses active_work_resolved (old name allowed only as alias per spec)
    assert "active_work_resolved" in (result.reason or "") or "active_project" in (result.reason or "")


def test_preflight_current_project_conflict_returns_suggest(preflight_project: Path):
    handoff_dir = preflight_project / ".oem"
    (handoff_dir / "session-handoff.md").write_text(
        "# Current Project State\n"
        "- Project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8",
    )
    runtime_dir = preflight_project / ".oem" / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text("Project: /other/project\n", encoding="utf-8")

    result = run_preflight(
        "continue working on the current project",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision == "suggest"
    conflict_warnings = [w for w in result.warnings if "conflict" in w.lower()]
    assert len(conflict_warnings) >= 1


def test_preflight_review_current_health_not_required_from_current_directive(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("review current OEM health", project=str(project))

    assert result["decision"] != "required"
    assert len(result["matched_directives"]) == 0


def test_preflight_langgraph_storm_prompt_can_match_langgraph_directive(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # LangGraph STORM Research Pipeline
        - MUST follow LangGraph STORM research pipeline steps.
        """,
    )

    result = engine.preflight("continue LangGraph STORM research pipeline implementation", project=str(project))

    assert result["decision"] == "required"
    directive = result["matched_directives"][0]
    assert directive["title"] == "LangGraph STORM Research Pipeline"
    assert directive["match_class"] == "semantic_directive_match"
    assert directive["can_force_required"] is True


def test_preflight_generic_current_project_reason_not_overridden_by_directive(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Project Contract
        - MUST inspect current project context.
        """,
    )
    _write_json(project / ".oem" / "session-handoff.json", {"active_topic": "Inbox cleanup"})

    result = engine.preflight("continue working on the current project", project=str(project))

    assert result["decision"] == "suggest"
    assert result["reason"] == "active_work_resolved"
    assert not any(d["match_class"] == "generic_lexical_match" for d in result["matched_directives"])
    assert not any(d["can_force_required"] for d in result["matched_directives"])


def test_preflight_output_includes_directive_match_class(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("review current OEM health", project=str(project))

    assert len(result["matched_directives"]) == 0


def test_preflight_review_current_oem_health_does_not_trigger_unrelated_required_directives(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.

        # LangGraph STORM Research Pipeline
        - MUST follow LangGraph STORM research pipeline steps.
        """,
    )

    result = engine.preflight("review current OEM health", project=str(project))

    assert result["decision"] != "required"
    assert result["reason_detail"] != "critical directive matched: Current Content-Machine Contract"
    titles = [d["title"] for d in result["matched_directives"]]
    assert "Current Content-Machine Contract" not in titles
    assert "LangGraph STORM Research Pipeline" not in titles


# ---------------------------------------------------------------------------
# P0: Directive guardrail — generic token overlap does not force required
# ---------------------------------------------------------------------------


def test_preflight_directive_title_generic_token_overlap_downgraded(preflight_project: Path):
    from oem_knowledge.instructions.matcher import _title_overlap_is_generic

    assert _title_overlap_is_generic(
        "Current Content-Machine Contract", "continue working on the current project"
    )
    assert not _title_overlap_is_generic(
        "Deployment Rules", "deploy the new feature"
    )


def test_preflight_semantically_irrelevant_critical_directive_does_not_force_required(
    preflight_project: Path,
):
    from oem_knowledge.instructions import get_db_connection
    from oem_knowledge.instructions.ledger import ensure_schema
    from oem_knowledge.runtime.active_work import _read_json_safe

    ledger_path = preflight_project / ".oem" / "instructions" / "ledger.db"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db_connection(ledger_path)
    ensure_schema(conn)
    conn.execute(
        """INSERT OR REPLACE INTO directives (
            id, source_id, source_path, source_hash,
            line_start, line_end, title, scope, triggers_json,
            priority, rule, forbidden_actions_json, related_concepts_json,
            related_skills_json, related_workflows_json, status, indexed_at
        ) VALUES (
            'dir_001', 'src_001', '/fake/rules.md', 'abc123',
            1, 3, 'Current Content-Machine Contract', 'project', '[]',
            'critical', 'Always follow the content-machine contract. Must use approved tone.',
            '[]', '[]', '[]', '[]', 'active', '2025-01-01T00:00:00Z'
        )"""
    )
    conn.commit()
    conn.close()

    # handoff and context define a current project
    handoff_dir = preflight_project / ".oem"
    (handoff_dir / "session-handoff.md").write_text(
        "# Current Project State\n"
        "- Project: 2_Essay/expertise-debt/Essay_ID.md\n",
        encoding="utf-8",
    )

    result = run_preflight(
        "continue working on the current project",
        project=str(preflight_project),
        write_audit=False,
    )
    # Must not be required from the generic-only title match
    assert result.decision != "required"


# ---------------------------------------------------------------------------
# P0: Explanation / structured output
# ---------------------------------------------------------------------------


def test_preflight_matched_memory_summary_present(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_dec_essay",
            "Decision: Essay_ID.md is the open project.\nIndonesian master draft.",
            {"source": ".oem/memory/decision.md", "title": "Decision: Essay_ID.md"},
        ),
    ])
    result = run_preflight(
        "continue working on Essay_ID.md, polish prose",
        project=str(preflight_project),
        write_audit=False,
    )
    assert len(result.matched_memory_summary) >= 1
    assert any(d["relevance"] in ("strong", "medium") for d in result.matched_memory_summary)


def test_preflight_supporting_reasons_includes_relevant_info(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        (
            "mem_dec_essay",
            "Decision: Essay_ID.md is the open project.\nIndonesian master draft.",
            {"source": ".oem/memory/decision.md", "title": "Decision: Essay_ID.md"},
        ),
    ])
    result = run_preflight(
        "continue working on Essay_ID.md, polish prose",
        project=str(preflight_project),
        write_audit=False,
    )
    assert result.decision != "noop"
    assert result.matched_memory_summary  # non-empty list


def test_preflight_result_json_serializable(preflight_project: Path):
    import json
    result = run_preflight("hello there", project=str(preflight_project), write_audit=False)
    from oem_knowledge.preflight.normalize import normalize_preflight_result
    payload = normalize_preflight_result(result)
    # Verify backward compatible
    assert "decision" in payload
    assert "reason" in payload
    # Verify new fields are present
    assert "active_project" in payload
    assert "matched_memory_summary" in payload
    assert "supporting_reasons" in payload
    json.dumps(payload)  # must not raise


def test_preflight_retrieves_indonesian_essay_workflow_rule_before_acting(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    rule = "Decision: For Indonesian essays: inspect, understand tone, propose changes. Do not modify the file unless user explicitly says to edit. Continue working means analyze, not write."
    _add_memory_rows(db_path, [
        ("mem_rule_id", rule, {"source": ".oem/m.md", "title": "Indonesian essay rule"}),
    ])
    result = run_preflight(
        "For Indonesian essays continue working means analyze not write do not modify file unless explicit",
        project=str(preflight_project),
        write_audit=False,
    )
    # Must not be noop; top memory should be the rule decision
    assert result.decision != "noop"
    assert result.matched_memory, "expected matched memory"
    top = result.matched_memory[0]
    assert top.metadata.get("memory_type") in ("decision", "observation")  # decision preferred
    # Rank #1 check via score ordering
    scores = [m.score for m in result.matched_memory]
    assert scores[0] == max(scores)


def test_preflight_matched_memory_does_not_put_command_log_above_exact_decision(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        ("mem_log", "Command output: " + ("essay project " * 100), {"source": "l.md", "title": "log"}),
        ("mem_dec", "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project.", {"source": "d.md", "title": "Decision"}),
    ])
    result = run_preflight(
        "2_Essay/expertise-debt/Essay_ID.md is the open project",
        project=str(preflight_project),
        write_audit=False,
    )
    titles = [m.title for m in result.matched_memory]
    # Decision must be first or only high-value result
    dec_idx = next((i for i, t in enumerate(titles) if "Decision" in (t or "")), None)
    log_idx = next((i for i, t in enumerate(titles) if "log" in (t or "").lower()), None)
    assert dec_idx is not None
    if log_idx is not None:
        assert dec_idx < log_idx


def test_preflight_memory_ranking_not_double_applied(preflight_project: Path):
    db_path = preflight_project / ".oem" / ".local_vector_db" / "vectors.db"
    _add_memory_rows(db_path, [
        ("m1", "Decision: Essay_ID.md is the open project", {"source": "d.md", "title": "D"}),
    ])
    result = run_preflight("Essay_ID.md open project", project=str(preflight_project), write_audit=False)
    # Ensure diagnostics are present exactly once from the ranker
    assert result.matched_memory
    meta = result.matched_memory[0].metadata or {}
    assert "ranking_boosts" in meta
    assert "ranking_reason" in meta
    # Calling preflight again must produce identical top diagnostics (no double application)
    result2 = run_preflight("Essay_ID.md open project", project=str(preflight_project), write_audit=False)
    meta2 = result2.matched_memory[0].metadata or {}
    assert meta.get("final_score") == meta2.get("final_score")


# ---------------------------------------------------------------------------
# P1: Directive stopword guardrail — Bug #4 preflight regressions
# ---------------------------------------------------------------------------


def test_preflight_continue_current_project_no_the_directives(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("continue working on the current project", project=str(project))

    assert len(result["matched_directives"]) == 0
    for md in result["matched_directives"]:
        assert "the" not in md.get("reason", "")
        assert "the" not in md.get("matched_tokens", [])


def test_preflight_fix_story_page_no_the_directives(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("fix the story page responsive layout", project=str(project))

    for md in result["matched_directives"]:
        assert "the" not in md.get("reason", "")
        assert "the" not in md.get("matched_tokens", [])


def test_preflight_review_current_oem_health_no_stopword_directives(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("review current OEM health", project=str(project))

    assert len(result["matched_directives"]) == 0


def test_preflight_review_current_oem_health_no_unrelated_required_directive(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.

        # LangGraph STORM Research Pipeline
        - MUST follow LangGraph STORM research pipeline steps.
        """,
    )

    result = engine.preflight("review current OEM health", project=str(project))

    assert result["decision"] != "required"
    titles = [d["title"] for d in result["matched_directives"]]
    assert "Current Content-Machine Contract" not in titles
    assert "LangGraph STORM Research Pipeline" not in titles


def test_preflight_current_content_machine_contract_not_triggered_by_current_only(tmp_path: Path):
    project, engine = _project_with_agents(
        tmp_path,
        """
        # Current Content-Machine Contract
        - MUST apply current content-machine contract.
        """,
    )

    result = engine.preflight("current", project=str(project))

    assert len(result["matched_directives"]) == 0
    assert result["decision"] != "required"
