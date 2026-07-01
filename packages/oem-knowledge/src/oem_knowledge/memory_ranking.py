from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

# Boost/penalty weights
BOOST_DECISION = 3.0
BOOST_FAILURE = 4.0
BOOST_EXACT_FILE_MATCH = 4.0
BOOST_EXACT_PATH_MATCH = 4.0
BOOST_EXACT_FILENAME_MATCH = 3.5
BOOST_IDENTIFIER_MATCH = 2.5
BOOST_TOPIC_MATCH = 1.0
BOOST_ACTIVE_WORK_SIGNAL = 2.0
BOOST_RECENT_HIGH_VALUE = 0.5

PENALTY_SEARCH_LOG = -4.0
PENALTY_COMMAND_LOG = -2.0
PENALTY_OPERATIONAL_TRANSCRIPT = -2.0
PENALTY_REPEATED_OBSERVATION = -1.0

# Memory type detection patterns
DECISION_PATTERNS = re.compile(r"(?:^|\n)(?:\s*)Decision:|##\s*Decision", re.IGNORECASE)
FAILURE_PATTERNS = re.compile(r"(?:^|\n)(?:\s*)Failure:|Do-not-repeat|Bug:|Regression:|BREAKING", re.IGNORECASE)
SEARCH_LOG_PATTERNS = re.compile(r"Search results?:|knowledge_search\(|oem knowledge search|ran:\s*knowledge_search", re.IGNORECASE)
COMMAND_LOG_PATTERNS = re.compile(r"Command\s+`.*|Shell output:|`$|\bRan:|\bsudo\b|\brm\s+-rf\b", re.IGNORECASE)
ACTIVE_WORK_PATTERNS = re.compile(r"\b(is the open project|open work item|active task|active work|working on|current project work)\b", re.IGNORECASE)


@dataclass
class RankingResult:
    base_score: float
    final_score: float
    memory_type: str
    ranking_reason: list[str] = field(default_factory=list)
    ranking_boosts: dict[str, float] = field(default_factory=dict)
    ranking_penalties: dict[str, float] = field(default_factory=dict)


def classify_memory_type(document: str, title: str | None = None, snippet: str | None = None) -> str:
    text = (title or "") + "\n" + (snippet or "") + "\n" + (document or "")
    first_lines = text.lstrip().split("\n", 4)[:3]
    head_text = "\n".join(first_lines).lower()

    for pattern in FAILURE_PATTERNS.finditer(head_text):
        if pattern:
            return "failure"
    for pattern in DECISION_PATTERNS.finditer(head_text):
        if pattern:
            return "decision"

    if SEARCH_LOG_PATTERNS.search(text):
        return "search_log"
    if COMMAND_LOG_PATTERNS.search(text):
        return "command_log"
    if ACTIVE_WORK_PATTERNS.search(text):
        return "active_work_signal"

    return "observation"


def extract_query_targets(query: str) -> dict[str, Any]:
    tokens = [t for t in re.findall(r"\w+", query) if len(t) > 1]
    return {
        "stems": tokens,
        "tokens": tokens,
        "files": [],
        "paths": [],
        "identifiers": [],
    }


def _apply_boosts(
    base_score: float,
    query_targets: dict[str, Any],
    document: str,
    memory_type: str,
) -> tuple[float, list[str], dict[str, float], dict[str, float]]:
    boosts: dict[str, float] = {}
    penalties: dict[str, float] = {}
    reasons: list[str] = []

    text_lower = document.lower()

    # Exact path match
    exact_path_matched = False
    for p in query_targets["paths"]:
        if p.lower() in text_lower:
            boosts["exact_path_match"] = BOOST_EXACT_PATH_MATCH
            reasons.append(f"exact path match: {p}")
            exact_path_matched = True
            break

    # Exact filename match
    if not exact_path_matched:
        for f in query_targets["files"] or query_targets["stems"]:
            if f.lower() in text_lower and "." in f:
                boosts["exact_filename_match"] = BOOST_EXACT_FILENAME_MATCH
                reasons.append(f"exact filename match: {f}")
                break

    # Identifier match
    if not exact_path_matched:
        for ident in query_targets["identifiers"] or query_targets["stems"]:
            if ident.lower() in text_lower and len(ident) >= 4:
                if "_" in ident or ident[0].isupper():
                    boosts["identifier_match"] = BOOST_IDENTIFIER_MATCH
                    reasons.append(f"identifier match: {ident}")
                    break

    # Topic/phrase match
    if not exact_path_matched and not boosts.get("identifier_match"):
        topic_hits = 0
        for t in query_targets["stems"]:
            if t.lower() in text_lower:
                topic_hits += 1
        if topic_hits >= 1:
            boosts["topic_match"] = BOOST_TOPIC_MATCH * topic_hits
            reasons.append(f"topic match ({topic_hits} terms)")

    # Active work signal
    if memory_type == "active_work_signal":
        boosts["active_work_signal"] = BOOST_ACTIVE_WORK_SIGNAL
        reasons.append("active work signal")

    # Memory-type boosts
    if memory_type == "decision":
        boosts["decision"] = BOOST_DECISION
        reasons.append("decision memory")
    elif memory_type == "failure":
        boosts["failure"] = BOOST_FAILURE
        reasons.append("failure memory")

    # Penalties
    if memory_type == "search_log":
        penalties["search_log"] = PENALTY_SEARCH_LOG
        reasons.append("downranked: search log")
    if memory_type == "command_log":
        penalties["command_log"] = PENALTY_COMMAND_LOG
        reasons.append("downranked: command log")

    boost_total = sum(boosts.values())
    penalty_total = sum(penalties.values())
    final_score = base_score + boost_total + penalty_total

    return final_score, reasons, boosts, penalties


def rank_search_result(query: str, candidate: dict[str, Any]) -> dict[str, Any]:
    query_targets = extract_query_targets(query)
    document = candidate.get("document", "")
    title = candidate.get("metadata", {}).get("title", "")
    snippet = candidate.get("snippet") or candidate.get("metadata", {}).get("snippet", "")

    base_score = float(candidate.get("score", 0.0))
    memory_type = classify_memory_type(document, title, snippet)

    final_score, reasons, boosts, penalties = _apply_boosts(
        base_score, query_targets, document, memory_type
    )

    result = dict(candidate)
    result["base_score"] = base_score
    result["score"] = final_score
    result["final_score"] = final_score
    result["memory_type"] = memory_type
    result["ranking_reason"] = reasons
    result["ranking_boosts"] = boosts
    result["ranking_penalties"] = penalties

    return result


def rank_search_results(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [rank_search_result(query, c) for c in candidates]

    ranked.sort(
        key=lambda x: (
            -x["final_score"],
            -x.get("base_score", 0.0),
            -(1 if x["memory_type"] == "decision" else 0),
            -(1 if x["memory_type"] == "failure" else 0),
            bool(x.get("ranking_boosts", {}).get("exact_path_match")),
            bool(x.get("ranking_boosts", {}).get("exact_filename_match")),
            bool(x.get("ranking_boosts", {}).get("identifier_match")),
            -(x.get("ranking_penalties", {}).get("search_log", 0.0)),
            -(x.get("ranking_penalties", {}).get("command_log", 0.0)),
            -x.get("ranking_boosts", {}).get("active_work_signal", 0.0),
            -x.get("base_score", 0.0),
            x.get("id", ""),
        )
    )
    return ranked