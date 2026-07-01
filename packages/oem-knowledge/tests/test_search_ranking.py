import pytest
from oem_knowledge.memory_ranking import (
    classify_memory_type,
    extract_query_targets,
    rank_search_result,
    rank_search_results,
    BOOST_DECISION,
    BOOST_FAILURE,
    BOOST_EXACT_PATH_MATCH,
    BOOST_EXACT_FILENAME_MATCH,
    BOOST_ACTIVE_WORK_SIGNAL,
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


class TestQueryTargetExtraction:
    def test_extract_tokens(self):
        targets = extract_query_targets("Essay_ID.md open project")
        assert "Essay_ID" in targets["tokens"] or "md" in targets["tokens"]


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


class TestPreflightRankingNotDoubleBoosted:
    def test_preflight_memory_ranking_not_double_boosted(self):
        from oem_knowledge.preflight.scoring import score_memory, MemoryMetadata

        mem = MemoryMetadata(
            id="test1",
            title="Decision",
            source_path=None,
            snippet="Decision: Essay_ID.md is open project.",
        )

        breakdown = score_memory("Essay_ID.md open project", mem)
        assert breakdown.score > 0

        memo = {
            "id": "test1",
            "document": "Decision: Essay_ID.md is open project.",
            "score": breakdown.score,
            "metadata": {"title": "Decision"},
        }
        ranked = rank_search_result("Essay_ID.md open project", memo)

        assert ranked["ranking_boosts"].get("decision") == BOOST_DECISION