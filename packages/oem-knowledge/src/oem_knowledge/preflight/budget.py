from __future__ import annotations

from dataclasses import dataclass

from .models import PreflightResult


@dataclass(frozen=True)
class ContextBudget:
    max_context_chars: int = 6000
    max_skills: int = 3
    max_concepts: int = 5
    max_memory_items: int = 5
    max_source_suggestions: int = 5


def _line_for_match(prefix: str, title: str, reason: str, snippet: str | None) -> str:
    text = f"- {title} — {reason}"
    if prefix:
        text = f"- {title} ({prefix}) — {reason}"
    if snippet:
        text += f". {snippet}"
    return text


def render_context(result: PreflightResult, budget: ContextBudget) -> tuple[str, list[str]]:
    warnings = list(result.warnings)
    lines = [
        "# OEM Preflight Context",
        "",
        f"Decision: {result.decision}",
        f"Reason: {result.reason}",
        "",
        f"Project: {result.project_root}",
    ]

    if result.matched_skills:
        lines.extend(["", "Relevant skills:"])
        for match in result.matched_skills[: budget.max_skills]:
            lines.append(_line_for_match("skill", match.title, match.reason, match.snippet))

    if result.matched_concepts:
        lines.extend(["", "Relevant concepts:"])
        for match in result.matched_concepts[: budget.max_concepts]:
            lines.append(_line_for_match("concept", match.title, match.reason, match.snippet))

    if result.matched_memory:
        lines.extend(["", "Relevant memory:"])
        for match in result.matched_memory[: budget.max_memory_items]:
            lines.append(_line_for_match("memory", match.title, match.reason, match.snippet))

    retrieval_lines: list[str] = []
    for match in result.matched_concepts[: budget.max_concepts]:
        retrieval_lines.append(f'- `knowledge_search`: "{match.title}"')
    for match in result.source_suggestions[: budget.max_source_suggestions]:
        retrieval_lines.append(f'- `knowledge_source_search`: "{match.title}"')

    if retrieval_lines:
        lines.extend(["", "Suggested next retrieval:"])
        lines.extend(dict.fromkeys(retrieval_lines))

    context = "\n".join(lines).strip()
    if len(context) <= budget.max_context_chars:
        return context, warnings

    truncated_suffix = "\n\n[context truncated]"
    allowed = max(0, budget.max_context_chars - len(truncated_suffix))
    context = context[:allowed].rstrip() + truncated_suffix
    warnings.append(f"Context truncated to {budget.max_context_chars} characters.")
    return context, warnings

