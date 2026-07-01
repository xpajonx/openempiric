from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# Boost/penalty weights (v2)
# ============================================================================

BOOST_DECISION = 4.0
BOOST_FAILURE = 5.0
BOOST_OUTCOME = 3.0
BOOST_EXACT_PATH_MATCH = 6.0
BOOST_EXACT_FILENAME_MATCH = 4.0
BOOST_IDENTIFIER_MATCH = 2.5
BOOST_TOPIC_MATCH = 2.0  # HARD CAP
BOOST_ACTIVE_WORK_SIGNAL = 3.0
BOOST_WORKFLOW_RULE = 4.0
BOOST_EXACT_PHRASE = 8.0
BOOST_NEAR_EXACT_RULE = 6.0
BOOST_RULE_PHRASE = 3.0

PENALTY_COMMAND_LOG = -5.0
PENALTY_SEARCH_LOG = -5.0
PENALTY_SOURCE_DUMP = -6.0
PENALTY_LARGE_LOW_DENSITY = -3.0

LARGE_CHUNK_CHAR_THRESHOLD = 1500

# ============================================================================
# Classification patterns (Failure/Decision/Outcome first)
# ============================================================================

DECISION_PATTERNS = re.compile(r"(?:^|\n)\s*Decision:|^##\s*Decision", re.IGNORECASE)
FAILURE_PATTERNS = re.compile(r"(?:^|\n)\s*Failure:|Do-not-repeat|Bug:|Regression:|BREAKING", re.IGNORECASE)
OUTCOME_PATTERNS = re.compile(r"(?:^|\n)\s*Outcome:", re.IGNORECASE)

# Expanded command / search / source dump patterns
COMMAND_LOG_PATTERNS = re.compile(
    r"Command\s+`|Command output:|Shell output:|Full source dump|File contents:|cat\s+<<EOF",
    re.IGNORECASE,
)
SEARCH_LOG_PATTERNS = re.compile(
    r"Search results?:|knowledge_search\(|knowledge_read\(|oem preflight|oem health",
    re.IGNORECASE,
)
SOURCE_DUMP_PATTERNS = re.compile(r"Full source dump|File contents:|cat\s+<<EOF", re.IGNORECASE)

ACTIVE_WORK_PATTERNS = re.compile(
    r"\b(is the open project|open work item|active task|active work|working on|current project work)\b",
    re.IGNORECASE,
)

# Workflow / rule phrases (used for both classification and boosts)
WORKFLOW_RULE_PHRASES = [
    "do not modify",
    "unless explicit",
    "continue working",
    "means analyze",
    "not write",
    "inspect understand tone propose changes",
    "open project",
    "is the open project",
]

# ============================================================================
# Query target extraction (deterministic, strong for paths)
# ============================================================================

def extract_query_targets(query: str) -> dict[str, Any]:
    q = query.strip()

    # Full relative paths (e.g. 2_Essay/expertise-debt/Essay_ID.md)
    full_paths = re.findall(r'[\w./-]+\.md', q)
    full_paths = [p for p in full_paths if '/' in p or '\\' in p]

    # Filenames
    filenames = re.findall(r'[\w-]+\.md', q)

    # Stems (without .md)
    stems = [re.sub(r'\.md$', '', f, flags=re.IGNORECASE) for f in filenames]
    # Also capture directory parts like 2_Essay/expertise-debt
    dir_parts = re.findall(r'[\w-]+(?:/[\w-]+)+', q)
    # Add bare stems from path segments
    for p in full_paths + dir_parts:
        for seg in re.split(r'[/\\]', p):
            if seg and len(seg) > 1 and not seg.endswith('.md'):
                stems.append(seg)

    # Identifiers (camel, snake, or uppercase-ish)
    identifiers = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', q)
    identifiers = [i for i in identifiers if not i.lower().endswith('.md')]

    # Tokens for topic
    tokens = [t.lower() for t in re.findall(r'\w+', q) if len(t) > 1]

    # 4+ consecutive token phrases (normalized later)
    words = [w for w in re.findall(r'\w+', q) if len(w) > 1]
    phrases = []
    for length in range(4, min(8, len(words) + 1)):
        for i in range(len(words) - length + 1):
            phrases.append(' '.join(words[i:i+length]))

    # Rule intent detection
    rule_intent = bool(
        re.search(r'\b(decision|means|workflow|should|do not|never|avoid|unless|explicit|continue working|analyze|write|modify|open project|current project)\b', q, re.IGNORECASE)
    )

    # Dedup
    def uniq(seq):
        seen = set()
        out = []
        for x in seq:
            lx = x.lower()
            if lx not in seen:
                seen.add(lx)
                out.append(x)
        return out

    return {
        "full_paths": uniq(full_paths),
        "filenames": uniq(filenames),
        "stems": uniq(stems),
        "dirs": uniq(dir_parts),
        "identifiers": uniq(identifiers),
        "tokens": uniq(tokens),
        "phrases": uniq(phrases),
        "rule_intent": rule_intent,
    }


def normalize_for_phrase(text: str) -> str:
    """Normalize for near-exact phrase matching (punctuation, case, light stopwords)."""
    t = text.lower()
    # normalize quotes/apostrophes
    t = re.sub(r"['’`]", "'", t)
    # normalize breaks
    t = re.sub(r'[:;]+', ',', t)
    # remove most punctuation except comma and apostrophe
    t = re.sub(r'[^\w\s,\']', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    # drop very common stopwords for phrase purposes
    stopwords = {"the", "a", "an", "file", "user", "says", "to", "it", "is"}
    words = [w for w in t.split() if w not in stopwords]
    return ' '.join(words)


def has_phrase_match(document: str, phrase: str) -> bool:
    norm_doc = normalize_for_phrase(document)
    norm_phrase = normalize_for_phrase(phrase)
    if not norm_phrase:
        return False
    return norm_phrase in norm_doc


def has_consecutive_phrase_match(document: str, phrase: str, min_tokens: int = 4) -> bool:
    """Check for 4+ consecutive token phrase after normalization."""
    norm_doc = normalize_for_phrase(document)
    norm_phrase = normalize_for_phrase(phrase)
    if not norm_phrase:
        return False
    doc_words = norm_doc.split()
    phrase_words = norm_phrase.split()
    if len(phrase_words) < min_tokens:
        return False
    phrase_str = ' '.join(phrase_words)
    # sliding window
    for i in range(len(doc_words) - len(phrase_words) + 1):
        if ' '.join(doc_words[i:i+len(phrase_words)]) == phrase_str:
            return True
    return False


# ============================================================================
# Classification (high-value first)
# ============================================================================

def classify_memory_type(document: str, title: str | None = None, snippet: str | None = None) -> str:
    text = (title or "") + "\n" + (snippet or "") + "\n" + (document or "")
    head = text.lstrip().split("\n", 3)[:3]
    head_text = "\n".join(head)

    if FAILURE_PATTERNS.search(head_text):
        return "failure"
    if DECISION_PATTERNS.search(head_text):
        return "decision"
    if OUTCOME_PATTERNS.search(head_text):
        return "outcome"

    # Now check logs (but we will not override if it was a Failure/Decision)
    if SOURCE_DUMP_PATTERNS.search(text):
        return "source_dump"
    if COMMAND_LOG_PATTERNS.search(text):
        return "command_log"
    if SEARCH_LOG_PATTERNS.search(text):
        return "search_log"
    if ACTIVE_WORK_PATTERNS.search(text):
        return "active_work_signal"

    return "observation"


def has_workflow_rule_signal(text: str) -> bool:
    norm = normalize_for_phrase(text)
    for rp in WORKFLOW_RULE_PHRASES:
        if normalize_for_phrase(rp) in norm:
            return True
    return False


# ============================================================================
# Core ranking
# ============================================================================

def _compute_exact_and_phrase_signals(query_targets: dict[str, Any], document: str) -> tuple[dict[str, float], list[str], bool]:
    boosts: dict[str, float] = {}
    reasons: list[str] = []
    has_exact = False
    text_lower = document.lower()

    # Exact full path (strongest)
    for p in query_targets.get("full_paths", []):
        if p.lower() in text_lower:
            boosts["exact_path_match"] = BOOST_EXACT_PATH_MATCH
            reasons.append(f"exact path match: {p} +{BOOST_EXACT_PATH_MATCH}")
            has_exact = True
            break

    # Exact filename
    if "exact_path_match" not in boosts:
        for f in query_targets.get("filenames", []):
            if f.lower() in text_lower:
                boosts["exact_filename_match"] = BOOST_EXACT_FILENAME_MATCH
                reasons.append(f"exact filename match: {f} +{BOOST_EXACT_FILENAME_MATCH}")
                has_exact = True
                break

    # Stem / identifier
    if not has_exact:
        for ident in query_targets.get("stems", []) + query_targets.get("identifiers", []):
            if ident.lower() in text_lower and len(ident) >= 3:
                boosts["identifier_match"] = BOOST_IDENTIFIER_MATCH
                reasons.append(f"identifier/stem match: {ident}")
                has_exact = True  # counts as exact-ish for density
                break

    # Exact consecutive phrase (4+ tokens)
    phrase_boost = 0.0
    for ph in query_targets.get("phrases", []):
        if has_consecutive_phrase_match(document, ph):
            phrase_boost = max(phrase_boost, BOOST_EXACT_PHRASE)
            reasons.append(f"exact phrase match: '{ph}' +{BOOST_EXACT_PHRASE}")
            has_exact = True
    if phrase_boost:
        boosts["exact_phrase"] = phrase_boost

    # Near-exact rule / workflow phrases (normalized)
    rule_phrase_boost = 0.0
    for rp in WORKFLOW_RULE_PHRASES:
        if has_phrase_match(document, rp):
            rule_phrase_boost = max(rule_phrase_boost, BOOST_RULE_PHRASE)
            reasons.append(f"workflow rule phrase: '{rp}' +{BOOST_RULE_PHRASE}")
    if rule_phrase_boost:
        boosts["rule_phrase"] = rule_phrase_boost

    return boosts, reasons, has_exact


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
    doc_len = len(document)

    # Exact path / filename / identifier / phrase signals
    exact_boosts, exact_reasons, has_exact_match = _compute_exact_and_phrase_signals(query_targets, document)
    boosts.update(exact_boosts)
    reasons.extend(exact_reasons)

    # Memory type boosts
    if memory_type == "decision":
        boosts["decision"] = BOOST_DECISION
        reasons.append("decision memory")
    elif memory_type == "failure":
        boosts["failure"] = BOOST_FAILURE
        reasons.append("failure memory")
    elif memory_type == "outcome":
        boosts["outcome"] = BOOST_OUTCOME
        reasons.append("outcome memory")

    # Active / open work signal (independent)
    if ACTIVE_WORK_PATTERNS.search(document) or query_targets.get("rule_intent"):
        if "active_work_signal" not in boosts:
            boosts["active_work_signal"] = BOOST_ACTIVE_WORK_SIGNAL
            reasons.append("active/open work signal")

    # Workflow rule signal
    if has_workflow_rule_signal(document):
        boosts["workflow_rule"] = BOOST_WORKFLOW_RULE
        reasons.append("workflow rule signal")

    # Hard-capped topic match
    topic_hits = 0
    for t in query_targets.get("tokens", []):
        if t in text_lower:
            topic_hits += 1
    if topic_hits > 0:
        topic_boost = min(BOOST_TOPIC_MATCH, 0.5 * min(topic_hits, 4))
        # ensure hard cap
        topic_boost = min(topic_boost, BOOST_TOPIC_MATCH)
        boosts["topic_match"] = topic_boost
        reasons.append(f"topic match capped: {topic_hits} terms -> +{topic_boost}")

    # Penalties for logs / dumps
    if memory_type == "search_log":
        penalties["search_log"] = PENALTY_SEARCH_LOG
        reasons.append("downranked: search_log")
    if memory_type == "command_log":
        penalties["command_log"] = PENALTY_COMMAND_LOG
        reasons.append("downranked: command_log")
    if memory_type == "source_dump":
        penalties["source_dump"] = PENALTY_SOURCE_DUMP
        reasons.append("downranked: source_dump")

    # Large low-density chunk penalty
    is_large = doc_len > LARGE_CHUNK_CHAR_THRESHOLD
    if is_large and not has_exact_match:
        penalties["large_low_density"] = PENALTY_LARGE_LOW_DENSITY
        reasons.append("large low-density chunk penalty")

    # Combined heavy penalty for large command/source dumps without exacts
    if is_large and memory_type in ("command_log", "source_dump") and not has_exact_match:
        penalties["large_command_or_source"] = -8.0
        reasons.append("large command/source dump penalty")

    final_score = base_score + sum(boosts.values()) + sum(penalties.values())
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


def _candidate_to_stable_item(c: dict[str, Any], rank: int, *, include_ranking_fields: bool = False) -> dict[str, Any]:
    meta = c.get("metadata", {})
    document = c.get("document", "") or ""
    memory_type = c.get("memory_type") or classify_memory_type(
        document,
        meta.get("title"),
        None,
    )
    item: dict[str, Any] = {
        "rank": rank,
        "id": c.get("id", ""),
        "chunk_id": c.get("id", ""),
        "source_id": meta.get("source", ""),
        "title": meta.get("title", ""),
        "source_path": meta.get("source", ""),
        "snippet": document[:200],
        "base_score": c.get("base_score", c.get("score", 0.0)),
        "memory_type": memory_type,
    }
    if include_ranking_fields or "final_score" in c:
        item["final_score"] = c.get("final_score", c.get("score", 0.0))
        item["ranking_reason"] = c.get("ranking_reason", [])
        item["ranking_boosts"] = c.get("ranking_boosts", {})
        item["ranking_penalties"] = c.get("ranking_penalties", {})
    return item


def build_ranking_debug_report(
    query: str,
    targets: dict[str, Any],
    raw_candidates: list[dict[str, Any]],
    reranked_candidates: list[dict[str, Any]],
    k: int = 3,
    candidate_pool_size: int = 0,
    used_fallback: bool = False,
) -> dict[str, Any]:
    """Build a deterministic debug report separating raw candidates from reranked results.

    Raw candidates are shown *before* reranking (only base_score + memory_type).
    Reranked candidates are shown *after* reranking (includes final_score and ranking diagnostics).

    Stable identity fields (id, chunk_id, source_id, title, source_path) are included
    on every candidate so test failures can be classified as recall vs rerank vs preflight.
    """
    raw_count = len(raw_candidates)
    reranked_count = len(reranked_candidates)

    raw_display = [_candidate_to_stable_item(c, i + 1) for i, c in enumerate(raw_candidates[:50])]
    reranked_display = [
        _candidate_to_stable_item(c, i + 1, include_ranking_fields=True)
        for i, c in enumerate(reranked_candidates[:k])
    ]

    return {
        "query": query,
        "k": k,
        "candidate_pool_size": candidate_pool_size or raw_count,
        "raw_candidate_count": raw_count,
        "reranked_candidate_count": reranked_count,
        "returned_count": min(k, len(reranked_candidates)),
        "used_fallback": used_fallback,
        "targets": {
            "paths": targets.get("full_paths", []),
            "files": targets.get("filenames", []),
            "identifiers": targets.get("stems", []) + targets.get("identifiers", []),
            "phrases": targets.get("phrases", []),
            "rule_intent": targets.get("rule_intent", False),
        },
        "raw_candidates": raw_display,
        "reranked_candidates": reranked_display,
    }


def rank_search_results(query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = [rank_search_result(query, c) for c in candidates]

    # Stable sort with priority: exact path/filename > decision/failure > active/rule > final_score > base
    ranked.sort(
        key=lambda x: (
            -x["final_score"],
            -x.get("base_score", 0.0),
            bool(x.get("ranking_boosts", {}).get("exact_path_match")),
            bool(x.get("ranking_boosts", {}).get("exact_filename_match")),
            -(1 if x["memory_type"] in ("decision", "failure") else 0),
            bool(x.get("ranking_boosts", {}).get("active_work_signal")),
            bool(x.get("ranking_boosts", {}).get("workflow_rule")),
            bool(x.get("ranking_boosts", {}).get("exact_phrase")),
            -(x.get("ranking_penalties", {}).get("search_log", 0.0)),
            -(x.get("ranking_penalties", {}).get("command_log", 0.0)),
            -(x.get("ranking_penalties", {}).get("source_dump", 0.0)),
            -x.get("base_score", 0.0),
            x.get("id", ""),
        )
    )
    return ranked
