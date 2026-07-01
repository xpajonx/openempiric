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
    BOOST_TECHNICAL_HANDOFF,
    BOOST_WORKAROUND,
    BOOST_DEBUG_NOTE,
    BOOST_IDENTIFIER_COOCCURRENCE_ONE,
    BOOST_IDENTIFIER_COOCCURRENCE_TWO,
    BOOST_IDENTIFIER_COOCCURRENCE_THREE,
    PENALTY_GENERIC_ACTIVE_PROJECT_FOR_TECHNICAL,
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


# ── v3: Technical query intent detection ──────────────────────────────


class TestTechnicalQueryIntentDetection:
    def test_technical_query_detects_identifiers_and_timeout(self):
        targets = extract_query_targets(
            "x-becoming-television GET_NOTEBOOK timeout "
            "pass source_ids explicitly chat.ask workaround"
        )
        assert targets.get("technical_intent") is True, (
            "Should detect technical intent from timeout + identifiers"
        )
        assert targets.get("debug_intent") is False
        tech_ids = targets.get("technical_identifiers", [])
        assert any("GET_NOTEBOOK" in t for t in tech_ids), (
            f"Should extract GET_NOTEBOOK as technical identifier, got {tech_ids}"
        )

    def test_non_technical_query_has_no_technical_intent(self):
        targets = extract_query_targets(
            "2_Essay/expertise-debt/Essay_ID.md is the open project"
        )
        assert targets.get("technical_intent") is False

    def test_debug_intent_detected(self):
        targets = extract_query_targets(
            "fix debug regression in source_ids code"
        )
        assert targets.get("debug_intent") is True

    def test_workaround_term_detected(self):
        targets = extract_query_targets(
            "workaround for GET_NOTEBOOK timeout"
        )
        assert targets.get("technical_intent") is True

    def test_camel_case_identifier_extracted(self):
        targets = extract_query_targets(
            "NotebookLM source_ids timeout workaround"
        )
        tech_ids = targets.get("technical_identifiers", [])
        assert any("source_ids" in t for t in tech_ids), (
            f"Should extract snake_case source_ids, got {tech_ids}"
        )

    def test_project_terms_extracted(self):
        targets = extract_query_targets(
            "x-becoming-television project workaround"
        )
        project_terms = targets.get("project_terms", [])
        assert any("x-becoming-television" in p for p in project_terms), (
            f"Should detect project terms, got {project_terms}"
        )


# ── v3: Session-handoff / workaround memory type classification ───────


class TestSessionHandoffMemoryClassification:
    def test_handoff_heading_classified_as_technical_handoff(self):
        assert classify_memory_type(
            "Handoff: x-becoming-television GET_NOTEBOOK timeout workaround"
        ) == "technical_handoff", "Handoff: prefix should be classified as technical_handoff"

    def test_technical_heading_classified(self):
        assert classify_memory_type(
            "Technical Note: source_ids must be passed explicitly to chat.ask"
        ) == "technical_handoff"

    def test_workaround_content_classified(self):
        assert classify_memory_type(
            "Some memory about a workaround for timeout issues"
        ) == "workaround"

    def test_debug_note_heading_classified(self):
        assert classify_memory_type(
            "Bug: GET_NOTEBOOK returns empty result"
        ) == "failure"  # Bug: maps to failure via FAILURE_PATTERNS

    def test_debug_note_heading_different(self):
        assert classify_memory_type(
            "Debug Note: source_ids handling fails when empty"
        ) == "debug_note"

    def test_session_handoff_with_technical_terms_classified(self):
        doc = (
            "Document: .oem/session-handoff.md\n"
            "Section: Technical Issues\n\n"
            "GET_NOTEBOOK times out when source_ids is not passed"
        )
        assert classify_memory_type(doc) == "technical_handoff", (
            "session-handoff.md with technical terms should be technical_handoff"
        )

    def test_session_handoff_without_technical_terms_stays_observation(self):
        doc = (
            "Document: .oem/session-handoff.md\n"
            "Section: General Notes\n\n"
            "Just some general working notes about the project."
        )
        assert classify_memory_type(doc) == "observation", (
            "session-handoff.md without technical terms should stay observation"
        )


# ── v3: Identifier co-occurrence boosts ───────────────────────────────


class TestIdentifierCooccurrenceBoosts:
    def test_identifier_cooccurrence_boost_applied_for_technical_query(self):
        candidates = [
            {
                "id": "handoff",
                "document": (
                    "Handoff: x-becoming-television GET_NOTEBOOK timeout: "
                    "pass source_ids explicitly to chat.ask"
                ),
                "score": 1.0,
                "metadata": {"source": "nb.md", "title": "Handoff"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        boosts = ranked[0]["ranking_boosts"]
        assert "identifier_cooccurrence" in boosts, (
            f"Expected identifier_cooccurrence boost, got {list(boosts.keys())}"
        )

    def test_single_identifier_gets_one_boost(self):
        candidates = [
            {
                "id": "m1",
                "document": "Something about GET_NOTEBOOK in the system.",
                "score": 1.0,
                "metadata": {},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout workaround"
        ranked = rank_search_results(q, candidates)
        boosts = ranked[0]["ranking_boosts"]
        # Only GET_NOTEBOOK matches, single ident
        coeff = boosts.get("identifier_cooccurrence", 0)
        assert coeff == BOOST_IDENTIFIER_COOCCURRENCE_ONE, (
            f"Expected single ident boost {BOOST_IDENTIFIER_COOCCURRENCE_ONE}, got {coeff}"
        )

    def test_multiple_identifiers_cooccurrence_gets_higher_boost(self):
        candidates = [
            {
                "id": "handoff",
                "document": (
                    "Handoff: GET_NOTEBOOK timeout workaround "
                    "pass source_ids explicitly to chat.ask"
                ),
                "score": 1.0,
                "metadata": {"source": "nb.md", "title": "Handoff"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        boosts = ranked[0]["ranking_boosts"]
        coeff = boosts.get("identifier_cooccurrence", 0)
        assert coeff == BOOST_IDENTIFIER_COOCCURRENCE_THREE, (
            f"Expected three+ ident boost {BOOST_IDENTIFIER_COOCCURRENCE_THREE}, got {coeff}"
        )


# ── v3: Technical handoff boost ───────────────────────────────────────


class TestTechnicalHandoffBoost:
    def test_notebooklm_source_ids_workaround_ranks_above_generic_concept_chunks(self):
        workaround = (
            "Handoff: x-becoming-television GET_NOTEBOOK timeout workaround: "
            "pass source_ids explicitly to chat.ask"
        )
        candidates = [
            {
                "id": "generic_obs",
                "document": "Observation: x-becoming-television is the current project. We are actively working on it.",
                "score": 5.0,
                "metadata": {"source": "c.md", "title": "current project"},
            },
            {
                "id": "unrelated_decision",
                "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project.",
                "score": 4.0,
                "metadata": {"source": "d.md", "title": "Essay open project"},
            },
            {
                "id": "workaround",
                "document": workaround,
                "score": 1.0,
                "metadata": {"source": "nb.md", "title": "workaround"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        ids = [r["id"] for r in ranked]
        workaround_pos = ids.index("workaround")
        generic_pos = ids.index("generic_obs")
        unrelated_pos = ids.index("unrelated_decision")
        assert workaround_pos < generic_pos, (
            f"Workaround (pos {workaround_pos}) should outrank generic obs (pos {generic_pos})"
        )
        assert workaround_pos < unrelated_pos, (
            f"Workaround (pos {workaround_pos}) should outrank unrelated decision (pos {unrelated_pos})"
        )

    def test_technical_handoff_boost_applied(self):
        candidates = [
            {
                "id": "handoff",
                "document": (
                    "Handoff: x-becoming-television GET_NOTEBOOK timeout workaround: "
                    "pass source_ids explicitly to chat.ask"
                ),
                "score": 1.0,
                "metadata": {"source": "nb.md", "title": "Handoff"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        boosts = ranked[0]["ranking_boosts"]
        assert "technical_handoff" in boosts, (
            f"Expected technical_handoff boost, got {list(boosts.keys())}"
        )
        assert boosts["technical_handoff"] == BOOST_TECHNICAL_HANDOFF


# ── v3: Generic active-project decision downrank ──────────────────────


class TestGenericActiveProjectDownrank:
    def test_generic_active_project_decision_downranked_for_technical_query(self):
        """Active-project decision with no technical ID match gets penalty."""
        candidates = [
            {
                "id": "generic_dec",
                "document": "Decision: Essay_ID.md is the open project. We are working on this actively.",
                "score": 5.0,
                "metadata": {"source": "d.md", "title": "Decision"},
            },
            {
                "id": "tech_handoff",
                "document": (
                    "Handoff: GET_NOTEBOOK timeout workaround: "
                    "pass source_ids explicitly to chat.ask"
                ),
                "score": 1.0,
                "metadata": {"source": "nb.md", "title": "Handoff"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        generic = [r for r in ranked if r["id"] == "generic_dec"][0]
        penalties = generic.get("ranking_penalties", {})
        assert "generic_active_project_for_technical" in penalties, (
            f"Expected penalty for generic active-project decision, got {list(penalties.keys())}"
        )
        assert penalties["generic_active_project_for_technical"] == PENALTY_GENERIC_ACTIVE_PROJECT_FOR_TECHNICAL

    def test_active_project_decision_not_downranked_for_open_project_query(self):
        """Active-project decision should NOT be downranked for non-technical query."""
        candidates = [
            {
                "id": "dec",
                "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project. Active work.",
                "score": 1.0,
                "metadata": {"source": "d.md", "title": "Decision"},
            },
        ]
        q = "2_Essay/expertise-debt/Essay_ID.md is the open project"
        ranked = rank_search_results(q, candidates)
        penalties = ranked[0].get("ranking_penalties", {})
        assert "generic_active_project_for_technical" not in penalties, (
            "Non-technical query should not trigger active-project penalty"
        )

    def test_active_project_decision_not_downranked_when_contains_technical_id(self):
        """Active-project decision containing technical identifiers should NOT be penalized."""
        candidates = [
            {
                "id": "tech_dec",
                "document": (
                    "Decision: GET_NOTEBOOK timeout fix. "
                    "We are working on this actively."
                ),
                "score": 3.0,
                "metadata": {"source": "d.md", "title": "Decision"},
            },
        ]
        q = "x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround"
        ranked = rank_search_results(q, candidates)
        penalties = ranked[0].get("ranking_penalties", {})
        assert "generic_active_project_for_technical" not in penalties, (
            "Active-project decision with technical ID should not be penalized"
        )


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


class TestMemoryStopwordFloodingAndGenericGuardrails:
    def test_memory_ranking_stopword_the_not_identifier(self):
        targets = extract_query_targets("the")
        assert "the" not in targets["identifiers"]
        assert "the" not in targets["stems"]

    def test_memory_ranking_stopword_for_not_identifier(self):
        targets = extract_query_targets("for")
        assert "for" not in targets["identifiers"]
        assert "for" not in targets["stems"]

    def test_memory_ranking_stopwords_do_not_contribute_topic_match(self):
        candidates = [
            {"id": "doc1", "document": "the for showing context", "score": 1.0, "metadata": {}}
        ]
        ranked = rank_search_results("the for", candidates)
        assert "topic_match" not in ranked[0]["ranking_boosts"]

    def test_memory_ranking_generic_only_query_does_not_create_strong_match(self):
        candidates = [
            {"id": "doc1", "document": "Decision: current project state review health", "score": 1.0, "metadata": {}}
        ]
        ranked = rank_search_results("continue working on current project state", candidates)
        assert ranked[0]["score"] <= 5.0

    def test_fix_story_page_layout_does_not_match_indonesian_essay_workflow(self):
        indonesian_essay_workflow = (
            "Decision: For Indonesian essays: inspect, understand tone, propose changes. "
            "Do not modify the file unless user explicitly says to edit. Continue working means analyze, not write."
        )
        candidates = [
            {"id": "indo", "document": indonesian_essay_workflow, "score": 1.0, "metadata": {}}
        ]
        ranked = rank_search_results("fix the story page responsive layout", candidates)
        reasons = ranked[0]["ranking_reason"]
        assert not any("identifier/stem match: the" in r for r in reasons)
        assert not any("identifier/stem match: for" in r for r in reasons)
        assert not any("topic match capped: 1 terms -> +0.5" in r for r in reasons)

    def test_technical_identifiers_still_match_after_stopword_filtering(self):
        targets = extract_query_targets("GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround")
        assert "GET_NOTEBOOK" in targets["identifiers"]
        assert "source_ids" in targets["identifiers"]
        assert "chat.ask" in targets["identifiers"]

    def test_exact_path_matching_still_works_after_stopword_filtering(self):
        targets = extract_query_targets("2_Essay/expertise-debt/Essay_ID.md is the open project")
        assert "2_Essay/expertise-debt/Essay_ID.md" in targets["full_paths"]
        assert "Essay_ID.md" in targets["filenames"]
        assert "Essay_ID" in targets["stems"]

    def test_rule_phrase_extraction_still_works_without_path_or_identifier(self):
        targets = extract_query_targets("do not modify file unless explicit")
        assert len(targets["phrases"]) > 0
        assert "do not modify file" in targets["phrases"]

    def test_responsive_layout_preserved_as_semantic_phrase(self):
        targets = extract_query_targets("fix the story page responsive layout")
        assert "responsive" in targets["semantic_terms"]
        assert "layout" in targets["semantic_terms"]
        assert "story" in targets["generic_terms"]
        assert "page" in targets["generic_terms"]
        assert "fix" in targets["generic_terms"]
        assert "the" in targets["stopwords"]

    def test_story_page_layout_alone_does_not_match_unrelated_memory(self):
        unrelated_memory = "This is a random document about design layout and page settings for book layout"
        candidates = [
            {"id": "doc", "document": unrelated_memory, "score": 1.0, "metadata": {}}
        ]
        ranked = rank_search_results("story page layout", candidates)
        assert "identifier_match" not in ranked[0]["ranking_boosts"]
        assert "topic_match" not in ranked[0]["ranking_boosts"]

    def test_t1_t2_t3_assert_rank_one_after_stopword_filtering(self):
        t1_cand = [
            {"id": "other", "document": "Some other document", "score": 1.0, "metadata": {}},
            {"id": "t1", "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project", "score": 1.0, "metadata": {}},
        ]
        ranked_t1 = rank_search_results("2_Essay/expertise-debt/Essay_ID.md is the open project", t1_cand)
        assert ranked_t1[0]["id"] == "t1"

        t2_cand = [
            {"id": "other", "document": "Some other document", "score": 1.0, "metadata": {}},
            {"id": "t2", "document": "Decision: For Indonesian essays: inspect, understand tone, propose changes.", "score": 1.0, "metadata": {}},
        ]
        ranked_t2 = rank_search_results("For Indonesian essays continue working means analyze not write do not modify file unless explicit", t2_cand)
        assert ranked_t2[0]["id"] == "t2"

        t3_cand = [
            {"id": "other", "document": "Some other document", "score": 1.0, "metadata": {}},
            {"id": "t3", "document": "Handoff: x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround", "score": 1.0, "metadata": {}},
        ]
        ranked_t3 = rank_search_results("x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround", t3_cand)
        assert ranked_t3[0]["id"] == "t3"


class TestRelevanceFloorRefinements:
    def test_active_work_pattern_in_document_does_not_unlock_type_boost_for_unrelated_query(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: some unrelated error occurred. is the open project.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("story", candidates)
        assert ranked[0]["eligible_for_type_boost"] is False
        assert ranked[0]["final_score"] == 0.0

    def test_single_weak_identifier_does_not_unlock_type_boost(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: the story layout had an issue.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("story", candidates)
        assert ranked[0]["eligible_for_type_boost"] is False

    def test_single_exact_technical_identifier_can_unlock_type_boost(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: source_ids had an issue.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("source_ids", candidates)
        assert ranked[0]["eligible_for_type_boost"] is True
        assert ranked[0]["final_score"] > 5.0

    def test_two_generic_tokens_do_not_meet_relevance_floor(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: fix the layout on the story page.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("fix story", candidates)
        assert ranked[0]["eligible_for_type_boost"] is False

    def test_two_semantic_tokens_meet_relevance_floor(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: GSAP and ScrollTrigger had responsive bugs.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("GSAP ScrollTrigger", candidates)
        assert ranked[0]["eligible_for_type_boost"] is True

    def test_suppressed_failure_boost_not_included_in_final_score(self):
        candidates = [
            {
                "id": "mem1",
                "document": "Failure: layout spacing is incorrect.",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("layout", candidates)
        assert ranked[0]["eligible_for_type_boost"] is False
        assert "failure boost suppressed" in " ".join(ranked[0]["ranking_reason"])
        assert ranked[0]["final_score"] == 0.0

    def test_t1_t2_t3_strict_rank_one_after_relevance_floor(self):
        t1_cand = [
            {"id": "other", "document": "Failure: some other document", "score": 1.0, "metadata": {}},
            {"id": "t1", "document": "Decision: 2_Essay/expertise-debt/Essay_ID.md is the open project", "score": 1.0, "metadata": {}},
        ]
        ranked_t1 = rank_search_results("2_Essay/expertise-debt/Essay_ID.md is the open project", t1_cand)
        assert ranked_t1[0]["id"] == "t1"

        t2_cand = [
            {"id": "other", "document": "Failure: some other document", "score": 1.0, "metadata": {}},
            {"id": "t2", "document": "Decision: For Indonesian essays: inspect, understand tone, propose changes.", "score": 1.0, "metadata": {}},
        ]
        ranked_t2 = rank_search_results("For Indonesian essays continue working means analyze not write do not modify file unless explicit", t2_cand)
        assert ranked_t2[0]["id"] == "t2"

        t3_cand = [
            {"id": "other", "document": "Failure: some other document", "score": 1.0, "metadata": {}},
            {"id": "t3", "document": "Handoff: x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround", "score": 1.0, "metadata": {}},
        ]
        ranked_t3 = rank_search_results("x-becoming-television GET_NOTEBOOK timeout pass source_ids explicitly chat.ask workaround", t3_cand)
        assert ranked_t3[0]["id"] == "t3"


def test_tech_id_boundary_matching():
    from oem_knowledge.memory_ranking import has_tech_id_boundary_match
    assert has_tech_id_boundary_match("this is api", "api") is True
    assert has_tech_id_boundary_match("capitalization", "api") is False
    assert has_tech_id_boundary_match("cli is nice", "cli") is True
    assert has_tech_id_boundary_match("client", "cli") is False
    assert has_tech_id_boundary_match("my_func() call", "my_func") is True
    assert has_tech_id_boundary_match("my_func_2", "my_func") is False
    assert has_tech_id_boundary_match("chat.ask()", "chat.ask") is True


class TestIdentifierMatchSubstringRegression:
    def test_identifier_match_ignores_substring_false_positives(self):
        from oem_knowledge.memory_ranking import rank_search_results
        candidates = [
            {
                "id": "mem1",
                "document": "capitalization is important",
                "score": 0.0,
                "metadata": {},
            }
        ]
        ranked = rank_search_results("api", candidates)
        assert "identifier_match" not in ranked[0]["ranking_boosts"]
        assert ranked[0]["final_score"] == 0.0

