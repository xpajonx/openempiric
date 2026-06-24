from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .audit import write_audit_event
from .budget import ContextBudget, render_context
from .models import ConceptMetadata, MemoryMetadata, PreflightResult, SkillMetadata
from .scoring import REQUIRED_THRESHOLD, SUGGEST_THRESHOLD, SOURCE_HINT_WEIGHT, make_match, score_concept, score_memory, score_skill
from .triggers import contains_phrase, normalize_text, tokenize, unique_tokens
from ..project_layout import ProjectLayout
from ..server import ProjectMismatchError, ProjectUnresolvedError, resolve_active_project

logger = logging.getLogger(__name__)

OPERATION = "run_preflight"
FRONTMATTER_CHAR_LIMIT = 16000
MEMORY_ROW_LIMIT = 200
PARAGRAPH_CHAR_LIMIT = 220


def _ensure_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
        return tuple(item for item in parts if item)
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return tuple(cleaned)
    return ()


def _read_text_prefix(path: Path, limit: int = FRONTMATTER_CHAR_LIMIT) -> str:
    with path.open("r", encoding="utf-8") as handle:
        return handle.read(limit)


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    if not text.startswith("---"):
        return {}, text, None

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, None

    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return {}, text, "frontmatter block was not closed"

    raw_frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])

    if yaml is not None:
        try:
            loaded = yaml.safe_load(raw_frontmatter) or {}
            if isinstance(loaded, dict):
                return loaded, body, None
            return {}, body, "frontmatter did not parse to a mapping"
        except Exception as exc:
            return {}, body, f"frontmatter parse failed: {exc}"

    metadata: dict[str, Any] = {}
    for line in raw_frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body, None


def _parse_markdown_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = stripped[3:].strip().casefold()
            current_lines = []
            continue
        current_lines.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(current_lines).strip()
    return sections


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _first_paragraph(body: str) -> str | None:
    current: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if not stripped:
            if current:
                break
            continue
        current.append(stripped)
    if not current:
        return None
    paragraph = " ".join(current)
    return paragraph[:PARAGRAPH_CHAR_LIMIT].strip()


def _parse_skill_file(path: Path, default_status: str, warnings: list[str]) -> SkillMetadata | None:
    try:
        text = _read_text_prefix(path)
    except Exception as exc:
        warnings.append(f"Could not read skill metadata from {path}: {exc}")
        return None

    metadata, body, parse_warning = _split_frontmatter(text)
    if parse_warning:
        warnings.append(f"{path}: {parse_warning}")

    sections = _parse_markdown_sections(body)
    title = str(metadata.get("title") or _extract_title(body, path.stem.replace("-", " ").title())).strip()
    if not title:
        warnings.append(f"{path}: missing skill title")
        return None

    status = str(metadata.get("status") or sections.get("status") or default_status).strip() or default_status
    triggers = _ensure_tuple(metadata.get("triggers"))
    if not triggers:
        trigger_section = sections.get("trigger") or sections.get("triggers")
        triggers = _ensure_tuple(trigger_section.splitlines() if trigger_section else ())

    tags = _ensure_tuple(metadata.get("tags"))
    aliases = _ensure_tuple(metadata.get("aliases"))
    behavior = str(
        metadata.get("behavior")
        or metadata.get("recommended_behavior")
        or sections.get("skill")
        or sections.get("recommended behavior")
        or ""
    ).strip()
    snippet = _first_paragraph(body) or behavior[:PARAGRAPH_CHAR_LIMIT] or None

    return SkillMetadata(
        id=str(metadata.get("id") or metadata.get("slug") or path.stem),
        title=title,
        status=status.casefold(),
        source_path=str(path),
        triggers=triggers,
        tags=tags,
        aliases=aliases,
        behavior=behavior,
        snippet=snippet,
    )


def _load_skills(layout: ProjectLayout, include_candidates: bool, warnings: list[str]) -> list[SkillMetadata]:
    skills: list[SkillMetadata] = []

    for path in sorted(layout.skills_dir.glob("*.md")) if layout.skills_dir.exists() else []:
        skill = _parse_skill_file(path, "approved", warnings)
        if skill is not None:
            skills.append(skill)

    if include_candidates and layout.skill_candidates_dir.exists():
        for path in sorted(layout.skill_candidates_dir.glob("*.md")):
            skill = _parse_skill_file(path, "proposed", warnings)
            if skill is not None:
                skills.append(skill)

    return skills


def _load_concept_frontmatter(path: Path, warnings: list[str]) -> tuple[dict[str, Any], str]:
    try:
        text = _read_text_prefix(path)
    except Exception as exc:
        warnings.append(f"Could not read concept metadata from {path}: {exc}")
        return {}, ""

    metadata, body, parse_warning = _split_frontmatter(text)
    if parse_warning:
        warnings.append(f"{path}: {parse_warning}")
    return metadata, body


def _load_concepts(layout: ProjectLayout, warnings: list[str]) -> list[ConceptMetadata]:
    concepts_by_id: dict[str, ConceptMetadata] = {}
    registry_path = layout.registry_path
    registry_data: dict[str, Any] = {}

    if registry_path.exists():
        try:
            raw = json.loads(registry_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                registry_data = raw
        except Exception as exc:
            warnings.append(f"Could not load concept registry: {exc}")

    for concept_id, payload in sorted(registry_data.items()):
        if not isinstance(payload, dict):
            continue
        title = str(payload.get("canonical_name") or concept_id).strip()
        concepts_by_id[concept_id] = ConceptMetadata(
            id=concept_id,
            title=title,
            status=str(payload.get("status") or "validated").casefold(),
            source_path=None,
            tags=_ensure_tuple(payload.get("tags")),
            aliases=_ensure_tuple(payload.get("aliases")),
            summary=str(payload.get("description") or payload.get("summary") or "").strip(),
        )

    if layout.concepts_dir.exists():
        for path in sorted(layout.concepts_dir.glob("concept_*.md")):
            metadata, body = _load_concept_frontmatter(path, warnings)
            concept_id = str(metadata.get("concept_id") or path.stem)
            title = str(
                metadata.get("title")
                or metadata.get("canonical_name")
                or concepts_by_id.get(concept_id, ConceptMetadata(None, path.stem, "validated", None)).title
            ).strip()
            summary = str(metadata.get("description") or metadata.get("summary") or _first_paragraph(body) or "").strip()
            merged = concepts_by_id.get(concept_id)
            concepts_by_id[concept_id] = ConceptMetadata(
                id=concept_id,
                title=title,
                status=str(metadata.get("status") or (merged.status if merged else "validated")).casefold(),
                source_path=str(path),
                tags=_ensure_tuple(metadata.get("tags") or (merged.tags if merged else ())),
                aliases=_ensure_tuple(metadata.get("aliases") or (merged.aliases if merged else ())),
                summary=summary or (merged.summary if merged else ""),
            )

    return [concepts_by_id[key] for key in sorted(concepts_by_id)]


def _extract_memory_title(document: str, metadata: dict[str, Any], fallback: str) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()

    for line in document.splitlines():
        stripped = line.strip()
        if stripped.startswith("Section:"):
            return stripped.removeprefix("Section:").strip()
    return fallback


def _load_memory_matches(task: str, layout: ProjectLayout, warnings: list[str]) -> list[MemoryMetadata]:
    db_path = layout.vector_db_path / "vectors.db"
    if not db_path.exists():
        warnings.append(f"Memory index unavailable at {db_path}.")
        return []

    tokens = [token for token in unique_tokens(task) if len(token) > 1][:8]
    if not tokens:
        return []

    query_parts: list[str] = []
    params: list[str] = []
    for token in tokens:
        query_parts.append("(lower(document) LIKE ? OR lower(metadata) LIKE ?)")
        params.extend([f"%{token}%", f"%{token}%"])

    sql = (
        "SELECT id, document, metadata FROM chunks "
        f"WHERE {' OR '.join(query_parts)} "
        "ORDER BY id ASC LIMIT ?"
    )
    params.append(str(MEMORY_ROW_LIMIT))

    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = 1")
        rows = connection.execute(sql, params[:-1] + [MEMORY_ROW_LIMIT]).fetchall()
        connection.close()
    except Exception as exc:
        warnings.append(f"Memory index could not be opened read-only: {exc}")
        return []

    items: list[MemoryMetadata] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except Exception:
            metadata = {}
        title = _extract_memory_title(row["document"], metadata, row["id"])
        snippet = row["document"].split("\n\n", 1)[-1][:PARAGRAPH_CHAR_LIMIT].strip() if row["document"] else None
        items.append(
            MemoryMetadata(
                id=row["id"],
                title=title,
                source_path=str(metadata.get("source")) if metadata.get("source") else None,
                snippet=snippet,
            )
        )
    return items


def _build_source_suggestions(
    task: str,
    layout: ProjectLayout,
    matched_skills: list,
    matched_concepts: list,
) -> list:
    source_available = layout.source_manifest_path.exists() or layout.source_index_db_path.exists()
    if not source_available:
        return []

    suggestions: list = []
    seen_titles: set[str] = set()

    for match in matched_skills + matched_concepts:
        title = f"Search source corpus for {match.title}"
        if title in seen_titles:
            continue
        seen_titles.add(title)
        suggestions.append(
            make_match(
                kind="source_suggestion",
                id=None,
                title=title,
                score=SOURCE_HINT_WEIGHT,
                reason=f"source corpus available; follow up on {match.title}",
                source_path=str(layout.source_index_db_path if layout.source_index_db_path.exists() else layout.source_manifest_path),
                snippet=None,
            )
        )

    if not suggestions:
        task_hint = " ".join(tokenize(task)[:4]) or "current task"
        suggestions.append(
            make_match(
                kind="source_suggestion",
                id=None,
                title=f"Search source corpus for {task_hint}",
                score=SOURCE_HINT_WEIGHT,
                reason="source corpus available",
                source_path=str(layout.source_index_db_path if layout.source_index_db_path.exists() else layout.source_manifest_path),
                snippet=None,
            )
        )

    return suggestions


def _sort_matches(matches: list) -> list:
    return sorted(
        matches,
        key=lambda match: (
            -match.score,
            normalize_text(match.title),
            normalize_text(match.id or ""),
            normalize_text(match.source_path or ""),
        ),
    )


def _resolve_project(project: str | None, session_id: str) -> tuple[Path, ProjectLayout]:
    project_root = resolve_active_project(project_arg=project or "", session_id=session_id)
    memory_root = project_root / ".oem"
    if not memory_root.is_dir():
        raise FileNotFoundError(str(memory_root))
    return project_root, ProjectLayout(memory_root)


def _blocked_result(task: str, reason: str, project_root: str = "", memory_root: str = "") -> PreflightResult:
    return PreflightResult(
        status="blocked",
        operation=OPERATION,
        project_root=project_root,
        memory_root=memory_root,
        task=task,
        decision="blocked",
        reason=reason,
    )


def run_preflight(
    task: str,
    project: str | None = None,
    *,
    session_id: str = "",
    include_candidates: bool = False,
    write_audit: bool = True,
    budget: ContextBudget | None = None,
) -> PreflightResult:
    budget = budget or ContextBudget()
    warnings: list[str] = []

    try:
        project_root, layout = _resolve_project(project, session_id)
    except ProjectMismatchError as exc:
        return _blocked_result(task, f"project mismatch: {exc}", memory_root=str(Path(exc.resolved_project) / '.oem'), project_root=exc.resolved_project)
    except ProjectUnresolvedError as exc:
        return _blocked_result(task, f"project unresolved: {exc}")
    except FileNotFoundError as exc:
        missing_root = Path(str(exc))
        return _blocked_result(task, f".oem missing at {missing_root}", project_root=str(missing_root.parent), memory_root=str(missing_root))
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected preflight project-resolution failure")
        return PreflightResult(
            status="error",
            operation=OPERATION,
            project_root="",
            memory_root="",
            task=task,
            decision="error",
            reason=f"unexpected project-resolution error: {exc}",
            warnings=[str(exc)],
        )

    try:
        skills = _load_skills(layout, include_candidates, warnings)
        concepts = _load_concepts(layout, warnings)
        memory_items = _load_memory_matches(task, layout, warnings)

        matched_skills = []
        for skill in skills:
            if skill.status in {"rejected", "deprecated", "superseded", "stale"}:
                continue
            breakdown = score_skill(task, skill)
            if breakdown.score < SUGGEST_THRESHOLD:
                continue
            matched_skills.append(
                make_match(
                    kind="skill",
                    id=skill.id,
                    title=skill.title,
                    score=breakdown.score,
                    reason=breakdown.reason,
                    source_path=skill.source_path,
                    snippet=skill.snippet,
                )
            )

        matched_concepts = []
        for concept in concepts:
            breakdown = score_concept(task, concept)
            if breakdown.score < SUGGEST_THRESHOLD:
                continue
            matched_concepts.append(
                make_match(
                    kind="concept",
                    id=concept.id,
                    title=concept.title,
                    score=breakdown.score,
                    reason=breakdown.reason,
                    source_path=concept.source_path,
                    snippet=concept.summary[:PARAGRAPH_CHAR_LIMIT] if concept.summary else None,
                )
            )

        matched_memory = []
        for memory in memory_items:
            breakdown = score_memory(task, memory)
            if breakdown.score <= 0:
                continue
            matched_memory.append(
                make_match(
                    kind="memory",
                    id=memory.id,
                    title=memory.title,
                    score=breakdown.score,
                    reason=breakdown.reason,
                    source_path=memory.source_path,
                    snippet=memory.snippet,
                )
            )

        matched_skills = _sort_matches(matched_skills)
        matched_concepts = _sort_matches(matched_concepts)
        matched_memory = _sort_matches(matched_memory)
        source_suggestions = _sort_matches(_build_source_suggestions(task, layout, matched_skills, matched_concepts))

        decision = "noop"
        reason = "no strong OEM preflight signals"
        top_match = None
        for candidate in matched_skills + matched_concepts:
            if top_match is None or candidate.score > top_match.score:
                top_match = candidate

        if top_match is not None and top_match.score >= REQUIRED_THRESHOLD:
            decision = "required"
            reason = f"{top_match.kind} matched strongly: {top_match.title}"
        elif top_match is not None and top_match.score >= SUGGEST_THRESHOLD:
            decision = "suggest"
            reason = f"{top_match.kind} matched: {top_match.title}"

        result = PreflightResult(
            status=decision,
            operation=OPERATION,
            project_root=str(project_root),
            memory_root=str(layout.root),
            task=task,
            decision=decision,
            reason=reason,
            matched_skills=matched_skills,
            matched_concepts=matched_concepts,
            matched_memory=matched_memory,
            source_suggestions=source_suggestions,
            context="",
            warnings=warnings,
        )

        context, context_warnings = render_context(result, budget)
        result = PreflightResult(
            **{
                **result.__dict__,
                "context": context,
                "warnings": context_warnings,
            }
        )

        if write_audit:
            try:
                write_audit_event(layout, result)
            except Exception as exc:
                result = PreflightResult(
                    **{
                        **result.__dict__,
                        "warnings": result.warnings + [f"Preflight audit write failed: {exc}"],
                    }
                )

        return result
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("Unexpected preflight failure")
        return PreflightResult(
            status="error",
            operation=OPERATION,
            project_root=str(project_root),
            memory_root=str(layout.root),
            task=task,
            decision="error",
            reason=f"unexpected preflight error: {exc}",
            warnings=warnings + [str(exc)],
        )
