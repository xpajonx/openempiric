import pytest
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng


def test_fallback_extracts_from_natural_language(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Fixed oem doctor global install detection.\nAdded executable availability checks.",
        session_id="test_nl_1",
    )
    concepts = [
        e["concept_candidates"][0]
        for e in res["canonical_events"]
        if e.get("source") == "chat-fallback"
    ]
    assert any("doctor" in c or "install" in c for c in concepts), (
        f"Expected doctor/install concept in fallback results, got {concepts}"
    )
    assert any("executable" in c or "availability" in c for c in concepts), (
        f"Expected executable/availability concept in fallback results, got {concepts}"
    )
    assert "explainability" in res
    assert res["explainability"]["fallback_extraction_used"] is True


def test_structured_prefixes_preserved(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="decision: Use runtime-native TypeScript retrieval.",
        session_id="test_prefix_1",
    )
    chat_events = [
        e for e in res["canonical_events"] if e.get("source") == "chat"
    ]
    assert len(chat_events) >= 1
    assert any(
        "typescript retrieval" in e["concept_candidates"][0].lower()
        for e in chat_events
    ), f"Expected TypeScript retrieval concept, got {[e['concept_candidates'][0] for e in chat_events]}"
    assert res["explainability"]["structured_events_found"] >= 1
    assert res["explainability"]["fallback_extraction_used"] is False


def test_empty_chat_no_crash(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Done.",
        session_id="test_empty_1",
    )
    assert res["status"] == "empty"
    assert res["report_path"] is None
    assert "explainability" in res


def test_all_sd_heuristics(engine, tmp_path):
    chat = "\n".join([
        "Fixed doctor null pointer in validation.",
        "Added fallback extraction to reflection pipeline.",
        "Removed deprecated metrics endpoint.",
        "Implemented new search cache layer.",
        "Refactored CLI argument parser.",
        "Migrated config from YAML to TOML.",
        "Decided to drop Python 3.11 support.",
        "Validated embedding model warmup works.",
        "Failed to reproduce the edge case.",
    ])
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text=chat,
        session_id="test_all_1",
    )
    fallback_events = [
        e for e in res["canonical_events"]
        if e.get("source") == "chat-fallback"
    ]
    assert len(fallback_events) >= 3, (
        f"Expected at least 3 fallback events, got {len(fallback_events)}"
    )
    concept_texts = " ".join(
        e["concept_candidates"][0] for e in fallback_events
    )
    for keyword in ["doctor", "fallback", "deprecated", "search", "cli", "yaml", "python", "embedding", "edge"]:
        assert keyword in concept_texts, (
            f"Expected {keyword} in fallback concepts: {concept_texts}"
        )


def test_report_prioritization(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Fixed doctor module import error.\nRefactored CLI entry point.",
        session_id="test_priority_1",
    )
    sources = [e.get("source", "") for e in res["canonical_events"]]
    chat_positions = [i for i, s in enumerate(sources) if s in ("chat", "chat-fallback")]
    diff_positions = [i for i, s in enumerate(sources) if s == "diff"]
    if chat_positions and diff_positions:
        assert max(chat_positions) < min(diff_positions), (
            f"Chat-derived events (positions {chat_positions}) should appear before "
            f"file observations (positions {diff_positions})"
        )


def test_mixed_prefix_and_fallback(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text=(
            "decision: Adopt uv as the package manager.\n"
            "Fixed CI pipeline permissions.\n"
        ),
        session_id="test_mixed_1",
    )
    structured = [
        e for e in res["canonical_events"] if e.get("source") == "chat"
    ]
    fallback = [
        e for e in res["canonical_events"] if e.get("source") == "chat-fallback"
    ]
    assert len(structured) >= 1
    assert len(fallback) == 0, (
        "Fallback should not run when structured events are found, "
        f"got {len(fallback)} fallback events"
    )
    assert res["explainability"]["structured_events_found"] >= 1
    assert res["explainability"]["fallback_extraction_used"] is False


def test_no_double_dedup(engine, tmp_path):
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text="Fixed doctor detection.\nFixed doctor detection.\nFixed doctor detection.",
        session_id="test_dedup_1",
    )
    fallback_events = [
        e for e in res["canonical_events"]
        if e.get("source") == "chat-fallback"
    ]
    assert len(fallback_events) == 1, (
        f"Expected deduplication to produce 1 event, got {len(fallback_events)}"
    )


def test_list_tolerant_fallback_extraction(engine, tmp_path):
    chat = (
        "- Fixed doctor null pointer.\n"
        "* Added reflection guidelines.\n"
        "1. Implemented list support.\n"
        "- [x] Refactored fallback parser.\n"
    )
    res = engine.reflection.reflect_session(
        project=str(tmp_path),
        conversation_text=chat,
        session_id="test_list_tolerant_1",
    )
    concepts = [
        e["concept_candidates"][0]
        for e in res["canonical_events"]
        if e.get("source") == "chat-fallback"
    ]
    assert "doctor-null-pointer" in concepts
    assert "reflection-guidelines" in concepts
    assert "list-support" in concepts
    assert "fallback-parser" in concepts


def test_reflection_excludes_oem_generated_files(engine, tmp_path):
    import os
    import time

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
                        ".oem/state/metrics.json",
                        "src/generated.md",
                        "src/user_module.py",
                    ]
                )
            )
        return GitResult("")

    from unittest.mock import patch

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

    diff_evidence = [
        e["evidence"] for e in res["canonical_events"] if e.get("source") == "diff"
    ]
    assert diff_evidence == ["Code modified in workspace: src/user_module.py"]
