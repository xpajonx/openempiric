from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from oem_knowledge.instructions.ledger import get_active_directives, get_db_connection, index_source_file
from oem_knowledge.instructions.matcher import match_directives


def _directive(
    *,
    title: str,
    rule: str,
    priority: str = "critical",
    scope: str = "general",
    triggers: list[str] | None = None,
    always_on: bool = False,
) -> dict:
    return {
        "id": f"directive_{title.lower().replace(' ', '_')}",
        "title": title,
        "rule": rule,
        "source_path": "AGENTS.md",
        "line_start": 1,
        "line_end": 1,
        "score": 0,
        "priority": priority,
        "scope": scope,
        "status": "active",
        "triggers_json": json.dumps(triggers or []),
        "forbidden_actions_json": "[]",
        "related_concepts_json": "[]",
        "related_skills_json": "[]",
        "always_on": always_on,
    }


def test_directive_stopword_the_does_not_match():
    matches = match_directives(
        "the current project",
        [_directive(title="Current Content-Machine Contract", rule="MUST follow content-machine rules", triggers=["current"])],
    )
    assert len(matches) == 0


def test_directive_single_article_overlap_ignored():
    matches = match_directives(
        "the project",
        [_directive(title="Some Project Directive", rule="MUST follow project rules", triggers=["project"])],
    )
    assert len(matches) == 0


def test_directive_stopwords_do_not_contribute_to_score():
    matches = match_directives(
        "the langgraph storm",
        [_directive(title="LangGraph STORM Research Pipeline", rule="MUST follow LangGraph STORM pipeline steps")],
    )
    assert len(matches) == 1
    assert "the" not in matches[0].get("matched_tokens", [])
    assert "the" not in matches[0].get("reason", "")


def test_directive_single_generic_current_cannot_force_required():
    matches = match_directives(
        "current",
        [_directive(title="Current Content-Machine Contract", rule="MUST follow content-machine rules")],
    )
    assert len(matches) == 0


def test_directive_single_generic_fix_cannot_force_required():
    matches = match_directives(
        "fix",
        [_directive(title="Story Page Fix", rule="MUST fix the page layout")],
    )
    assert len(matches) == 0


def test_directive_single_generic_oem_cannot_force_required():
    matches = match_directives(
        "oem",
        [_directive(title="OEM Health Check", rule="MUST check OEM health")],
    )
    assert len(matches) == 0


def test_directive_requires_two_semantic_tokens_for_positive_match():
    matches = match_directives(
        "fix the page",
        [_directive(title="Page Layout Guide", rule="MUST follow page layout rules")],
    )
    assert len(matches) == 0


def test_critical_directive_still_requires_semantic_relevance():
    matches = match_directives(
        "review current OEM health",
        [_directive(title="Current Content-Machine Contract", rule="MUST apply content-machine checks", triggers=["current"])],
    )
    assert len(matches) == 0


def test_always_on_directive_can_force_required():
    match = match_directives(
        "unrelated task",
        [_directive(title="Global Safety Contract", rule="MUST always preserve safety", always_on=True)],
    )[0]

    assert match["match_class"] == "global_always_on_directive"
    assert match["always_on"] is True
    assert match["can_force_required"] is True


def test_semantic_langgraph_storm_directive_still_matches():
    match = match_directives(
        "continue LangGraph STORM research pipeline implementation",
        [_directive(title="LangGraph STORM Research Pipeline", rule="MUST follow LangGraph STORM pipeline steps")],
    )[0]

    assert match["match_class"] == "semantic_directive_match"
    assert "langgraph" in match["semantic_tokens"]
    assert match["can_force_required"] is True


def test_generic_only_matches_omitted_from_normal_results():
    matches = match_directives(
        "review current OEM health",
        [_directive(title="Current Content-Machine Contract", rule="MUST apply content-machine checks", triggers=["current"])],
    )

    assert len(matches) == 0


def test_matched_tokens_never_include_hard_stopwords():
    match = match_directives(
        "the and langgraph storm",
        [_directive(title="LangGraph STORM Research Pipeline", rule="MUST follow LangGraph STORM pipeline steps")],
    )[0]

    for token in ("the", "and"):
        assert token not in match.get("matched_tokens", [])
    assert "langgraph" in match["matched_tokens"]
    assert "storm" in match["matched_tokens"]


def test_fix_with_semantic_tokens_can_match_relevant_directive():
    match = match_directives(
        "fix NotebookLM source_ids timeout",
        [_directive(title="NotebookLM Adapter", rule="MUST handle source_ids timeout correctly")],
    )[0]

    assert match["match_class"] == "semantic_directive_match"
    assert match["can_force_required"] is True


def test_phrase_overlap_ignores_stopword_only_phrases():
    matches = match_directives(
        "the current project",
        [_directive(title="Current Project Contract", rule="MUST inspect current project context")],
    )
    assert len(matches) == 0


def test_oem_health_phrase_can_match_oem_health_directive():
    match = match_directives(
        "review OEM health output",
        [_directive(title="OEM Health Check Workflow", rule="MUST inspect OEM health output carefully")],
    )[0]

    assert match["match_class"] == "semantic_directive_match"
    assert "oem" in match["matched_tokens"]
    assert "health" in match["matched_tokens"]
    assert "output" in match["matched_tokens"]


def test_scope_global_implies_always_on():
    match = match_directives(
        "unrelated task",
        [_directive(title="Global Safety Contract", rule="MUST always preserve safety", scope="global")],
    )[0]

    assert match["match_class"] == "global_always_on_directive"
    assert match["always_on"] is True
    assert match["can_force_required"] is True


def test_instruction_ledger_adds_always_on_column_idempotently(tmp_path: Path):
    db_path = tmp_path / "instruction_ledger.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE directives (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            title TEXT,
            scope TEXT,
            triggers_json TEXT,
            priority TEXT,
            rule TEXT NOT NULL,
            forbidden_actions_json TEXT,
            related_concepts_json TEXT,
            related_skills_json TEXT,
            related_workflows_json TEXT,
            status TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    conn = get_db_connection(db_path)
    get_db_connection(db_path).close()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(directives)").fetchall()}
    conn.close()

    assert "always_on" in columns


def test_existing_directives_default_always_on_false(tmp_path: Path):
    conn = get_db_connection(tmp_path / "instruction_ledger.sqlite")
    index_source_file(conn, "AGENTS.md", "# Instructions\n- MUST run tests.", "hash1", 0, 0)
    directive = get_active_directives(conn)[0]
    conn.close()

    assert directive["always_on"] == 0
