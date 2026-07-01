from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..markdown.frontmatter import parse_frontmatter

from .audit import write_audit_event
from .budget import ContextBudget, render_context
from .models import ConceptMetadata, MemoryMetadata, PreflightResult, SkillMetadata
from .scoring import REQUIRED_THRESHOLD, SUGGEST_THRESHOLD, SOURCE_HINT_WEIGHT, make_match, score_concept, score_memory, score_skill
from .triggers import contains_phrase, normalize_text, tokenize, unique_tokens
from ..project_layout import ProjectLayout
from ..project import ProjectMismatchError, ProjectUnresolvedError, resolve_active_project
from ..runtime.active_work import (
    is_continuation_prompt,
    resolve_active_work,
    resolve_active_work_identity,
    resolve_active_project as resolve_active_project_identity,
    ActiveWorkResult,
)

logger = logging.getLogger(__name__)

OPERATION = "run_preflight"
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


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str, str | None]:
    """Legacy adapter that delegates to the canonical frontmatter parser."""
    parsed = parse_frontmatter(text)
    warning_str = None
    if parsed.warnings:
        w = parsed.warnings[0]
        parts = [p for p in (w.get("path"), w.get("reason")) if p]
        warning_str = ": ".join(parts) if parts else None
    return parsed.metadata, parsed.body, warning_str


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
        text = path.read_text(encoding="utf-8")
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
        text = path.read_text(encoding="utf-8")
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


# ---------------------------------------------------------------------------
# Target extraction from task text
# ---------------------------------------------------------------------------

_TASK_PATH_RE = re.compile(
    r"\b(?:\.\/|~\/)?[a-zA-Z0-9_\-\.\/]+\.(?:md|json|py|ts|js|yaml|yml|toml|txt)\b"
)
_TASK_IDENTIFIER_RE = re.compile(r"\b([A-Z][a-z]+_[A-Z][a-z]+(?:_[a-zA-Z0-9]+)*|[a-z]+(?:_[a-z]+)+)\b")
_GENERIC_CONTINUATION_TRIGGERS = frozenset({
    "current project", "this project", "the current project",
    "what should i work on", "what is the current",
})


def _extract_task_targets(task: str) -> dict:
    task_lower = task.lower().strip()

    raw_files: list[str] = _TASK_PATH_RE.findall(task)
    files: list[str] = [f for f in raw_files if f]

    raw_identifiers: list[str] = _TASK_IDENTIFIER_RE.findall(task)
    identifiers: list[str] = [i for i in raw_identifiers if len(i) >= 3]

    stems: list[str] = []
    for f in files:
        clean = f.lstrip("./~")
        parts = clean.rsplit(".", 1)
        stems.append(clean)
        if parts[0]:
            stems.append(parts[0])
        stem_parts = parts[0].split("/")
        if stem_parts:
            stems.append(stem_parts[-1])

    # Normalize: strip common prefixes for cross-matching
    stems_norm: set[str] = set()
    for s in stems:
        # /path/to/Essay_ID.md → Essay_ID
        # Essay_ID.md → Essay_ID
        # 2_Essay/expertise-debt/Essay_ID.md → expertise-debt, Essay_ID
        for part in s.replace("/", " ").replace("_", " ").replace("-", " ").split():
            cleaned = part.strip("._-").lower()
            if len(cleaned) >= 3:
                stems_norm.add(cleaned)
    # Also keep the original stems
    stems_norm.update(s.lower() for s in stems)

    return {
        "files": files,
        "identifiers": [i.lower() for i in identifiers],
        "stems": sorted(stems_norm),
        "has_continuation_phrase": any(
            phrase in task_lower for phrase in _GENERIC_CONTINUATION_TRIGGERS
        ),
    }


# ---------------------------------------------------------------------------
# Memory relevance classification
# ---------------------------------------------------------------------------

_DECISION_TYPES = frozenset({"decision", "failure"})
_OPEN_PROJECT_SIGNALS = (
    "is the open project",
    "open project",
    "current project",
    "active project",
)


def _detect_memory_chunk_type(memory) -> str:
    """Return the classified chunk type (decision/failure/outcome/observation)."""
    from .scoring import _detect_memory_type  # defer import

    return _detect_memory_type(memory.title, memory.snippet)


def _classify_memory_relevance(
    memory, task_targets: dict, task_lower: str
) -> tuple[str, list[str]]:
    """Returns (relevance_level, signals).

    relevance_level is "strong", "medium", or "weak".
    """
    chunk_type = _detect_memory_chunk_type(memory)
    title_lower = (memory.title or "").lower()
    snippet_lower = (memory.snippet or "").lower()
    combined = title_lower + " " + snippet_lower

    signals: list[str] = []

    # Check file/stem/identifier overlap
    target_hit = False
    for stem in task_targets["stems"]:
        if stem in combined:
            target_hit = True
            signals.append(f"stem_match:{stem}")
            break
    if not target_hit:
        for fid in task_targets["identifiers"]:
            if fid in combined:
                target_hit = True
                signals.append(f"id_match:{fid}")
                break
    if not target_hit:
        for f in task_targets["files"]:
            if f.lower() in combined:
                target_hit = True
                signals.append(f"file_match:{f}")
                break

    is_decision_type = chunk_type in _DECISION_TYPES
    has_open_project = any(sig in combined for sig in _OPEN_PROJECT_SIGNALS)
    is_continuation = task_targets.get("has_continuation_phrase", False)

    # Strong: Decision/Failure + target match, or open project + continuation
    if is_decision_type and target_hit:
        signals.append(f"type:{chunk_type}")
        return "strong", signals
    if has_open_project and is_continuation:
        signals.append("open_project")
        return "strong", signals

    # Medium: Decision/Failure without file match, or target hit without Decision type
    if is_decision_type:
        signals.append(f"type:{chunk_type}")
        return "medium", signals
    if target_hit:
        return "medium", signals

    return "weak", signals


def _evaluate_matched_memory_relevance(
    matched_memory: list, task_targets: dict, task: str
) -> dict:
    task_lower = task.lower()
    strong: list[dict] = []
    medium: list[dict] = []
    weak: list[dict] = []
    has_open_project_signal = False

    for mem in matched_memory:
        level, signals = _classify_memory_relevance(mem, task_targets, task_lower)
        detail = {
            "title": mem.title,
            "type": _detect_memory_chunk_type(mem),
            "relevance": level,
            "signals": signals,
        }
        if level == "strong":
            strong.append(detail)
        elif level == "medium":
            medium.append(detail)
        else:
            weak.append(detail)

        if not has_open_project_signal:
            has_open_project_signal = any(
                sig in (mem.title or "").lower() + " " + (mem.snippet or "").lower()
                for sig in _OPEN_PROJECT_SIGNALS
            )

    max_relevance = "strong" if strong else ("medium" if medium else "weak")
    return {
        "max_relevance": max_relevance,
        "strong_count": len(strong),
        "medium_count": len(medium),
        "details": strong + medium + weak,
        "has_open_project_signal": has_open_project_signal,
    }


def is_generic_continuation(task: str) -> bool:
    """Returns True for continuation prompts that should route through
    active-project resolution before directive matching.

    This covers prompts that explicitly reference 'current project' /
    'this project' — not every continuation phrase.
    """
    task_lower = task.strip().casefold()
    for phrase in _GENERIC_CONTINUATION_TRIGGERS:
        if phrase in task_lower:
            return True
    return False


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

        # Directives and workflows matching
        import hashlib
        matched_directives = []
        selected_workflow = None
        try:
            from oem_knowledge.instructions import (
                get_db_connection,
                get_active_directives,
                match_directives,
                resolve_selected_workflow
            )
            if layout.instruction_ledger_path.exists():
                conn = get_db_connection(layout.instruction_ledger_path)
                active_dirs = get_active_directives(conn)
                matched_directives = match_directives(task, active_dirs, matched_skills, matched_concepts)
                selected_workflow = resolve_selected_workflow(task, matched_directives)
                
                # Record match history
                from datetime import datetime
                now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                task_hash = hashlib.sha256(task.encode("utf-8")).hexdigest()[:12]
                for md in matched_directives:
                    conn.execute("""
                        INSERT OR REPLACE INTO session_directive_matches (
                            session_id, directive_id, task_hash, match_score, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id or "default_session", md["id"], task_hash, md["score"], md["reason"], now_str))
                conn.close()
        except Exception as e:
            warnings.append(f"Failed to match directives: {e}")

        # Extract task targets for relevance classification
        task_targets = _extract_task_targets(task)
        memory_relevance = _evaluate_matched_memory_relevance(
            matched_memory, task_targets, task
        )

        decision = "noop"
        reason = "no strong OEM preflight signals"
        reason_detail = ""
        supporting_reasons: list[str] = []
        active_project_data: dict | None = None
        active_work_data: dict | None = None

        top_match = None
        for candidate in matched_skills + matched_concepts + matched_memory:
            if top_match is None or candidate.score > top_match.score:
                top_match = candidate

        forcing_directives = [md for md in matched_directives if md.get("can_force_required")]
        top_forcing_directive = max(forcing_directives, key=lambda md: md.get("score", 0.0), default=None)
        top_semantic_directive = max(
            (md for md in matched_directives if md.get("match_class") == "semantic_directive_match"),
            key=lambda md: md.get("score", 0.0),
            default=None,
        )

        # Generic continuation: resolve active work (field-aware)
        is_generic = is_generic_continuation(task)
        if is_generic:
            try:
                ident = resolve_active_work_identity(layout.root)
                # Legacy shaped data for active_project (for backward compat)
                legacy = ident.to_legacy_active_project_result()
                active_project_data = {
                    "latest_project": legacy.latest_project,
                    "selected_source": legacy.selected_source,
                    "active_projects_by_source": dict(legacy.active_projects_by_source),
                    "conflicts": [
                        {"type": c.type, "sources": c.sources, "severity": c.severity}
                        for c in legacy.conflicts
                    ],
                    "warnings": list(legacy.warnings),
                }
                active_work_data = ident.to_dict()

                if ident.active_work_item or ident.active_topic:
                    decision = "suggest"
                    reason = "active_work_resolved"
                    val = ident.active_work_item or ident.active_topic
                    reason_detail = f"Active work resolved to {val}"
                else:
                    decision = "suggest"
                    reason = "workspace_resolved_active_work_unknown"
                    reason_detail = (
                        "Generic continuation detected but no active_work_item or active_topic "
                        "found (workspace root resolved)."
                    )
                    warnings.append("workspace_resolved_active_work_unknown; inspect memory or ask user.")

                # Surface field-specific conflicts
                for c in ident.conflicts:
                    warnings.append(
                        f"Active work conflict ({c.severity}): field={c.semantic_field} sources={', '.join(c.sources)}"
                    )

                # Escalate on field conflicts
                for c in ident.conflicts:
                    if c.severity == "error":
                        decision = "required"
                        reason = "active_work_conflict"
                        reason_detail = f"Generic continuation with active-work conflict on {c.semantic_field}"
                        break
                    elif c.severity == "warning" and decision != "required":
                        decision = "suggest"
                        reason = "active_work_conflict"
                        reason_detail = f"Active-work field signals differ on {c.semantic_field}"

                # Append relevant directives as supporting context
                for md in matched_directives:
                    if md.get("match_class") in {"generic_lexical_match", "weak_directive_match"}:
                        continue
                    supporting_reasons.append(f"directive:{md['title']}")
            except Exception as e:
                logger.warning("Active-work resolution failed: %s", e)

        # ---- Decision cascade (posts for generic continuation) ----
        # Steps below only fire if generic continuation didn't already decide.
        # For generic continuation, finished above.

        # Step: strong Decision/Failure target match → required
        if (
            decision == "noop"
            and memory_relevance.get("max_relevance") == "strong"
            and not is_generic
        ):
            strong_details = [
                d for d in memory_relevance["details"] if d["relevance"] == "strong"
            ]
            decision = "required"
            reason = "target_file_decision_matched"
            reason_detail = (
                f"Matched memory contains {memory_relevance['strong_count']} strong "
                f"Decision/Failure chunk(s) tied to task target."
            )
            for d in strong_details:
                supporting_reasons.append(f"strong_memory:{d['type']}:{d['title'][:60]}")

        # top_match ≥ 8.0 → required
        if decision == "noop" and top_match is not None and top_match.score >= REQUIRED_THRESHOLD:
            decision = "required"
            reason = f"{top_match.kind} matched strongly: {top_match.title}"

        # Semantically-relevant critical/global directive → required.
        # Generic diagnostic matches never force required.
        if decision == "noop" and top_forcing_directive is not None:
            decision = "required"
            reason = f"critical directive matched: {top_forcing_directive['title']}"

        # active_work.has_active_work → required
        active_work: ActiveWorkResult | None = None
        if is_continuation_prompt(task):
            active_work = resolve_active_work(layout.root)
            if active_work.contradictions:
                warnings.extend(
                    f"Active-work contradiction: {c}"
                    for c in active_work.contradictions
                )
        if decision == "noop" and active_work is not None and active_work.has_active_work:
            decision = "required"
            n_items = len(active_work.items)
            reason = f"active work detected ({n_items} item{'s' if n_items != 1 else ''})"
            if active_work.contradictions:
                reason += "; state surfaces conflict"

        # top_match ≥ 4.0 → suggest
        if decision == "noop" and top_match is not None and top_match.score >= SUGGEST_THRESHOLD:
            decision = "suggest"
            reason = f"{top_match.kind} matched: {top_match.title}"

        # medium memory relevance → suggest
        if (
            decision == "noop"
            and memory_relevance.get("max_relevance") == "medium"
            and not is_generic
        ):
            decision = "suggest"
            reason = "relevant_memory_matched"
            reason_detail = (
                f"Matched memory contains {memory_relevance['medium_count']} medium-relevance "
                f"chunk(s) related to task domain."
            )

        # directive score ≥ 4.0, semantic → suggest
        if (
            decision == "noop"
            and top_semantic_directive is not None
            and top_semantic_directive.get("score", 0.0) >= SUGGEST_THRESHOLD
        ):
            decision = "suggest"
            reason = f"directive matched: {top_semantic_directive['title']}"

        # active_work.score ≥ 2.0 → suggest
        if decision == "noop" and active_work is not None and active_work.score >= 2.0:
            decision = "suggest"
            reason = f"active work signals detected (score {active_work.score:.1f})"

        # aggregate memory → suggest
        if decision == "noop" and len(matched_memory) >= 3:
            top_scores = sorted((m.score for m in matched_memory), reverse=True)[:5]
            if sum(top_scores) >= 8.0:
                decision = "suggest"
                reason = f"multiple memory signals detected ({len(matched_memory)} hits, aggregate {sum(top_scores):.1f})"

        # Active-project conflict escalation — only if no higher-priority decision
        if decision == "noop":
            try:
                proj = resolve_active_project_identity(layout.root)
                if active_project_data is None:
                    active_project_data = {
                        "latest_project": proj.latest_project,
                        "selected_source": proj.selected_source,
                        "active_projects_by_source": dict(
                            proj.active_projects_by_source
                        ),
                        "conflicts": [
                            {
                                "type": c.type,
                                "sources": c.sources,
                                "severity": c.severity,
                            }
                            for c in proj.conflicts
                        ],
                        "warnings": list(proj.warnings),
                    }
                for c in proj.conflicts:
                    unique_high = set(
                        s.project for s in proj.sources
                        if s.confidence == "high" and s.project is not None
                    )
                    if c.severity == "error" and len(unique_high) >= 3:
                        decision = "required"
                        reason = f"active project conflict (3-way): {', '.join(c.sources)}"
                        break
                    elif c.severity == "warning" and len(unique_high) >= 2:
                        decision = "suggest"
                        reason = f"active project conflict (2-way): {', '.join(c.sources)}"
                        break
                    else:
                        decision = "suggest"
                        reason = f"active project signals differ: {', '.join(c.sources)}"
                        break
                for c in proj.conflicts:
                    warnings.append(
                        f"Active project conflict ({c.severity}): "
                        f"{', '.join(c.sources)}"
                    )
            except Exception as e:
                logger.warning("Project conflict check failed: %s", e)

        # Build matched_memory_summary
        matched_memory_summary = memory_relevance.get("details", [])

        if active_work_data is None:
            active_work_data = None

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
            matched_directives=matched_directives,
            selected_workflow=selected_workflow,
            context="",
            warnings=warnings,
            active_project=active_project_data,
            active_work=active_work_data,
            matched_memory_summary=matched_memory_summary,
            reason_detail=reason_detail,
            supporting_reasons=supporting_reasons,
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
