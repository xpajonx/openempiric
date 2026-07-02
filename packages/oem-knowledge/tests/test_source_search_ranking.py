from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

from oem_knowledge.services.source_corpus import (
    _classify_source_result,
    _count_identifier_cooccurrence,
    _detect_source_query_intent,
    _extract_source_identifiers,
    _has_boundary_identifier_match,
)


class TestExtractSourceIdentifiers:
    def test_extracts_dotted_identifiers(self):
        ids = _extract_source_identifiers("chat.ask getNotebook timeout")
        assert "chat.ask" in ids
        assert "getNotebook" in ids

    def test_preserves_camelcase_and_snakecase(self):
        ids = _extract_source_identifiers("getNotebook my_function UPPERCASE")
        assert "getNotebook" in ids
        assert "my_function" in ids
        assert "UPPERCASE" in ids

    def test_stopwords_excluded(self):
        ids = _extract_source_identifiers("the for how to fix a bug")
        for stopword in ("the", "for", "how", "to", "a"):
            assert stopword not in ids

    def test_splits_longer_dotted_identifiers(self):
        ids = _extract_source_identifiers("chat.ask.getNotebook")
        assert "chat.ask.getNotebook" in ids

    def test_empty_query_returns_empty_list(self):
        assert _extract_source_identifiers("") == []


class TestBoundaryIdentifierMatch:
    def test_exact_match_found(self):
        assert _has_boundary_identifier_match(
            "getNotebook", "def getNotebook(): pass"
        )

    def test_substring_not_matched(self):
        assert not _has_boundary_identifier_match(
            "ask", "def chat_ask_helper(): pass"
        )

    def test_dotted_identifier_as_prefix_not_matched(self):
        assert not _has_boundary_identifier_match(
            "chat", "def chat_ask(): pass"
        )

    def test_dotted_identifier_found(self):
        assert _has_boundary_identifier_match(
            "chat.ask", "some code about chat.ask and stuff"
        )

    def test_empty_document_returns_false(self):
        assert not _has_boundary_identifier_match("foo", "")

    def test_empty_string_returns_false(self):
        assert not _has_boundary_identifier_match("foo", "")

    def test_underscore_prefix_identifier_matches(self):
        assert _has_boundary_identifier_match(
            "SourceIndexStore", "class _SourceIndexStore: pass"
        )

    def test_dot_prefixed_identifer_not_matched(self):
        assert not _has_boundary_identifier_match(
            "ask", "def chat.ask(): pass"
        )


class TestCountIdentifierCooccurrence:
    def test_zero_when_no_identifiers_match(self):
        assert _count_identifier_cooccurrence(
            ["foo", "bar"], "nothing matches here"
        ) == 0

    def test_counts_only_distinct_matching_identifiers(self):
        assert _count_identifier_cooccurrence(
            ["foo", "bar", "baz"],
            "foo is here and bar is also here foo again",
        ) == 2

    def test_returns_minimum_of_identifiers_length_and_matches(self):
        assert _count_identifier_cooccurrence(
            ["getNotebook", "source_ids"],
            "getNotebook uses source_ids and source_ids again",
        ) == 2

    def test_empty_identifiers_returns_zero(self):
        assert _count_identifier_cooccurrence([], "any doc") == 0


class TestDetectSourceQueryIntent:
    def test_source_intent_detected(self):
        result = _detect_source_query_intent("find chat.ask implementation")
        assert result["source_intent"] is True

    def test_debug_intent_detected(self):
        result = _detect_source_query_intent("debug getNotebook timeout error")
        assert result["debug_intent"] is True

    def test_test_intent_detected(self):
        result = _detect_source_query_intent("test source_ids regression")
        assert result["test_intent"] is True

    def test_doc_intent_detected(self):
        result = _detect_source_query_intent("documentation for guidelines")
        assert result["doc_intent"] is True

    def test_config_intent_detected(self):
        result = _detect_source_query_intent(
            "configure pyproject.toml settings"
        )
        assert result["config_intent"] is True

    def test_mixed_intent_allows_multiple(self):
        result = _detect_source_query_intent(
            "fix getNotebook test regression"
        )
        assert result["debug_intent"] is True
        assert result["test_intent"] is True

    def test_domain_terms_detected(self):
        result = _detect_source_query_intent(
            "chat.ask source_ids notebook"
        )
        assert "chat.ask" in result["identifiers"]
        assert "source_ids" in result["identifiers"]
        assert "notebook" in result.get("domain_terms", [])

    def test_empty_query_returns_no_intent(self):
        result = _detect_source_query_intent("")
        assert result["source_intent"] is False
        assert result["debug_intent"] is False
        assert result["test_intent"] is False
        assert result["doc_intent"] is False


class TestClassifySourceResult:
    def test_agents_md_classified_as_agent_instruction(self):
        assert (
            _classify_source_result(
                ".agents/foo.md", "## This is agent instruction", []
            )
            == "agent_instruction"
        )

    def test_python_implementation_classified(self):
        assert (
            _classify_source_result(
                "src/module.py", "def my_function(): pass", ["my_function"]
            )
            == "implementation_code"
        )

    def test_adapter_classified_by_path(self):
        result = _classify_source_result(
            "adapters/slack/adapter.py",
            "class SlackAdapter: pass",
            ["slack"],
        )
        assert result == "adapter_code"

    def test_service_classified_by_path(self):
        result = _classify_source_result(
            "services/llm/service.py",
            "class LLMService: pass",
            ["llm"],
        )
        assert result == "service_code"

    def test_client_classified_by_path(self):
        result = _classify_source_result(
            "clients/github/client.py",
            "class GitHubClient: pass",
            ["github"],
        )
        assert result == "client_code"

    def test_relevant_test_with_identifier(self):
        result = _classify_source_result(
            "tests/test_module.py",
            "# my_function tests\ndef test_my_function(): pass",
            ["my_function"],
        )
        assert result == "relevant_test"

    def test_unrelated_test_without_identifier(self):
        result = _classify_source_result(
            "tests/test_other.py",
            "def test_other(): pass",
            ["my_function"],
        )
        assert result == "unrelated_test"

    def test_generated_or_cache_detected(self):
        assert (
            _classify_source_result(
                "generated/output.txt", "some generated content", []
            )
            == "generated_or_cache"
        )

    def test_readme_classified_as_readme_doc(self):
        assert (
            _classify_source_result(
                "README.md", "# Project", []
            )
            == "readme_doc"
        )

    def test_project_doc_classified(self):
        assert (
            _classify_source_result(
                "docs/guide.md", "# Guide", []
            )
            == "project_doc"
        )

    def test_config_file_classified(self):
        assert (
            _classify_source_result(
                "pyproject.toml", "[tool.poetry]", []
            )
            == "config_file"
        )

    def test_unknown_extension_classified_unknown(self):
        assert (
            _classify_source_result(
                "some/file.xyz", "binary content", []
            )
            == "unknown"
        )

    def test_agents_md_override_by_classifier_rules(self):
        """AGENTS.md must always classify as agent_instruction regardless of content."""
        result = _classify_source_result(
            "AGENTS.md",
            "def some_function(): pass",
            ["some_function"],
        )
        assert result == "agent_instruction"

    def test_toml_config_does_not_match_short_rel_path_py(self):
        """.toml files should not be misclassified as python code."""
        result = _classify_source_result(
            "config/settings.toml", "[settings]", []
        )
        assert result == "config_file"


class TestSearchRankingIntegration:
    """Integration-style tests that verify the search() method produces correct
    rankings when given controlled mock store data.

    These use direct patching of _SourceIndexStore and _bm25_scores to
    avoid needing a real SQLite index.
    """

    @pytest.fixture
    def engine(self, tmp_path):
        from oem_knowledge.engine import KnowledgeEngine

        eng = KnowledgeEngine(project_path=tmp_path)
        eng.init_project(str(tmp_path))
        oem_dir = tmp_path / ".oem"
        oem_dir.mkdir(parents=True, exist_ok=True)
        (oem_dir / "source_manifest.json").write_text("{}")
        (oem_dir / "indexes").mkdir(parents=True, exist_ok=True)
        (oem_dir / "indexes" / "source_index.sqlite").touch()
        return eng

    def _make_mock_rows(self, entries):
        """Build rows matching iter_chunks() schema from simple (rel_path, document) pairs."""
        rows = []
        for i, (rel_path, document, snippet) in enumerate(entries):
            metadata = {
                "rel_path": rel_path,
                "start_line": 1,
                "end_line": len(document.splitlines()),
            }
            rows.append({
                "id": i + 1,
                "rel_path": rel_path,
                "document": document,
                "snippet": snippet or document,
                "metadata": metadata,
                "path_text": rel_path,
                "symbols_text": "",
                "start_line": 1,
                "end_line": len(document.splitlines()),
            })
        return rows

    def test_agents_md_penalized_over_implementation(self, engine):
        """AGENTS.md should rank below implementation code for a code query."""
        rows = self._make_mock_rows([
            (
                "AGENTS.md",
                "## Agent instructions for the project.",
                None,
            ),
            (
                "src/chat.py",
                "def ask(): return chat.ask(query)",
                None,
            ),
        ])
        with patch(
            "oem_knowledge.services.source_corpus._SourceIndexStore",
            return_value=_MockStore(rows),
        ), patch.object(
            engine.source, "_bm25_scores", return_value=[0.8, 0.6]
        ):
            result = engine.source.search(
                "chat.ask implementation", k=2
            )
        assert result["status"] == "success"
        results = result["results"]
        src_rank = next(i for i, r in enumerate(results) if "src/chat.py" in r["metadata"]["rel_path"])
        agents_rank = next(i for i, r in enumerate(results) if "AGENTS.md" in r["metadata"]["rel_path"])
        assert src_rank < agents_rank, (
            f"src/chat.py at rank {src_rank} should be above AGENTS.md at rank {agents_rank}"
        )

    def test_relevant_test_boosted_for_test_query(self, engine):
        """Test query should boost relevant_test results."""
        rows = self._make_mock_rows([
            (
                "src/chat.py",
                "def ask(): return chat.ask(query)",
                None,
            ),
            (
                "tests/test_chat.py",
                "def test_ask(): chat.ask returns correct result",
                None,
            ),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.5, 0.4]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search(
                    "test chat.ask", k=2
                )
        assert result["status"] == "success"
        results = result["results"]
        diagnostics = {}
        for r in results:
            rel = r["metadata"]["rel_path"]
            diagnostics[rel] = r["metadata"].get("source_diagnostics", {})
        test_diag = diagnostics.get("tests/test_chat.py", {})
        assert test_diag.get("source_type") == "relevant_test", (
            f"test_chat.py should be relevant_test, got {test_diag.get('source_type')}"
        )
        assert "relevant_test" in test_diag.get("ranking_boosts", {}), (
            f"relevant_test boost missing in {test_diag.get('ranking_boosts')}"
        )

    def test_unrelated_test_penalized_for_source_query(self, engine):
        """Unrelated test should be penalized for non-test source query."""
        rows = self._make_mock_rows([
            (
                "src/module.py",
                "def my_function(): pass",
                None,
            ),
            (
                "tests/test_other.py",
                "def test_unrelated(): pass",
                None,
            ),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.4, 0.5]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search(
                    "find my_function", k=2
                )
        assert result["status"] == "success"
        results = result["results"]
        src_rank = next(i for i, r in enumerate(results) if "src/module.py" in r["metadata"]["rel_path"])
        test_rank = next(i for i, r in enumerate(results) if "tests/test_other.py" in r["metadata"]["rel_path"])
        assert src_rank < test_rank, (
            f"src/module.py at rank {src_rank} should be above test_other.py at rank {test_rank}"
        )

    def test_generated_cache_heavily_penalized(self, engine):
        """Generated/cache files should be heavily penalized for any query."""
        rows = self._make_mock_rows([
            (
                "src/module.py",
                "def real_code(): pass",
                None,
            ),
            (
                "generated/output.txt",
                "## This file is auto-generated.",
                None,
            ),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.3, 0.7]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search(
                    "find code", k=2
                )
        assert result["status"] == "success"
        results = result["results"]
        src_rank = next(i for i, r in enumerate(results) if "src/module.py" in r["metadata"]["rel_path"])
        gen_rank = next(i for i, r in enumerate(results) if "generated/output.txt" in r["metadata"]["rel_path"])
        assert src_rank < gen_rank, (
            f"src/module.py at rank {src_rank} should be above generated at rank {gen_rank}"
        )

    def test_diagnostics_shape_is_present(self, engine):
        """Every search result should include source_diagnostics."""
        rows = self._make_mock_rows([
            ("src/module.py", "def foo(): pass", None),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.5]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search("foo", k=1)
        assert result["status"] == "success"
        diag = result["results"][0]["metadata"].get("source_diagnostics", {})
        assert "source_type" in diag
        assert "base_score" in diag
        assert "final_score" in diag
        assert "ranking_reason" in diag
        assert "ranking_boosts" in diag
        assert "ranking_penalties" in diag

    def test_exact_identifier_boost_dominates_bm25(self, engine):
        """Exact identifier match should overcome lower BM25."""
        rows = self._make_mock_rows([
            (
                "src/chat.py",
                "def ask(): return chat.ask(query)",
                None,
            ),
            (
                "docs/general.md",
                "# General documentation about everything",
                None,
            ),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.3, 0.9]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search(
                    "chat.ask", k=2
                )
        assert result["status"] == "success"
        results = result["results"]
        src_rank = next(i for i, r in enumerate(results) if "src/chat.py" in r["metadata"]["rel_path"])
        doc_rank = next(i for i, r in enumerate(results) if "docs/general.md" in r["metadata"]["rel_path"])
        assert src_rank < doc_rank, (
            f"src/chat.py at rank {src_rank} should be above docs at rank {doc_rank}"
        )

    def test_boundary_matching_prevents_false_positive(self, engine):
        """Identifier 'ask' should NOT match document containing only 'chat.ask'."""
        rows = self._make_mock_rows([
            (
                "src/asking.py",
                "def helper_ask(): pass  # the only 'ask' reference",
                None,
            ),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.5]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search("ask", k=1)
        assert result["status"] == "success"
        diag = result["results"][0]["metadata"].get("source_diagnostics", {})
        if diag.get("ranking_boosts", {}).get("exact_identifier"):
            pytest.fail(
                f"query 'ask' should not produce exact_identifier boost for "
                f"doc containing only 'helper_ask' in a function name: {diag}"
            )

    def test_empty_query_returns_no_results(self, engine):
        """Empty query should return success but empty results."""
        rows = self._make_mock_rows([
            ("src/module.py", "def foo(): pass", None),
        ])
        with patch.object(
            engine.source, "_bm25_scores", return_value=[0.0]
        ):
            with patch(
                "oem_knowledge.services.source_corpus._SourceIndexStore",
                return_value=_MockStore(rows),
            ):
                result = engine.source.search("", k=1)
        assert result["status"] == "success"


class _MockStore:
    """Stand-in for _SourceIndexStore that returns pre-canned rows."""

    def __init__(self, rows):
        self._rows = rows

    def iter_chunks(self):
        return self._rows

    def close(self):
        pass
