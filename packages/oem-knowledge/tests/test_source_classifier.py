from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.source_classifier import SourceType, classify_source, is_ingestion_eligible


@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng


def test_source_classifier_marks_oem_wiki_as_not_ingestion_eligible():
    classification = classify_source(".oem/wiki/concepts/example.md")

    assert classification.source_type == SourceType.OEM_WIKI
    assert classification.ingestion_eligible is False
    assert is_ingestion_eligible(".oem/wiki/concepts/example.md") is False


def test_source_classifier_marks_runtime_events_as_oem_runtime_log():
    classification = classify_source(".oem/runtime_events.jsonl")

    assert classification.source_type == SourceType.OEM_RUNTIME_LOG
    assert classification.ingestion_eligible is False


def test_source_classifier_marks_project_file_as_ingestion_eligible():
    classification = classify_source("src/package/module.py", "def hello():\n    pass\n")

    assert classification.source_type == SourceType.PROJECT_FILE
    assert classification.ingestion_eligible is True
    assert is_ingestion_eligible("src/package/module.py") is True


def test_source_classifier_marks_clean_reports_as_not_ingestion_eligible():
    classification = classify_source(".oem/reports/clean-20260609-120000.md")

    assert classification.source_type == SourceType.OEM_SESSION_REPORT
    assert classification.ingestion_eligible is False
    assert is_ingestion_eligible(".oem/reports/clean-20260609-120000.md") is False


def test_source_classifier_marks_sessions_as_session_reports():
    classification = classify_source(".oem/sessions/session-2026-09-06.md")
    assert classification.source_type == SourceType.OEM_SESSION_REPORT
    assert classification.ingestion_eligible is False
    assert is_ingestion_eligible(".oem/sessions/session-2026-09-06.md") is False


def test_reflection_excludes_oem_generated_files(engine, tmp_path):
    concepts_dir = engine._concepts_dir(str(tmp_path))
    old_time = time.time() - 3600
    for existing_file in concepts_dir.rglob("*.md"):
        os.utime(existing_file, (old_time, old_time))

    session_started_at = time.time()
    generated_wiki = concepts_dir / "generated_oem.md"
    generated_wiki.write_text("# Generated OEM concept\n", encoding="utf-8")
    os.utime(generated_wiki, (session_started_at + 1, session_started_at + 1))

    generated_source = tmp_path / "src" / "generated.md"
    generated_source.parent.mkdir(parents=True, exist_ok=True)
    generated_source.write_text(
        "---\ngenerated_by: openempiric\n---\n# Generated summary\n",
        encoding="utf-8",
    )
    user_source = tmp_path / "src" / "user_module.py"
    user_source.write_text("def user_authored():\n    return True\n", encoding="utf-8")

    engine.state._save_registry(
        {
            "concept_generated": {
                "canonical_name": "generated-summary",
                "aliases": ["generated.md", "src/generated.md"],
            },
            "concept_user": {
                "canonical_name": "user-module",
                "aliases": ["user_module.py", "src/user_module.py"],
            },
        },
        str(tmp_path),
    )

    class GitResult:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "diff", "--name-only"]:
            return GitResult(
                "\n".join(
                    [
                        ".oem/wiki/generated_oem.md",
                        ".oem/runtime_events.jsonl",
                        "src/generated.md",
                        "src/user_module.py",
                    ]
                )
            )
        return GitResult("")

    with patch("subprocess.run", new=fake_run):
        res = engine.reflection.reflect_session(
            project=str(tmp_path),
            conversation_text="",
            session_id="test_exclude_oem_generated",
            session_started_at=session_started_at,
        )

    assert res["status"] == "success"
    assert res["explainability"]["excluded_oem_generated_files"] == 3
    assert res["explainability"]["file_observations_count"] == 1
    assert [
        e["evidence"] for e in res["canonical_events"] if e.get("source") == "diff"
    ] == ["Code modified in workspace: src/user_module.py"]


def test_materialization_skips_oem_generated_events(engine, tmp_path):
    sessions_dir = engine._sessions_dir(str(tmp_path))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "session_oem_schema.md"
    report_file.write_text(
        "```json\n"
        + json.dumps(
            {
                "knowledge_events": [
                    {
                        "type": "observation",
                        "concept": "schema",
                        "evidence": "Generated wiki content should not become source evidence.",
                        "source_path": ".oem/wiki/schema.md",
                    }
                ]
            }
        )
        + "\n```",
        encoding="utf-8",
    )

    res = engine.materialization.materialize_concepts(str(tmp_path))

    assert res["status"] == "success"
    assert res["materialized"] == []
    assert res["skipped_oem_generated_events"] == 1
    assert res["skipped_oem_generated_event_details"][0]["classifier_source_type"] == "oem_wiki"
    assert engine.state._load_registry(str(tmp_path)) == {}
    assert list(engine._concepts_dir(str(tmp_path)).glob("concept_*.md")) == []
