from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

# Scopes that are not yet fully implemented
_UNIMPLEMENTED_SCOPES = {"recent", "skills", "health"}

_LIGHTWEIGHT_STATUS_CHECKS = [
    ("manifest.json", ".oem/manifest.json"),
    ("events.jsonl", ".oem/events.jsonl"),
    ("concept registry", ".oem/concept_registry.json"),
    ("skills dir", ".oem/skills"),
    ("state dir", ".oem/state"),
]


def _check_oem_paths(proj_path: Path) -> tuple[list[str], list[str]]:
    """Return (status_lines, warning_lines) from lightweight filesystem probes only."""
    status_lines = []
    warning_lines = []
    for label, rel in _LIGHTWEIGHT_STATUS_CHECKS:
        p = proj_path / rel
        if p.exists():
            status_lines.append(f"{label}: readable")
        else:
            status_lines.append(f"{label}: missing")
            warning_lines.append(f"{label} not found at {rel}")
    return status_lines, warning_lines


def _read_recent_sessions(proj_path: Path, limit: int) -> list[str]:
    """Read the tail of outcomes.jsonl — completely read-only."""
    outcomes_candidates = [
        proj_path / ".oem" / "state" / "outcomes.jsonl",
        proj_path / ".oem" / "outcomes.jsonl",
    ]
    for outcomes_file in outcomes_candidates:
        if not outcomes_file.exists():
            continue
        try:
            lines = outcomes_file.read_text(encoding="utf-8").strip().splitlines()
            recent = []
            for raw in lines[-limit:]:
                try:
                    data = json.loads(raw)
                    sid = data.get("session_id") or "unknown"
                    status = data.get("outcome") or data.get("status") or "unknown"
                    ts = data.get("timestamp", "")
                    entry = f"[{ts[:10]}] session={sid[:8]} outcome={status}"
                    goal = data.get("goal_satisfaction")
                    if goal is not None:
                        entry += f" satisfaction={goal:.2f}"
                    recent.append(entry)
                except Exception:
                    continue
            if recent:
                return recent
        except Exception:
            continue
    return []


def execute_knowledge_read(
    eng: "KnowledgeEngine",
    project: str | None = None,
    scope: str = "project",
    limit: int = 10,
) -> dict:
    """Lightweight, read-only project memory baseline retrieval.

    Does NOT:
    - Create or write any files
    - Run LLM inference
    - Run index/reflect/clean/recover
    - Call full doctor
    """
    # --- Unsupported scope guard ---
    if scope in _UNIMPLEMENTED_SCOPES:
        return {
            "status": "not_implemented",
            "operation": "knowledge_read",
            "scope": scope,
            "message": f"scope='{scope}' is not yet implemented. Use scope='project'.",
            "suggestion": "Call knowledge_read(scope='project') for the full baseline.",
        }

    proj_path = Path(project or getattr(eng, "project_path", None) or ".").resolve()

    # --- Missing .oem guard (read-only, never creates anything) ---
    oem_dir = proj_path / ".oem"
    if not oem_dir.exists():
        return {
            "status": "error",
            "operation": "knowledge_read",
            "scope": scope,
            "project": str(proj_path),
            "message": "No OEM project memory found.",
            "suggestion": "Run `oem init` or `oem run opencode --init-if-missing`.",
            "warnings": ["No .oem directory found."],
        }

    # 1. Project identity — load manifest read-only (load_manifest never writes)
    from oem_knowledge.runtime.manifest import load_manifest
    manifest = load_manifest(proj_path)
    project_id = manifest.get("project_id", proj_path.name) if manifest else proj_path.name
    project_section = [f"ID: {project_id}", f"Path: {proj_path}"]

    # 2. Lightweight status checks — pure filesystem probes, no subprocess, no LLM
    runtime_status, path_warnings = _check_oem_paths(proj_path)

    # 3. Recent sessions — tail of outcomes.jsonl, completely read-only
    recent_sessions = _read_recent_sessions(proj_path, limit)
    if not recent_sessions:
        recent_sessions = ["No session history found."]

    # 4. Important concepts — sorted by confidence * evidence_count
    important_concepts: list[str] = []
    registry_warnings: list[str] = []
    try:
        registry = eng.state._load_registry(project)
        sorted_concepts = sorted(
            registry.values(),
            key=lambda c: float(c.get("confidence", 1)) * float(c.get("evidence_count", 0)),
            reverse=True,
        )
        for c in sorted_concepts[:limit]:
            name = c.get("canonical_name") or c.get("concept_id", "?")
            conf = c.get("confidence", "?")
            ev = c.get("evidence_count", 0)
            status_val = c.get("status", "?")
            important_concepts.append(f"{name} (status={status_val}, confidence={conf}/5, evidence={ev})")
    except Exception as exc:
        registry_warnings.append(f"Could not load concept registry: {exc}")

    # 5. Approved skills — read from skills service, bounded by limit
    approved_skills: list[str] = []
    skills_warnings: list[str] = []
    try:
        candidates = eng.skills.list_skill_candidates(project)
        approved = [c for c in candidates if c.status == "approved"]
        for s in approved[:limit]:
            approved_skills.append(f"{s.title}: {s.recommended_behavior}")
    except Exception as exc:
        skills_warnings.append(f"Could not load skills: {exc}")

    # 6. Suggested next searches — deterministic, no LLM
    suggested_searches = [
        'knowledge_search("current task")',
        'knowledge_search("project conventions")',
        'knowledge_search("recent failures")',
        'knowledge_search("active architecture decisions")',
    ]
    if important_concepts:
        top_name = important_concepts[0].split(" (")[0]
        suggested_searches.insert(0, f'knowledge_search("{top_name}")')

    # Aggregate warnings
    all_warnings = path_warnings + registry_warnings + skills_warnings

    return {
        "status": "success",
        "operation": "knowledge_read",
        "scope": scope,
        "project": str(proj_path),
        "message": "OEM project memory baseline loaded.",
        "sections": {
            "project": project_section,
            "runtime_status": runtime_status,
            "recent_sessions": recent_sessions,
            "important_concepts": important_concepts,
            "approved_skills": approved_skills,
            "warnings": all_warnings,
            "suggested_next_searches": suggested_searches[:limit],
        },
        "warnings": all_warnings,
        "suggestion": "Run `oem doctor` for full health diagnostics." if all_warnings else None,
    }
