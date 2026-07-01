import pytest
from oem_knowledge.memory_ranking import (
    classify_memory_type,
    extract_query_targets,
    rank_search_result,
    rank_search_results,
    normalize_for_phrase,
    has_phrase_match,
    has_consecutive_phrase_match,
    BOOST_DECISION,
    BOOST_FAILURE,
    BOOST_EXACT_PATH_MATCH,
    BOOST_EXACT_FILENAME_MATCH,
    BOOST_ACTIVE_WORK_SIGNAL,
    BOOST_TOPIC_MATCH,
    PENALTY_SEARCH_LOG,
    PENALTY_COMMAND_LOG,
)


class TestMemoryTypeClassification:
    def test_decision_heading_classified(self):
        assert classify_memory_type("## Decision\nWe chose SQLite") == "decision"
        assert classify_memory_type("Decision: Use PostgreSQL") == "decision"

    def test_failure_heading_classified_before_command_log(self):
        doc = "Failure: Command X failed. Command `ls` returns error."
        assert classify_memory_type(doc) == "failure"

    def test_failure_do_not_repeat_classified(self):
        doc = "Do-not-repeat: Running rm -rf on user data.\nDo not repeat this."
        assert classify_memory_type(doc) == "failure"

    def test_search_log_classified(self):
        doc = "Search results:\nNo matches.\nknowledge_search(query='test')"
        assert classify_memory_type(doc) == "search_log"

    def test_command_log_classified(self):
        doc = "Command `ls -la`\nShell output: ..."
        assert classify_memory_type(doc) == "command_log"

    def test_observation_default(self):
        doc = "Just some random notes about work."
        assert classify_memory_type(doc) == "observation"

    def test_failure_with_command_text_remains_failure(self):
        doc = "Failure: NotebookLM get_notebook timeout occurs when source_ids are passed explicitly to chat.ask."
        assert classify_memory_type(doc) == "failure"

    def test_command_source_dump_patterns_detected(self):
        assert classify_memory_type("Command output: ls -la\n lots of files") == "command_log"
        assert classify_memory_type("Shell output: some long trace") == "command_log"
        assert classify_memory_type("Search results:\n[1] foo\n[2] bar\nknowledge_search(query=...)") == "search_log"
        assert classify_memory_type("Full source dump\nFile contents:\ncat <<EOF\n...") == "source_dump"


class TestQueryTargetExtraction:
    def test_extract_tokens(self):
        targets = extract_query_targets("Essay_ID.md open project")
        assert "Essay_ID" in targets["tokens"] or "md" in targets["tokens"]

    def test_exact_path_extraction_includes_full_path_filename_and_stem(self):
        q = "2_Essay/expertise-debt/Essay_ID.md is the open project"
        targets = extract_query_targets(q)
        assert "2_Essay/expertise-debt/Essay_ID.md" in targets["full_paths"]
        assert "Essay_ID.md" in targets["filenames"]
        assert "Essay_ID" in targets["stems"]
        assert any("2_Essay/expertise-debt" in d for d in targets.get("dirs", []) + targets.get("stems", []))


class TestPhraseNormalization:
    def test_phrase_matching_normalizes_punctuation_and_stopwords(self):
        a = "do not modify file unless explicit"
        b = "do not modify the file unless user explicitly says to edit"
        assert normalize_for_phrase(a) == normalize_for_phrase(b) or has_phrase_match(b, a)
        # Should not falsely match unrelated
        unrelated = "write a completely different essay about poetry"
        assert not has_phrase_match(unrelated, a)

    def test_near_exact_workflow_rule_match(self):
        rule = "For Indonesian essays: inspect, understand tone, propose changes. Do not modify the file unless user explicitly says to edit. 'Continue working' means analyze."
        query = "For Indonesian essays continue working means analyze not write do not modify file unless explicit"
        # The rule text should trigger phrase matches
        assert has_phrase_match(rule, "do not modify") or has_phrase_match(rule, "continue working") or has_phrase_match(rule, "unless explicit")


class TestRankingBoosts:
    def test_memory_search_decision_ranks_above_search_log(self):
        candidates = [
            {
                "id": "log1",
                "document": "Search results: knowledge_search(query='essay')",
                "score": 0.5,
                "metadata": {},
            },
            {
                "id": "dec1",
                "document": "Decision: Essay_ID.md is the open project",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("Essay_ID open project", candidates)
        assert ranked[0]["memory_type"] == "decision"
        assert ranked[-1]["memory_type"] == "search_log"

    def test_memory_search_failure_ranks_above_command_log(self):
        candidates = [
            {
                "id": "cmdlog",
                "document": "Shell output: oem build exited with code 1.",
                "score": 0.5,
                "metadata": {},
            },
            {
                "id": "failure",
                "document": "Failure: oem build returns exit code 1 when config missing.",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("oem build error", candidates)
        assert ranked[0]["memory_type"] == "failure"
        assert ranked[-1]["memory_type"] == "command_log"

    def test_exact_file_decision_beats_topic_decision(self):
        candidates = [
            {
                "id": "topic_dec",
                "document": "Decision: Indonesian essay workflow should be prioritized.",
                "score": 0.5,
                "metadata": {},
            },
            {
                "id": "file_dec",
                "document": "Decision: Essay_ID.md is the open project.",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("Essay_ID.md open project", candidates)
        assert ranked[0]["id"] == "file_dec"

    def test_active_work_signal_boosted(self):
        candidates = [
            {
                "id": "signal",
                "document": "Essay_ID.md is the open project. Working on this actively.",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("Essay_ID.md open project", candidates)
        assert ranked[0]["ranking_boosts"].get("active_work_signal") == BOOST_ACTIVE_WORK_SIGNAL

    def test_search_log_downranked(self):
        candidates = [
            {
                "id": "log",
                "document": "Search results: knowledge_search(query='something')",
                "score": 6.0,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("something", candidates)
        assert ranked[0]["ranking_penalties"].get("search_log") == PENALTY_SEARCH_LOG

    def test_failure_about_command_not_downranked_as_command_log(self):
        candidates = [
            {
                "id": "cmdlog",
                "document": "Command `ls` output: nothing here.",
                "score": 0.5,
                "metadata": {},
            },
            {
                "id": "failure",
                "document": "Failure: Running rm -rf on production data breaks the system.",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("rm failure", candidates)
        assert ranked[0]["memory_type"] == "failure"
        assert ranked[0]["ranking_penalties"] == {}

    def test_ranking_boosts_do_not_stack_repeated_decision_words(self):
        candidates = [
            {
                "id": "rep",
                "document": "Decision: Decision: Decision: Essay_ID.md is key.",
                "score": 0.5,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("Essay_ID.md", candidates)
        boost_count = ranked[0]["ranking_reason"].count("decision memory")
        assert boost_count == 1

    # --- New v2 regression tests ---

    def test_essay_id_open_project_decision_ranks_top(self):
        candidates = [
            {
                "id": "huge_log",
                "document": "Command output: " + ("searching for essay project " * 50),
                "score": 10.0,
                "metadata": {},
            },
            {
                "id": "exact_dec",
                "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project.\nWe are actively editing this.",
                "score": 1.0,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("2_Essay/expertise-debt/Essay_ID.md is the open project", candidates)
        assert ranked[0]["id"] == "exact_dec"
        assert ranked[0]["memory_type"] == "decision"

    def test_indonesian_essay_continue_working_rule_ranks_top(self):
        rule_doc = (
            "Decision: For Indonesian essays: inspect, understand tone, propose changes. "
            "Do not modify the file unless user explicitly says to edit. "
            "'Continue working' means analyze, not write."
        )
        candidates = [
            {
                "id": "unrelated",
                "document": "NotebookLM code chunk about get_notebook and source_ids and chat.ask lots of implementation details " * 10,
                "score": 5.0,
                "metadata": {},
            },
            {
                "id": "rule",
                "document": rule_doc,
                "score": 1.0,
                "metadata": {},
            },
        ]
        q = "For Indonesian essays continue working means analyze not write do not modify file unless explicit"
        ranked = rank_search_results(q, candidates)
        assert ranked[0]["id"] == "rule"
        assert ranked[0]["memory_type"] == "decision"

    def test_notebooklm_source_ids_workaround_memory_ranks_above_unrelated_code_chunks(self):
        workaround = "Handoff: x-becoming-television GET_NOTEBOOK timeout workaround: pass source_ids explicitly to chat.ask"
        candidates = [
            {"id": "unrelated1", "document": "NotebookLM implementation details source_ids chat.ask get_notebook " * 20, "score": 8.0, "metadata": {}},
            {"id": "unrelated2", "document": "More code about NotebookLM and timeouts and source handling", "score": 7.5, "metadata": {}},
            {"id": "workaround", "document": workaround, "score": 2.0, "metadata": {}},
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        # Workaround should be in top results and above pure unrelated code
        ids = [r["id"] for r in ranked]
        assert "workaround" in ids[:3]
        # Unrelated code chunks must not outrank the workaround memory
        workaround_pos = ids.index("workaround")
        for i, rid in enumerate(ids):
            if rid.startswith("unrelated"):
                assert i > workaround_pos, f"Unrelated {rid} ranked above workaround"

    def test_exact_phrase_match_boosts_decision_to_top(self):
        candidates = [
            {"id": "log", "document": "Search results: " + ("essay project open " * 10), "score": 9.0, "metadata": {}},
            {"id": "dec", "document": "Decision: Essay_ID.md is the open project", "score": 1.0, "metadata": {}},
        ]
        ranked = rank_search_results("Essay_ID.md is the open project", candidates)
        assert ranked[0]["id"] == "dec"


class TestDiagnosticsFields:
    def test_registry_fallback_results_include_ranking_diagnostics(self):
        candidates = [
            {
                "id": "fb1",
                "document": "Decision: Test concept decision.",
                "score": 0.8,
                "metadata": {"title": "test"},
            },
        ]
        ranked = rank_search_results("test", candidates)
        assert "memory_type" in ranked[0]
        assert "base_score" in ranked[0]
        assert "final_score" in ranked[0]
        assert "ranking_reason" in ranked[0]
        assert "ranking_boosts" in ranked[0]
        assert "ranking_penalties" in ranked[0]

    def test_memory_search_preserves_base_score_and_final_score(self):
        candidates = [
            {
                "id": "t1",
                "document": "Decision: Something important.",
                "score": 6.2,
                "metadata": {},
            },
        ]
        ranked = rank_search_results("something", candidates)
        assert ranked[0]["base_score"] == 6.2
        assert ranked[0]["score"] == ranked[0]["final_score"]
        assert ranked[0]["score"] > 6.2

    def test_ranking_diagnostics_show_capped_topic_match(self):
        # Build a document with many topic words but no exact path/phrase
        many_terms = " ".join([f"term{i}" for i in range(20)])
        candidates = [
            {"id": "log", "document": "Command output: " + many_terms, "score": 10.0, "metadata": {}},
        ]
        ranked = rank_search_results("term0 term1 term2 term3 term4 term5 term6 term7", candidates)
        assert ranked[0]["ranking_boosts"].get("topic_match", 0) <= BOOST_TOPIC_MATCH
        assert "capped" in " ".join(ranked[0].get("ranking_reason", [])).lower()


class TestTopicCapAndLargeChunks:
    def test_topic_match_hard_cap_even_with_many_terms(self):
        many = " ".join(["essay", "project", "open", "decision", "indonesian", "workflow", "analyze", "modify", "explicit", "continue"] * 3)
        candidates = [
            {"id": "biglog", "document": "Search results: " + many, "score": 20.0, "metadata": {}},
            {"id": "dec", "document": "Decision: Essay_ID.md is the open project", "score": 1.0, "metadata": {}},
        ]
        ranked = rank_search_results("Essay_ID.md is the open project", candidates)
        # Even if log matches many terms, exact decision should win
        assert ranked[0]["id"] == "dec"
        assert ranked[0]["ranking_boosts"].get("topic_match", 0) <= BOOST_TOPIC_MATCH

    def test_large_low_density_chunk_loses_to_exact_decision(self):
        huge = "random text " * 300  # >1500 chars, no exact match
        candidates = [
            {"id": "huge", "document": huge, "score": 15.0, "metadata": {}},
            {"id": "dec", "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project", "score": 1.0, "metadata": {}},
        ]
        ranked = rank_search_results("2_Essay/expertise-debt/Essay_ID.md is the open project", candidates)
        assert ranked[0]["id"] == "dec"

    def test_large_chunk_penalty_skipped_for_exact_decision(self):
        huge_dec = ("Decision: Essay_ID.md is the open project. " + "context " * 400)
        candidates = [
            {"id": "huge_dec", "document": huge_dec, "score": 1.0, "metadata": {}},
        ]
        ranked = rank_search_results("Essay_ID.md is the open project", candidates)
        assert "large" not in " ".join(ranked[0].get("ranking_reason", [])).lower()


class TestPreflightRankingNotDoubleApplied:
    def test_preflight_memory_ranking_not_double_applied(self):
        # Simulate what preflight now does: convert MemoryMetadata-like items and call rank_search_results once
        mem_like = [
            {
                "id": "m1",
                "document": "Decision: Essay_ID.md is the open project",
                "metadata": {"title": "Decision"},
                "score": 0.0,
            }
        ]
        ranked = rank_search_results("Essay_ID.md open project", mem_like)
        assert len(ranked) == 1
        assert "ranking_boosts" in ranked[0]
        # Calling rank again should not multiply boosts in a way that changes structure
        ranked2 = rank_search_results("Essay_ID.md open project", ranked)
        assert ranked2[0]["memory_type"] == "decision"
