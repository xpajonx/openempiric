from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from oem_knowledge.retrieval import KNOWN_MEMORY_TYPES

# ============================================================================
# Stopwords, generic words, and helper (module-level)
# ============================================================================

HARD_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "from",
    "by", "at", "as", "is", "are", "was", "were", "be", "been", "being",
    "and", "or", "but", "if", "then", "than", "that", "this", "it", "its"
}

GENERIC_WORDS = {
    "current", "project", "content", "work", "continue", "session",
    "context", "file", "task", "next", "now", "state", "review",
    "health", "memory", "agent", "oem", "fix", "story", "page", "layout"
}

def clean_stem(w: str) -> str:
    w_low = w.lower()
    if w_low.endswith("ing"):
        w_low = w_low[:-3]
    elif w_low.endswith("ed"):
        w_low = w_low[:-2]
    elif w_low.endswith("es"):
        w_low = w_low[:-2]
    elif w_low.endswith("s") and not w_low.endswith("ss"):
        w_low = w_low[:-1]
    return w_low

# ============================================================================
# Boost/penalty weights (v3)
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

# v3: Technical handoff / workaround / identifier co-occurrence
BOOST_TECHNICAL_HANDOFF = 5.0
BOOST_WORKAROUND = 5.0
BOOST_DEBUG_NOTE = 4.0
BOOST_SESSION_HANDOFF_TECHNICAL = 4.0
BOOST_IDENTIFIER_COOCCURRENCE_ONE = 2.5
BOOST_IDENTIFIER_COOCCURRENCE_TWO = 5.0
BOOST_IDENTIFIER_COOCCURRENCE_THREE = 7.0
BOOST_IDENTIFIER_WITH_TERM = 3.0

PENALTY_COMMAND_LOG = -5.0
PENALTY_SEARCH_LOG = -5.0
PENALTY_SOURCE_DUMP = -6.0
PENALTY_LARGE_LOW_DENSITY = -3.0
PENALTY_GENERIC_ACTIVE_PROJECT_FOR_TECHNICAL = -4.0
LOW_QUALITY_PENALTY = -8.0
BOOST_TEMPORAL_MATCH = 2.0
BOOST_EXACT_DECISION_PHRASE = 5.0
BOOST_PREFERENCE = 4.0
BOOST_USER_SCOPE = 3.0

PENALTY_LARGE_CONCEPT = -3.0
STATUS_BOOSTS = {
    "canonical": 1.3,
    "validated": 1.1,
    "emerging": 0.9,
    "candidate": 0.7,
    "needs_review": 0.5,
}
PENALTY_LARGE_CONCEPT = -3.0
STATUS_BOOSTS = {
    "canonical": 1.3,
    "validated": 1.1,
    "emerging": 0.9,
    "candidate": 0.7,
    "needs_review": 0.5,
}

LARGE_CHUNK_CHAR_THRESHOLD = 1500

# ============================================================================
# Classification patterns (Failure/Decision/Outcome first)
# ============================================================================

DECISION_PATTERNS = re.compile(r"(?:^|\n)\s*Decision:|^##\s*Decision", re.IGNORECASE)
FAILURE_PATTERNS = re.compile(r"(?:^|\n)\s*Failure:|Do-not-repeat|Bug:|Regression:|BREAKING", re.IGNORECASE)
OUTCOME_PATTERNS = re.compile(r"(?:^|\n)\s*Outcome:", re.IGNORECASE)

# v3: Technical handoff / workaround / debug note patterns
TECHNICAL_HANDOFF_PATTERNS = re.compile(r"(?:^|\n)\s*(Handoff|Technical\s+Note|Technical\s+Handoff):", re.IGNORECASE)
WORKAROUND_PATTERNS = re.compile(r"\bworkaround\b", re.IGNORECASE)
DEBUG_NOTE_PATTERNS = re.compile(r"(?:^|\n)\s*(Bug|Regression|Debug\s+Note|Debug):", re.IGNORECASE)
SESSION_HANDOFF_PATTERNS = re.compile(r"session-handoff\.md", re.IGNORECASE)
TECHNICAL_DETAIL_TERMS = re.compile(r"\b(timeout|error|bug|workaround|adapter|debug|fix|explicitly|workaround|get_notebook|source_ids|chat\.ask)\b", re.IGNORECASE)

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
    raw_identifiers = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]{2,}\b', q)
    raw_identifiers = [i for i in raw_identifiers if not i.lower().endswith('.md')]

    # Tokens for topic
    raw_tokens = [t.lower() for t in re.findall(r'\w+', q) if len(t) > 1]

    # Rule intent detection
    rule_intent = bool(
        re.search(r'\b(decision|means|workflow|should|do not|never|avoid|unless|explicit|continue working|analyze|write|modify|open project|current project)\b', q, re.IGNORECASE)
    )

    # v3: Technical / debug intent detection
    technical_intent, debug_intent, technical_identifiers, project_terms = _detect_technical_intent(q, raw_identifiers)

    # Stopwords and generic terms classification
    CONDITIONAL_GENERIC = {"story", "page", "layout"}

    words_lower = [w.lower() for w in re.findall(r'\b\w+\b', q)]

    path_words = set()
    for p in full_paths + filenames + dir_parts:
        for seg in re.split(r'[/\._-]', p):
            if seg:
                path_words.add(seg.lower())

    # Pre-calculate strong signals using non-conditional semantics
    non_conditional_semantics = [
        w for w in words_lower
        if clean_stem(w) not in HARD_STOPWORDS and clean_stem(w) not in GENERIC_WORDS
    ]

    filtered_tech_ids = [ti for ti in technical_identifiers if clean_stem(ti) not in HARD_STOPWORDS]

    has_paths = len(full_paths) > 0
    has_files = len(filenames) > 0
    has_tech_ids = len(filtered_tech_ids) > 0
    has_project_terms = len(project_terms) > 0
    has_strong_signals = has_paths or has_files or has_tech_ids or has_project_terms or (len(non_conditional_semantics) > 0)

    if has_strong_signals:
        layout_pairing_words = {"responsive", "frontend", "mobile", "css", "grid", "page"}
        is_layout_paired = (
            "layout" in path_words or
            any(w in words_lower for w in layout_pairing_words) or
            bool(full_paths or filenames or dir_parts)
        )
        is_story_paired = (
            "story" in path_words or
            bool(full_paths or filenames or dir_parts)
        )
        is_page_paired = (
            "page" in path_words or
            bool(full_paths or filenames or dir_parts)
        )
    else:
        is_layout_paired = False
        is_story_paired = False
        is_page_paired = False

    extracted_stopwords = []
    extracted_generic = []
    extracted_semantic = []

    for w in words_lower:
        w_stem = clean_stem(w)
        if w in HARD_STOPWORDS or w_stem in HARD_STOPWORDS:
            if w not in extracted_stopwords:
                extracted_stopwords.append(w)
        elif w in GENERIC_WORDS or w_stem in GENERIC_WORDS:
            is_semantic = False
            if (w == "layout" or w_stem == "layout") and is_layout_paired:
                is_semantic = True
            elif (w == "story" or w_stem == "story") and is_story_paired:
                is_semantic = True
            elif (w == "page" or w_stem == "page") and is_page_paired:
                is_semantic = True
            
            if is_semantic:
                if w not in extracted_semantic:
                    extracted_semantic.append(w)
            else:
                if w not in extracted_generic:
                    extracted_generic.append(w)
        else:
            if w not in extracted_semantic:
                extracted_semantic.append(w)

    # Disable phrase extraction only for stopword/generic-only queries
    is_stopword_or_generic_only = all(
        (w in HARD_STOPWORDS or clean_stem(w) in HARD_STOPWORDS) or
        ((w in GENERIC_WORDS or clean_stem(w) in GENERIC_WORDS) and w not in extracted_semantic)
        for w in words_lower
    )

    words = [w for w in re.findall(r'\w+', q) if len(w) > 1]
    phrases = []
    if not is_stopword_or_generic_only:
        for length in range(4, min(8, len(words) + 1)):
            for i in range(len(words) - length + 1):
                phrases.append(' '.join(words[i:i+length]))

    # Filter stopwords and generic words from identifiers, stems, and tokens.
    def should_keep_token(tok: str) -> bool:
        tok_low = tok.lower()
        tok_stem = clean_stem(tok_low)
        if tok_low in HARD_STOPWORDS or tok_stem in HARD_STOPWORDS:
            return False
        if (tok_low in GENERIC_WORDS or tok_stem in GENERIC_WORDS) and tok_low not in extracted_semantic:
            return bool(has_strong_signals)
        return True

    # Filter identifiers, stems, and tokens
    filtered_technical_identifiers = [ti for ti in technical_identifiers if should_keep_token(ti)]
    stems = [s for s in stems if should_keep_token(s)]
    tokens = [t for t in raw_tokens if should_keep_token(t)]

    # Standard identifiers should only contain technical identifiers in queries
    identifiers_to_use = filtered_technical_identifiers

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

    # paths: full_paths + filenames + filename_stems (deduplicated)
    filename_stems = [re.sub(r'\.md$', '', f, flags=re.IGNORECASE) for f in filenames]
    paths_list = uniq(full_paths + filenames + filename_stems)
    files_list = uniq(filenames)

    return {
        "full_paths": uniq(full_paths),
        "filenames": uniq(filenames),
        "stems": uniq(stems),
        "dirs": uniq(dir_parts),
        "identifiers": uniq(identifiers_to_use),
        "tokens": uniq(tokens),
        "phrases": uniq(phrases),
        "rule_intent": rule_intent,
        "technical_intent": technical_intent,
        "debug_intent": debug_intent,
        "technical_identifiers": uniq(filtered_technical_identifiers),
        "project_terms": uniq(project_terms),
        # New keys for Part 4
        "files": files_list,
        "paths": paths_list,
        "semantic_terms": uniq(extracted_semantic),
        "generic_terms": uniq(extracted_generic),
        "stopwords": uniq(extracted_stopwords),
        "has_strong_signals": has_strong_signals,
    }




def _detect_technical_intent(query: str, general_identifiers: list[str]) -> tuple[bool, bool, list[str], list[str]]:
    q_lower = query.lower()

    technical_signals = [
        "timeout", "error", "bug", "workaround", "adapter",
        "source_ids", "chat.ask", "get_notebook",
    ]
    debug_signals = ["debug", "trace", "fix", "regression", "break"]

    has_technical = any(s in q_lower for s in technical_signals)
    has_debug = any(s in q_lower for s in debug_signals)

    function_like = re.findall(r'\b[A-Z][A-Z_0-9]{2,}(?:_[A-Z0-9]+)*\b', query)
    snake_case = re.findall(r'\b[a-z]+_[a-z]+\b', query)
    snake_case = [s for s in snake_case if len(s) >= 4]
    camel_case = re.findall(r'\b[A-Z][a-z]+[A-Z][a-zA-Z]*\b', query)
    dotted = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]+\b', query)
    # Exclude file extensions from dotted identifiers
    dotted = [d for d in dotted if not re.search(r'\.(md|py|ts|js|json|yaml|yml|toml|txt|css|html)$', d)]
    uppercase_constants = re.findall(r'\b[A-Z]{3,}\b', query)
    noise = {"THE", "AND", "FOR", "ARE", "NOT", "ALL", "GET", "SET", "PUT", "HOW", "WHY", "CAN", "YOU"}
    uppercase_constants = [u for u in uppercase_constants if u not in noise]

    technical_identifiers = list(set(
        function_like + snake_case + camel_case + dotted + uppercase_constants
    ))

    # Filter out identifiers that look like file extensions
    technical_identifiers = [t for t in technical_identifiers
                             if not t.lower().endswith((".md", ".py", ".ts", ".js"))]

    has_identifier_pattern = bool(
        function_like or snake_case or camel_case or dotted or uppercase_constants
    )
    # Only claim identifier pattern if we kept at least one technical identifier
    has_identifier_pattern = bool(technical_identifiers)

    technical_intent = (
        has_technical or has_debug or has_identifier_pattern
    )
    debug_intent = has_debug

    # Extract project terms from raw query (including hyphenated names)
    project_terms = re.findall(r'[a-zA-Z]+(?:-[a-zA-Z]+)+', query)
    project_terms = [p for p in project_terms if p.count("-") >= 2 and len(p) >= 6]

    return technical_intent, debug_intent, technical_identifiers, project_terms


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

def strip_code_fences(text: str) -> str:
    return re.sub(r"```\w*[\s\S]*?```", "", text)


def classify_memory_type(document: str, title: str | None = None, snippet: str | None = None) -> str:
    text = (title or "") + "\n" + (snippet or "") + "\n" + (document or "")
    clean_text = strip_code_fences(text)

    # Check failure patterns
    has_failure = (
        re.search(r"(?:^|\n)\s*#{1,6}\s*failure\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??failure(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE) or
        re.search(r"\bdo-not-repeat\b", clean_text, re.IGNORECASE) or
        re.search(r"\bbreaking\b", clean_text, re.IGNORECASE)
    )

    # Check decision patterns
    has_decision = (
        re.search(r"(?:^|\n)\s*#{1,6}\s*decision\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??decision(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE)
    )

    # Check outcome patterns
    has_outcome = (
        re.search(r"(?:^|\n)\s*#{1,6}\s*outcome\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??outcome(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE)
    )

    clean_head = clean_text.lstrip().split("\n", 3)[:3]
    clean_head_text = "\n".join(clean_head)

    # Priority 1: Failure
    if has_failure or FAILURE_PATTERNS.search(clean_head_text):
        return "failure"
    # Priority 2: Decision
    if has_decision or DECISION_PATTERNS.search(clean_head_text):
        return "decision"
    # Priority 3: Outcome
    if has_outcome or OUTCOME_PATTERNS.search(clean_head_text):
        return "outcome"

    # Support Observation: Constraint: Learning:
    has_observation = (
        re.search(r"(?:^|\n)\s*#{1,6}\s*observation\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??observation(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*#{1,6}\s*learning\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??learning(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*#{1,6}\s*constraint\b", clean_text, re.IGNORECASE) or
        re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:\*\*|__)??constraint(?:\*\*|__)??\s*:", clean_text, re.IGNORECASE)
    )

    # v3: Technical handoff / workaround / debug note — check before logs
    if TECHNICAL_HANDOFF_PATTERNS.search(clean_head_text):
        return "technical_handoff"
    if DEBUG_NOTE_PATTERNS.search(clean_head_text):
        return "debug_note"
    if WORKAROUND_PATTERNS.search(clean_text):
        return "workaround"
    if SESSION_HANDOFF_PATTERNS.search(clean_text) and TECHNICAL_DETAIL_TERMS.search(clean_text):
        return "technical_handoff"

    # Preference patterns (user preferences, style, conventions)
    if re.search(r"\b(prefer|like|always|never|don't|hate|style|convention)\b", clean_text, re.IGNORECASE):
        return "preference"

    # Episodic patterns (past experiences)
    if re.search(r"\b(last time|previously|tried|attempted|before)\b", clean_text, re.IGNORECASE):
        return "episodic"

    # Now check logs
    if SOURCE_DUMP_PATTERNS.search(clean_text):
        return "source_dump"
    if COMMAND_LOG_PATTERNS.search(clean_text):
        return "command_log"
    if SEARCH_LOG_PATTERNS.search(clean_text):
        return "search_log"
    if ACTIVE_WORK_PATTERNS.search(clean_text):
        return "active_work_signal"

    if has_observation:
        return "observation"

    return "observation"



def has_workflow_rule_signal(text: str) -> bool:
    norm = normalize_for_phrase(text)
    for rp in WORKFLOW_RULE_PHRASES:
        if normalize_for_phrase(rp) in norm:
            return True
    return False


def has_tech_id_boundary_match(text: str, tid: str) -> bool:
    escaped = re.escape(tid.lower())
    pattern = rf"(?<![a-zA-Z0-9_\.]){escaped}(?![a-zA-Z0-9_\.])"
    return bool(re.search(pattern, text.lower()))


def has_word_boundary_match(text: str, token: str) -> bool:
    escaped = re.escape(token.lower())
    pattern = rf"(?<![a-zA-Z0-9_\.]){escaped}(?:s|es)?(?![a-zA-Z0-9_\.])"
    return bool(re.search(pattern, text.lower()))


# ============================================================================
# Core ranking
# ============================================================================

def _compute_exact_and_phrase_signals(query_targets: dict[str, Any], document: str) -> tuple[dict[str, float], list[str], bool, int]:
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
            if has_tech_id_boundary_match(text_lower, ident) and len(ident) >= 3:
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
    if query_targets.get("rule_intent"):
        for rp in WORKFLOW_RULE_PHRASES:
            if has_phrase_match(document, rp):
                rule_phrase_boost = max(rule_phrase_boost, BOOST_RULE_PHRASE)
                reasons.append(f"workflow rule phrase: '{rp}' +{BOOST_RULE_PHRASE}")
    if rule_phrase_boost:
        boosts["rule_phrase"] = rule_phrase_boost

    # v3: Identifier co-occurrence count (for co-occurrence boost in _apply_boosts)
    tech_ids = query_targets.get("technical_identifiers", [])
    identifier_cooccurrence_count = sum(1 for tid in tech_ids if has_tech_id_boundary_match(text_lower, tid) and len(tid) >= 3)

    return boosts, reasons, has_exact, identifier_cooccurrence_count


def _apply_boosts(
    base_score: float,
    query_targets: dict[str, Any],
    document: str,
    memory_type: str,
) -> tuple[float, float, float, bool, list[str], dict[str, float], dict[str, float]]:
    boosts: dict[str, float] = {}
    penalties: dict[str, float] = {}
    reasons: list[str] = []

    text_lower = document.lower()
    doc_len = len(document)
    query_text = query_targets.get("query_text", "")

    # Exact path / filename / identifier / phrase signals
    exact_boosts, exact_reasons, has_exact_match, id_cooccurrence = _compute_exact_and_phrase_signals(query_targets, document)
    boosts.update(exact_boosts)
    reasons.extend(exact_reasons)

    # Active / open work signal (independent)
    if (ACTIVE_WORK_PATTERNS.search(document) or query_targets.get("rule_intent")) and query_targets.get("has_strong_signals", True):
        if "active_work_signal" not in boosts:
            boosts["active_work_signal"] = BOOST_ACTIVE_WORK_SIGNAL
            reasons.append("active/open work signal")

    # Workflow rule signal
    if has_workflow_rule_signal(document) and query_targets.get("has_strong_signals", True):
        boosts["workflow_rule"] = BOOST_WORKFLOW_RULE
        reasons.append("workflow rule signal")

    # Relevance Floor check
    has_exact_file = "exact_filename_match" in boosts
    has_exact_path = "exact_path_match" in boosts
    has_exact_phrase = "exact_phrase" in boosts
    has_workflow_rule = (("workflow_rule" in boosts) or ("rule_phrase" in boosts)) and bool(query_targets.get("rule_intent"))
    has_tech_id = id_cooccurrence >= 1

    # Active work alignment (Refinement 1)
    active_work_aligned = False
    if ACTIVE_WORK_PATTERNS.search(document):
        has_query_paths_or_files = bool(query_targets.get("full_paths") or query_targets.get("filenames"))
        has_continuation_intent = any(p in query_text.lower() for p in ("continue", "working on", "current project", "active task"))
        strong_semantic_tokens = [
            t for t in query_targets.get("tokens", [])
            if t.lower() not in HARD_STOPWORDS and clean_stem(t.lower()) not in HARD_STOPWORDS and
               t.lower() not in GENERIC_WORDS and clean_stem(t.lower()) not in GENERIC_WORDS
        ]
        semantic_overlap_count = sum(1 for t in strong_semantic_tokens if t in text_lower)
        if has_query_paths_or_files or has_continuation_intent or semantic_overlap_count >= 2:
            active_work_aligned = True

    # Semantic overlap count (Refinement 3)
    strong_semantic_tokens = [
        t for t in query_targets.get("tokens", [])
        if t.lower() not in HARD_STOPWORDS and clean_stem(t.lower()) not in HARD_STOPWORDS and
           t.lower() not in GENERIC_WORDS and clean_stem(t.lower()) not in GENERIC_WORDS
    ]
    semantic_overlap_count = sum(1 for t in strong_semantic_tokens if t in text_lower)

    eligible_for_type_boost = (
        has_exact_file or
        has_exact_path or
        has_exact_phrase or
        has_workflow_rule or
        has_tech_id or
        active_work_aligned or
        semantic_overlap_count >= 2
    )

    # Memory type boosts (suppressed if floor not met)
    importance_boost = 0.0
    if memory_type == "decision":
        importance_boost = BOOST_DECISION
    elif memory_type == "failure":
        importance_boost = BOOST_FAILURE
    elif memory_type == "outcome":
        importance_boost = BOOST_OUTCOME

    if importance_boost > 0.0:
        if eligible_for_type_boost:
            boosts[memory_type] = importance_boost
            reasons.append(f"{memory_type} memory")
        else:
            reasons.append(f"{memory_type} boost suppressed: relevance floor not met")

    # Hard-capped topic match
    topic_hits = 0
    for t in query_targets.get("tokens", []):
        if has_word_boundary_match(text_lower, t):
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

    # ========================================================================
    # v3: Technical handoff / workaround / identifier co-occurrence
    # ========================================================================

    # Part 3: Identifier co-occurrence boost (technical query only)
    if query_targets.get("technical_intent") and id_cooccurrence > 0:
        if id_cooccurrence >= 3:
            boosts["identifier_cooccurrence"] = BOOST_IDENTIFIER_COOCCURRENCE_THREE
            reasons.append(f"identifier co-occurrence: {id_cooccurrence}+ ids +{BOOST_IDENTIFIER_COOCCURRENCE_THREE}")
        elif id_cooccurrence >= 2:
            boosts["identifier_cooccurrence"] = BOOST_IDENTIFIER_COOCCURRENCE_TWO
            reasons.append(f"identifier co-occurrence: {id_cooccurrence} ids +{BOOST_IDENTIFIER_COOCCURRENCE_TWO}")
        else:
            boosts["identifier_cooccurrence"] = BOOST_IDENTIFIER_COOCCURRENCE_ONE
            reasons.append(f"identifier co-occurrence: {id_cooccurrence} id +{BOOST_IDENTIFIER_COOCCURRENCE_ONE}")

    # Part 4: Technical handoff / workaround / debug note boost
    if query_targets.get("technical_intent") or query_targets.get("debug_intent"):
        if memory_type == "technical_handoff":
            boosts["technical_handoff"] = BOOST_TECHNICAL_HANDOFF
            reasons.append(f"technical handoff memory +{BOOST_TECHNICAL_HANDOFF}")
        elif memory_type == "workaround":
            boosts["workaround"] = BOOST_WORKAROUND
            reasons.append(f"workaround memory +{BOOST_WORKAROUND}")
        elif memory_type == "debug_note":
            boosts["debug_note"] = BOOST_DEBUG_NOTE
            reasons.append(f"debug note memory +{BOOST_DEBUG_NOTE}")

        # Extra session-handoff match
        if SESSION_HANDOFF_PATTERNS.search(text_lower) and id_cooccurrence >= 1:
            if "session_handoff_technical" not in boosts:
                boosts["session_handoff_technical"] = BOOST_SESSION_HANDOFF_TECHNICAL
                reasons.append(f"session handoff technical match +{BOOST_SESSION_HANDOFF_TECHNICAL}")

    # Part 5: Downrank generic active-project decisions for technical queries
    if query_targets.get("technical_intent") and "active_work_signal" in boosts:
        has_technical_identifier = any(
            has_tech_id_boundary_match(text_lower, tid) and len(tid) >= 3
            for tid in query_targets.get("technical_identifiers", [])
        )
        if not has_technical_identifier and id_cooccurrence == 0:
            penalties["generic_active_project_for_technical"] = PENALTY_GENERIC_ACTIVE_PROJECT_FOR_TECHNICAL
            reasons.append(f"downranked: generic active-project decision for technical query {PENALTY_GENERIC_ACTIVE_PROJECT_FOR_TECHNICAL}")

    # Identifier + workaround/timeout/debug term additional boost
    if id_cooccurrence >= 1 and TECHNICAL_DETAIL_TERMS.search(document):
        if "identifier_with_term" not in boosts:
            boosts["identifier_with_term"] = BOOST_IDENTIFIER_WITH_TERM
            reasons.append(f"identifier with workaround/timeout/debug term +{BOOST_IDENTIFIER_WITH_TERM}")

    # Part 6: Temporal match boost - when both query and document share temporal context
    query_text = (query_targets.get("query_text") or "").lower()
    temporal_pattern = r"\b(since|recently|recent|last|before|after|currently|today|yesterday|previous|earlier|later|now)\b"
    doc_has_temporal = bool(re.search(temporal_pattern, text_lower))
    query_has_temporal = bool(re.search(temporal_pattern, query_text))
    if doc_has_temporal and query_has_temporal:
        boosts["temporal_match"] = BOOST_TEMPORAL_MATCH
        reasons.append(f"temporal match +{BOOST_TEMPORAL_MATCH}")

    # Part 7: Exact decision phrase boost
    if re.search(r"\b(we decided to)\b", text_lower):
        boosts["exact_decision_phrase"] = BOOST_EXACT_DECISION_PHRASE
        reasons.append(f"exact decision phrase +{BOOST_EXACT_DECISION_PHRASE}")

    # Split boosts to calculate relevance_score and final_score correctly
    type_boost_val = boosts.get(memory_type, 0.0) if memory_type in boosts else 0.0
    other_boosts_sum = sum(v for k, v in boosts.items() if k != memory_type)
    
    relevance_score = base_score + other_boosts_sum + sum(penalties.values())
    final_score = relevance_score + type_boost_val

    return final_score, relevance_score, importance_boost, eligible_for_type_boost, reasons, boosts, penalties


def rank_search_result(query: str, candidate: dict[str, Any], registry: dict[str, Any] | None = None) -> dict[str, Any]:
    query_targets = extract_query_targets(query)
    query_targets["query_text"] = query
    document = candidate.get("document", "")
    title = candidate.get("metadata", {}).get("title", "")
    snippet = candidate.get("snippet") or candidate.get("metadata", {}).get("snippet", "")

    base_score = float(candidate.get("score", 0.0))
    memory_type = None
    metadata = candidate.get("metadata")
    if isinstance(metadata, dict):
        meta_type = metadata.get("memory_type")
        if isinstance(meta_type, str) and meta_type in KNOWN_MEMORY_TYPES:
            memory_type = meta_type
    if memory_type is None:
        memory_type = classify_memory_type(document, title, snippet)

    final_score, relevance_score, importance_boost, eligible_for_type_boost, reasons, boosts, penalties = _apply_boosts(
        base_score, query_targets, document, memory_type
    )

    if registry:
        concept_id = candidate.get("metadata", {}).get("concept_id", "")
        if concept_id and concept_id in registry:
            cdata = registry[concept_id]
            evidence_count = cdata.get("evidence_count", 0)
            if evidence_count > 100:
                size_penalty = max(0.3, 1.0 - (evidence_count - 100) / 500.0)
                final_score *= size_penalty
                reasons.append(f"concept size penalty ({evidence_count} events): x{size_penalty:.2f}")
            status = cdata.get("status", "candidate")
            status_boost = STATUS_BOOSTS.get(status, 1.0)
            if status_boost != 1.0:
                final_score *= status_boost
                reasons.append(f"concept status boost ({status}): x{status_boost}")
            confidence = cdata.get("confidence", 1)
            conf_boost = 0.5 + confidence / 10.0
            final_score *= conf_boost
            reasons.append(f"concept confidence boost ({confidence}/5): x{conf_boost:.2f}")

    result = dict(candidate)
    result["base_score"] = base_score
    result["score"] = final_score
    result["final_score"] = final_score
    result["relevance_score"] = relevance_score
    result["importance_boost"] = importance_boost
    result["eligible_for_type_boost"] = eligible_for_type_boost
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
        item["relevance_score"] = c.get("relevance_score", 0.0)
        item["importance_boost"] = c.get("importance_boost", 0.0)
        item["eligible_for_type_boost"] = c.get("eligible_for_type_boost", False)
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
            "technical_intent": targets.get("technical_intent", False),
            "debug_intent": targets.get("debug_intent", False),
            "technical_identifiers": targets.get("technical_identifiers", []),
            "project_terms": targets.get("project_terms", []),
        },
        "raw_candidates": raw_display,
        "reranked_candidates": reranked_display,
    }


def rank_search_results(query: str, candidates: list[dict[str, Any]], registry: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    ranked = [rank_search_result(query, c, registry) for c in candidates]

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
