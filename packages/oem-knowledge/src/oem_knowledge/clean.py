from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

CleanScope = Literal["self-ingestion", "duplicates", "structure", "all"]
CleanStatus = Literal["clean", "issues_found", "repaired", "repaired_partial", "error"]
CleanMode = Literal["dry_run", "apply"]

_ALLOWED_SCOPES = {"self-ingestion", "duplicates", "structure", "all"}
_EXPECTED_OEM_DIRS = ("wiki", "sessions", "state", "graph", "skills")
_OEM_SOURCE_MARKERS = (
    ".oem/wiki",
    ".oem\\wiki",
    "runtime_events.jsonl",
    ".oem/sessions",
    ".oem\\sessions",
    "session_report",
    "session-report",
    "session reports",
)
_FORBIDDEN_NAMES = {
    "opencode.jsonc",
    "config.toml",
    "config.json",
}


def _empty_report(project: Path, scope: str, mode: CleanMode = "dry_run") -> dict[str, Any]:
    return {
        "status": "clean",
        "mode": mode,
        "scope": scope,
        "project": str(project),
        "self_ingestion": {
            "suspect_events": 0,
            "suspect_concepts": 0,
        },
        "duplicates": {
            "duplicate_runtime_events": 0,
        },
        "structure": {
            "orphan_wiki_files": 0,
            "missing_wiki_files": 0,
            "duplicate_slugs": 0,
        },
        "changed_files": [],
        "backup_dir": None,
        "report_path": None,
        "warnings": [],
    }


def _resolve_project(project: str | Path | None) -> Path:
    return Path(project or ".").expanduser().resolve()


def _oem_dir(project: Path) -> Path:
    return project / ".oem"


def _events_path(project: Path) -> Path:
    return _oem_dir(project) / "events.jsonl"


def _registry_path(project: Path) -> Path:
    return _oem_dir(project) / "concept_registry.json"


def _wiki_dir(project: Path) -> Path:
    return _oem_dir(project) / "wiki"


def _selected(scope: str, name: str) -> bool:
    return scope == "all" or scope == name


def _read_jsonl(path: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                report["warnings"].append(f"Could not parse {path.name}:{line_no}: {exc}")
                continue
            if isinstance(item, dict):
                events.append(item)
    except OSError as exc:
        report["warnings"].append(f"Could not read {path}: {exc}")
    return events


def _read_registry(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report["warnings"].append(f"Could not read concept registry: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _event_duplicate_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(event.get("timestamp", "")),
        str(event.get("event_type", "")),
        json.dumps(event.get("concept_candidates", []), sort_keys=True),
        str(event.get("evidence", "")),
        str(event.get("source", "")),
    )


def _is_oem_generated_source(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_is_oem_generated_source(item) for item in value)
    if isinstance(value, dict):
        return any(_is_oem_generated_source(item) for item in value.values())
    text = str(value).lower()
    return any(marker in text for marker in _OEM_SOURCE_MARKERS)


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _update_status(report: dict[str, Any]) -> None:
    if report["warnings"] and any(str(w).lower().startswith("error") for w in report["warnings"]):
        report["status"] = "error"
        return

    issue_count = (
        report["self_ingestion"]["suspect_events"]
        + report["self_ingestion"]["suspect_concepts"]
        + report["duplicates"]["duplicate_runtime_events"]
        + report["structure"]["orphan_wiki_files"]
        + report["structure"]["missing_wiki_files"]
        + report["structure"]["duplicate_slugs"]
        + sum(
            1
            for warning in report["warnings"]
            if str(warning).startswith("Missing expected .oem dirs:")
        )
    )
    if report["changed_files"]:
        report["status"] = "repaired_partial" if issue_count else "repaired"
    else:
        report["status"] = "issues_found" if issue_count else "clean"


def analyze_cleanliness(project: str | Path | None, scope: CleanScope = "all") -> dict[str, Any]:
    """Analyze OEM cleanliness without mutating files.

    The returned plain-dict shape is intentionally stable for CLI, MCP, and
    future agent workflows.
    """
    if scope not in _ALLOWED_SCOPES:
        raise ValueError(f"Invalid clean scope: {scope}")

    resolved_project = _resolve_project(project)
    report = _empty_report(resolved_project, scope, mode="dry_run")
    oem_root = _oem_dir(resolved_project)

    events: list[dict[str, Any]] = []
    if _selected(scope, "self-ingestion") or _selected(scope, "duplicates"):
        events = _read_jsonl(_events_path(resolved_project), report)

    if _selected(scope, "self-ingestion"):
        report["self_ingestion"]["suspect_events"] = sum(
            1
            for event in events
            if _is_oem_generated_source(event.get("source"))
            or _is_oem_generated_source(event.get("evidence"))
        )
        registry = _read_registry(_registry_path(resolved_project), report)
        suspect_concepts = 0
        for concept in registry.values():
            if not isinstance(concept, dict):
                continue
            source_fields = (
                concept.get("source"),
                concept.get("sources"),
                concept.get("source_path"),
                concept.get("source_paths"),
                concept.get("evidence"),
            )
            if any(_is_oem_generated_source(field) for field in source_fields):
                suspect_concepts += 1
        report["self_ingestion"]["suspect_concepts"] = suspect_concepts

    if _selected(scope, "duplicates"):
        seen: set[tuple[str, str, str, str, str]] = set()
        duplicate_count = 0
        for event in events:
            key = _event_duplicate_key(event)
            if key in seen:
                duplicate_count += 1
            else:
                seen.add(key)
        report["duplicates"]["duplicate_runtime_events"] = duplicate_count

    if _selected(scope, "structure"):
        registry = _read_registry(_registry_path(resolved_project), report)
        wiki_dir = _wiki_dir(resolved_project)
        registry_ids = {str(cid) for cid in registry.keys()}
        wiki_ids = {path.stem for path in wiki_dir.glob("*.md")} if wiki_dir.exists() else set()
        reserved_wiki = {"index", "inbox", "log"}
        concept_wiki_ids = wiki_ids - reserved_wiki

        report["structure"]["orphan_wiki_files"] = len(concept_wiki_ids - registry_ids)
        report["structure"]["missing_wiki_files"] = len(registry_ids - concept_wiki_ids)

        slug_to_ids: dict[str, list[str]] = defaultdict(list)
        for cid, concept in registry.items():
            if not isinstance(concept, dict):
                continue
            name = concept.get("canonical_name") or cid
            slug_to_ids[_slug(str(name))].append(str(cid))
        report["structure"]["duplicate_slugs"] = sum(1 for ids in slug_to_ids.values() if len(ids) > 1)

        missing_dirs = [dirname for dirname in _EXPECTED_OEM_DIRS if not (oem_root / dirname).is_dir()]
        if missing_dirs:
            report["warnings"].append(f"Missing expected .oem dirs: {', '.join(missing_dirs)}")

    _update_status(report)
    return report


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_forbidden_mutation_path(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if _is_relative_to(resolved, home / ".config" / "opencode"):
        return True
    if _is_relative_to(resolved, home / ".codex"):
        return True
    if resolved.name == "opencode.jsonc":
        return True
    parts = {part.lower() for part in resolved.parts}
    if ".codex" in parts:
        return True
    if resolved.name.lower() in _FORBIDDEN_NAMES and ("codex" in parts or ".codex" in parts):
        return True
    if ("opencode" in parts or ".opencode" in parts) and ("plugins" in parts or "plugin" in parts):
        return True
    return False


def _project_allows_oem_mutation(project: Path) -> bool:
    resolved = project.expanduser().resolve()
    home = Path.home().resolve()
    if _is_relative_to(resolved, home / ".config" / "opencode"):
        return False
    if _is_relative_to(resolved, home / ".codex"):
        return False
    return True


def _safe_to_mutate(path: Path, project: Path) -> bool:
    resolved = path.expanduser().resolve()
    if _is_forbidden_mutation_path(resolved):
        return False
    oem_root = (project / ".oem").resolve()
    if _is_relative_to(resolved, oem_root):
        return True
    legacy_root = (project / ".harness").resolve()
    return _is_relative_to(resolved, legacy_root)


def _create_backup_dir(project: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = project / ".oem" / "backups" / f"clean-{stamp}"
    suffix = 1
    candidate = backup_dir
    while candidate.exists():
        suffix += 1
        candidate = backup_dir.with_name(f"{backup_dir.name}-{suffix}")
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _copy_backup(source: Path, backup_dir: Path, project: Path) -> None:
    if not source.exists() or not _safe_to_mutate(source, project):
        return
    source_resolved = source.resolve()
    try:
        rel = source_resolved.relative_to(project.resolve())
    except ValueError:
        rel = Path(source.name)
    destination = backup_dir / rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_resolved, destination)


def apply_cleanups(project: str | Path | None, report: dict[str, Any], backup: bool = True) -> dict[str, Any]:
    """Apply safe cleanups from a prior analysis report.

    Clean-01 intentionally performs no ambiguous repairs. It creates the
    requested backup and returns an apply-mode report. Future slices can add
    scoped safe mutations here without involving CLI code.
    """
    resolved_project = _resolve_project(project)
    apply_report = dict(report)
    apply_report["mode"] = "apply"
    apply_report["project"] = str(resolved_project)
    apply_report["changed_files"] = list(report.get("changed_files", []))
    apply_report["warnings"] = list(report.get("warnings", []))

    if not _project_allows_oem_mutation(resolved_project):
        apply_report["warnings"].append(
            "Error refusing to apply cleanups inside protected Codex/OpenCode configuration paths."
        )
        apply_report["status"] = "error"
        return apply_report

    if backup:
        try:
            backup_dir = _create_backup_dir(resolved_project)
            apply_report["backup_dir"] = str(backup_dir)
            for path in (_events_path(resolved_project), _registry_path(resolved_project)):
                _copy_backup(path, backup_dir, resolved_project)
        except OSError as exc:
            apply_report["warnings"].append(f"Error creating clean backup: {exc}")
            apply_report["status"] = "error"
            return apply_report
    else:
        apply_report["backup_dir"] = None

    if report.get("duplicates", {}).get("duplicate_runtime_events", 0):
        apply_report["warnings"].append(
            "Exact duplicate runtime event repair is detected but deferred to Clean-02; no events were mutated."
        )
    if (
        report.get("self_ingestion", {}).get("suspect_events", 0)
        or report.get("self_ingestion", {}).get("suspect_concepts", 0)
    ):
        apply_report["warnings"].append(
            "Self-ingestion suspects are reported for manual review; no generated knowledge was deleted."
        )
    if any(
        report.get("structure", {}).get(key, 0)
        for key in ("orphan_wiki_files", "missing_wiki_files", "duplicate_slugs")
    ):
        apply_report["warnings"].append(
            "Structure issues are reported for manual review; no wiki or registry files were merged or deleted."
        )

    _update_status(apply_report)
    return apply_report
