from __future__ import annotations

from dataclasses import dataclass

from .models import ConceptMetadata, MemoryMetadata, PreflightMatch, SkillMetadata
from .triggers import contains_phrase, lexical_overlap, normalize_text, shared_tokens, unique_tokens

APPROVED_SKILL_EXACT_TRIGGER_WEIGHT = 6.0
EXACT_CONCEPT_TITLE_WEIGHT = 5.0
SKILL_TAG_WEIGHT = 4.0
CONCEPT_TAG_WEIGHT = 3.0
ALIAS_WEIGHT = 3.0
LEXICAL_OVERLAP_WEIGHT = 2.0
MEMORY_HIT_WEIGHT = 2.0
SOURCE_HINT_WEIGHT = 1.0
STALE_STATUS_PENALTY = 4.0

# Memory type weight multipliers (applied to MEMORY_HIT_WEIGHT)
# Decision → 6.0 (≥4 SUGGEST), Failure → 5.0, Outcome → 4.0, Observation → 2.0
MEMORY_TYPE_WEIGHTS: dict[str, float] = {
    "decision": 3.0,
    "failure": 2.5,
    "outcome": 2.0,
    "observation": 1.0,
}

REQUIRED_THRESHOLD = 8.0
SUGGEST_THRESHOLD = 4.0

NEGATIVE_SKILL_STATUSES = {"rejected", "deprecated", "superseded", "stale"}
NEGATIVE_CONCEPT_STATUSES = {"deprecated", "stale", "missing_file"}


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    reason: str


def score_skill(task: str, skill: SkillMetadata) -> ScoreBreakdown:
    normalized_task = normalize_text(task)
    task_tokens = unique_tokens(task)
    score = 0.0
    reasons: list[str] = []

    for trigger in skill.triggers:
        if contains_phrase(normalized_task, trigger):
            score += APPROVED_SKILL_EXACT_TRIGGER_WEIGHT
            reasons.append(f"approved trigger match: {trigger}")
            break

    for tag in skill.tags:
        if contains_phrase(normalized_task, tag):
            score += SKILL_TAG_WEIGHT
            reasons.append(f"skill tag match: {tag}")
            break

    for alias in skill.aliases:
        if contains_phrase(normalized_task, alias):
            score += ALIAS_WEIGHT
            reasons.append(f"skill alias match: {alias}")
            break

    skill_tokens = unique_tokens(" ".join([skill.title, skill.behavior, *skill.tags, *skill.aliases, *skill.triggers]))
    overlap_ratio = lexical_overlap(task_tokens, skill_tokens)
    if overlap_ratio >= 0.2 or len(shared_tokens(task_tokens, skill_tokens)) >= 2:
        score += LEXICAL_OVERLAP_WEIGHT
        reasons.append("lexical overlap")

    if skill.status.casefold() in NEGATIVE_SKILL_STATUSES:
        score -= STALE_STATUS_PENALTY
        reasons.append(f"status penalty: {skill.status}")

    return ScoreBreakdown(score=max(score, 0.0), reason=", ".join(reasons) or "no significant skill signals")


def score_concept(task: str, concept: ConceptMetadata) -> ScoreBreakdown:
    normalized_task = normalize_text(task)
    task_tokens = unique_tokens(task)
    score = 0.0
    reasons: list[str] = []
    normalized_title = normalize_text(concept.title)
    exact_title_match = contains_phrase(normalized_task, concept.title)

    if exact_title_match:
        score += EXACT_CONCEPT_TITLE_WEIGHT
        reasons.append(f"exact concept title match: {concept.title}")

    for tag in concept.tags:
        if exact_title_match and contains_phrase(normalized_title, tag):
            continue
        if contains_phrase(normalized_task, tag):
            score += CONCEPT_TAG_WEIGHT
            reasons.append(f"concept tag match: {tag}")
            break

    for alias in concept.aliases:
        if exact_title_match and contains_phrase(normalized_title, alias):
            continue
        if contains_phrase(normalized_task, alias):
            score += ALIAS_WEIGHT
            reasons.append(f"concept alias match: {alias}")
            break

    concept_tokens = unique_tokens(" ".join([concept.title, concept.summary, *concept.tags, *concept.aliases]))
    overlap_ratio = lexical_overlap(task_tokens, concept_tokens)
    if overlap_ratio >= 0.2 or len(shared_tokens(task_tokens, concept_tokens)) >= 2:
        score += LEXICAL_OVERLAP_WEIGHT
        reasons.append("lexical overlap")

    if concept.status.casefold() in NEGATIVE_CONCEPT_STATUSES:
        score -= STALE_STATUS_PENALTY
        reasons.append(f"status penalty: {concept.status}")

    return ScoreBreakdown(score=max(score, 0.0), reason=", ".join(reasons) or "no significant concept signals")


def _detect_memory_type(title: str | None, snippet: str | None) -> str:
    from oem_knowledge.memory_ranking import classify_memory_type as classify
    return classify("", title, snippet or "")


def score_memory(task: str, memory: MemoryMetadata) -> ScoreBreakdown:
    task_tokens = unique_tokens(task)
    memory_text = " ".join(filter(None, [memory.title, memory.snippet or ""]))
    memory_tokens = unique_tokens(memory_text)
    overlap_ratio = lexical_overlap(task_tokens, memory_tokens)
    if overlap_ratio >= 0.15 or len(shared_tokens(task_tokens, memory_tokens)) >= 2:
        memory_type = _detect_memory_type(memory.title, memory.snippet)
        multiplier = MEMORY_TYPE_WEIGHTS.get(memory_type, 1.0)
        score = MEMORY_HIT_WEIGHT * multiplier
        return ScoreBreakdown(score=score, reason=f"memory {memory_type} hit")
    return ScoreBreakdown(score=0.0, reason="no significant memory signals")


def make_match(
    *,
    kind: str,
    id: str | None,
    title: str,
    score: float,
    reason: str,
    source_path: str | None,
    snippet: str | None,
    metadata: dict | None = None,
) -> PreflightMatch:
    return PreflightMatch(
        kind=kind,
        id=id,
        title=title,
        score=round(score, 3),
        reason=reason,
        source_path=source_path,
        snippet=snippet,
        metadata=metadata or {},
    )
