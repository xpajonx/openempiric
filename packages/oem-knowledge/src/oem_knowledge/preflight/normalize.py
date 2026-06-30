from __future__ import annotations

from typing import Any

from .budget import ContextBudget
from .models import PreflightMatch, PreflightResult

DEFAULT_LIMIT = 8
MIN_LIMIT = 1
MAX_LIMIT = 20


def clamp_preflight_limit(limit: int | None) -> tuple[int, list[str]]:
    if limit is None:
        return DEFAULT_LIMIT, []

    warnings: list[str] = []
    clamped = limit

    if limit < MIN_LIMIT:
        clamped = MIN_LIMIT
        warnings.append(
            f"Preflight limit {limit} is below the minimum of {MIN_LIMIT}; clamped to {MIN_LIMIT}."
        )
    elif limit > MAX_LIMIT:
        clamped = MAX_LIMIT
        warnings.append(
            f"Preflight limit {limit} exceeds the maximum of {MAX_LIMIT}; clamped to {MAX_LIMIT}."
        )

    return clamped, warnings


def make_preflight_budget(limit: int | None) -> tuple[ContextBudget, list[str], int]:
    clamped_limit, warnings = clamp_preflight_limit(limit)
    return (
        ContextBudget(
            max_skills=clamped_limit,
            max_concepts=clamped_limit,
            max_memory_items=clamped_limit,
            max_source_suggestions=clamped_limit,
        ),
        warnings,
        clamped_limit,
    )


def _serialize_match(match: PreflightMatch) -> dict[str, Any]:
    return {
        "kind": match.kind,
        "id": match.id,
        "title": match.title,
        "score": match.score,
        "reason": match.reason,
        "source_path": match.source_path,
        "snippet": match.snippet,
    }


def _reason_code(result: PreflightResult) -> tuple[str, str | None]:
    reason = (result.reason or "").casefold()

    if result.decision == "blocked":
        if reason.startswith("project unresolved:"):
            return "project_unresolved", "Pass project explicitly or start a session from a directory containing .oem."
        if reason.startswith("project mismatch:"):
            return "project_mismatch", "Verify you are in the correct workspace directory or pass the project explicitly."
        if reason.startswith(".oem missing"):
            return "oem_missing", "Initialize OEM in the project or pass a directory containing .oem."
        return "preflight_blocked", None

    if result.decision == "error":
        return "preflight_error", "Inspect warnings and retry. If the issue persists, treat it as an OEM bug."

    if result.decision == "required":
        if result.matched_skills:
            return "approved_skill_match", None
        if result.matched_concepts:
            return "concept_match", None
        if result.matched_memory:
            return "memory_match", None
        return "preflight_required", None

    if result.decision == "suggest":
        if result.matched_skills:
            return "skill_match", None
        if result.matched_concepts:
            return "concept_match", None
        if result.matched_memory:
            return "memory_match", None
        return "preflight_suggest", None

    return "no_relevant_oem_context", None


def normalize_preflight_result(
    result: PreflightResult,
    *,
    operation: str = "knowledge_preflight",
    limit: int | None = None,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    clamped_limit, limit_warnings = clamp_preflight_limit(limit)
    reason_code, suggestion = _reason_code(result)
    warnings = list(result.warnings)
    warnings.extend(limit_warnings)
    warnings.extend(extra_warnings or [])

    payload: dict[str, Any] = {
        "status": "success" if result.decision in {"noop", "suggest", "required"} else "error",
        "operation": operation,
        "project_root": result.project_root,
        "memory_root": result.memory_root,
        "decision": result.decision,
        "reason": reason_code,
        "reason_detail": result.reason_detail or result.reason,
        "matched_skills": [_serialize_match(match) for match in result.matched_skills[:clamped_limit]],
        "matched_concepts": [_serialize_match(match) for match in result.matched_concepts[:clamped_limit]],
        "matched_memory": [_serialize_match(match) for match in result.matched_memory[:clamped_limit]],
        "source_suggestions": [_serialize_match(match) for match in result.source_suggestions[:clamped_limit]],
        "matched_directives": result.matched_directives,
        "selected_workflow": result.selected_workflow,
        "context": result.context,
        "warnings": warnings,
    }

    if result.active_project:
        payload["active_project"] = result.active_project
    else:
        payload["active_project"] = None

    if result.matched_memory_summary:
        payload["matched_memory_summary"] = result.matched_memory_summary
    else:
        payload["matched_memory_summary"] = []

    if result.supporting_reasons:
        payload["supporting_reasons"] = result.supporting_reasons
    else:
        payload["supporting_reasons"] = []

    if suggestion:
        payload["suggestion"] = suggestion

    return payload
