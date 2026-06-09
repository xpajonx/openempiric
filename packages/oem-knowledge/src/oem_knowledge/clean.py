from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

CleanScope = Literal[
    "self-ingestion", "duplicates", "structure", "registry", "legacy", "all"
]
CleanStatus = Literal["clean", "issues_found", "repaired", "repaired_partial", "error"]
CleanMode = Literal["dry_run", "apply"]

_ALLOWED_SCOPES = {
    "self-ingestion",
    "duplicates",
    "structure",
    "registry",
    "legacy",
    "all",
}
_EXPECTED_OEM_DIRS = ("wiki", "sessions", "state", "graph", "skills")
_RUNTIME_EVENT_NAMES = ("runtime_events.jsonl", "events.jsonl")
_OEM_SOURCE_MARKERS = (
    ".oem/",
    ".oem\\",
    "runtime_events.jsonl",
    "events.jsonl",
    "outcomes.jsonl",
    "session_reports",
    "session-report",
    "session_report",
    "session reports",
    "concept_registry.json",
    ".oem/wiki",
    ".oem\\wiki",
)
_SUSPICIOUS_SYSTEM_SLUGS = {
    "index",
    "log",
    "inbox",
    "schema",
    "purpose",
    "triggers",
    "runtime-events",
    "runtime_events",
    "outcomes",
    "session-report",
    "session-reports",
    "session_reports",
}
_RESERVED_WIKI_FILES = {"index", "inbox", "log"}
_FORBIDDEN_NAMES = {
    "opencode.jsonc",
    "config.toml",
    "config.json",
}
_CONCEPT_NUMBER_RE = re.compile(r"^concept[-_]?\d+$", re.IGNORECASE)


@dataclass(frozen=True)
class CleanFinding:
    """A single dry-run cleanup finding."""

    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"
    scope: str = "all"
    path: str | None = None
    line: int | None = None
    concept_id: str | None = None
    safe_repair: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CleanReport(dict):
    """Dictionary-compatible cleanup report with typed finding helpers."""

    @property
    def findings(self) -> list[CleanFinding]:
        findings: list[CleanFinding] = []
        for item in self.get("findings", []):
            if isinstance(item, CleanFinding):
                findings.append(item)
            elif isinstance(item, dict):
                allowed = {
                    data_field.name
                    for data_field in CleanFinding.__dataclass_fields__.values()
                }
                findings.append(
                    CleanFinding(
                        **{key: value for key, value in item.items() if key in allowed}
                    )
                )
        return findings

    def add_finding(self, finding: CleanFinding) -> None:
        self.setdefault("findings", []).append(finding.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return dict(self)


@dataclass(frozen=True)
class _JsonlRecord:
    line_no: int
    raw: str
    parsed: dict[str, Any] | None


def _empty_report(
    project: Path, scope: str, mode: CleanMode = "dry_run"
) -> CleanReport:
    return CleanReport(
        {
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
                "duplicate_canonical_names": 0,
                "wiki_without_registry": 0,
                "registry_without_wiki": 0,
                "suspicious_system_concepts": 0,
                "concept_sources_oem_artifacts": 0,
                "legacy_harness_artifacts": 0,
                "unknown_harness_files": 0,
            },
            "changed_files": [],
            "backup_dir": None,
            "report_path": None,
            "warnings": [],
            "findings": [],
            "repair_plan": [],
        }
    )


def _resolve_project(project: str | Path | None) -> Path:
    return Path(project or ".").expanduser().resolve()


def _oem_dir(project: Path) -> Path:
    return project / ".oem"


def _runtime_event_paths(project: Path) -> list[Path]:
    oem = _oem_dir(project)
    return [oem / name for name in _RUNTIME_EVENT_NAMES]


def _events_path(project: Path) -> Path:
    paths = _runtime_event_paths(project)
    for path in paths:
        if path.exists():
            return path
    return paths[0]


def _registry_path(project: Path) -> Path:
    return _oem_dir(project) / "concept_registry.json"


def _wiki_dir(project: Path) -> Path:
    return _oem_dir(project) / "wiki"


def _selected(scope: str, name: str) -> bool:
    if name == "registry":
        name = "structure"
    return (
        scope == "all" or scope == name or (scope == "registry" and name == "structure")
    )


def _relative_display(path: Path, project: Path) -> str:
    try:
        return str(path.resolve().relative_to(project.resolve()))
    except ValueError:
        return str(path)


def _read_jsonl_records(path: Path, report: CleanReport) -> list[_JsonlRecord]:
    if not path.exists():
        return []

    records: list[_JsonlRecord] = []
    try:
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            raw = line.strip()
            if not raw:
                continue
            parsed: dict[str, Any] | None = None
            try:
                item = json.loads(raw)
                if isinstance(item, dict):
                    parsed = item
            except json.JSONDecodeError as exc:
                report["warnings"].append(
                    f"Could not parse {path.name}:{line_no}: {exc}"
                )
            records.append(_JsonlRecord(line_no=line_no, raw=raw, parsed=parsed))
    except OSError as exc:
        report["warnings"].append(f"Could not read {path}: {exc}")
    return records


def _read_jsonl(path: Path, report: CleanReport) -> list[dict[str, Any]]:
    return [
        record.parsed
        for record in _read_jsonl_records(path, report)
        if record.parsed is not None
    ]


def _read_registry(path: Path, report: CleanReport) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report["warnings"].append(f"Could not read concept registry: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _json_record_key(raw: str, parsed: dict[str, Any] | None) -> str:
    if parsed is not None:
        try:
            normalized = dict(parsed)
            # Runtime event IDs are capture metadata, not event content. Treat
            # otherwise identical materialization records as duplicates while
            # still preserving the earliest full line verbatim.
            normalized.pop("event_id", None)
            normalized.pop("id", None)
            return json.dumps(normalized, sort_keys=True, separators=(",", ":"))
        except TypeError:
            pass
    return raw


def _path_text(value: Any) -> str:
    return str(value).replace("\\", "/").lower()


def _is_under_oem(value: Any) -> bool:
    text = _path_text(value)
    parts = PurePosixPath(text).parts
    return ".oem" in parts or text.startswith(".oem/") or "/.oem/" in text


def _is_oem_generated_source(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_is_oem_generated_source(item) for item in value)
    if isinstance(value, dict):
        return any(_is_oem_generated_source(item) for item in value.values())
    text = _path_text(value)
    return any(marker in text for marker in _OEM_SOURCE_MARKERS)


def _source_fields(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("source"),
        item.get("sources"),
        item.get("source_path"),
        item.get("source_paths"),
        item.get("source_file"),
        item.get("path"),
        item.get("paths"),
        item.get("evidence"),
        item.get("metadata"),
    )


def _event_text(event: dict[str, Any]) -> str:
    for key in ("text", "message", "summary", "evidence", "content"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _slug(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _concept_slug(cid: str, concept: dict[str, Any]) -> str:
    for key in ("slug", "canonical_slug"):
        if concept.get(key):
            return _slug(str(concept[key]))
    return _slug(str(concept.get("canonical_name") or concept.get("title") or cid))


def _concept_title(cid: str, concept: dict[str, Any]) -> str:
    return str(
        concept.get("title")
        or concept.get("canonical_name")
        or concept.get("name")
        or cid
    )


def _is_concept_number(value: str) -> bool:
    return bool(_CONCEPT_NUMBER_RE.match(_slug(value)))


def _is_suspicious_system_slug(value: str) -> bool:
    slug = _slug(value)
    return slug in _SUSPICIOUS_SYSTEM_SLUGS or _is_concept_number(slug)


def _add_finding(report: CleanReport, finding: CleanFinding) -> None:
    report.add_finding(finding)
    if finding.safe_repair:
        report.setdefault("repair_plan", []).append(finding.to_dict())


def _duplicate_runtime_records(
    path: Path, report: CleanReport, project: Path
) -> tuple[list[_JsonlRecord], list[_JsonlRecord]]:
    records = _read_jsonl_records(path, report)
    seen: set[str] = set()
    kept: list[_JsonlRecord] = []
    duplicates: list[_JsonlRecord] = []
    for record in records:
        key = _json_record_key(record.raw, record.parsed)
        if key in seen:
            duplicates.append(record)
            _add_finding(
                report,
                CleanFinding(
                    code="duplicate_runtime_event",
                    scope="duplicates",
                    path=_relative_display(path, project),
                    line=record.line_no,
                    message="Exact duplicate runtime event; earliest matching event will be preserved.",
                    safe_repair="deduplicate_runtime_event_line",
                    details={"raw": record.raw},
                ),
            )
        else:
            seen.add(key)
            kept.append(record)
    return kept, duplicates


def _update_status(report: dict[str, Any]) -> None:
    if report["warnings"] and any(
        str(w).lower().startswith("error") for w in report["warnings"]
    ):
        report["status"] = "error"
        return

    structure = report.get("structure", {})
    issue_count = (
        report.get("self_ingestion", {}).get("suspect_events", 0)
        + report.get("self_ingestion", {}).get("suspect_concepts", 0)
        + report.get("duplicates", {}).get("duplicate_runtime_events", 0)
        + sum(int(structure.get(key, 0) or 0) for key in structure)
        + len(report.get("findings", []))
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


def analyze_project(
    project_path: str | Path | None, scope: CleanScope = "all"
) -> CleanReport:
    """Analyze OEM cleanliness without mutating files."""
    if scope not in _ALLOWED_SCOPES:
        raise ValueError(f"Invalid clean scope: {scope}")

    resolved_project = _resolve_project(project_path)
    report = _empty_report(resolved_project, scope, mode="dry_run")
    oem_root = _oem_dir(resolved_project)
    events_path = _events_path(resolved_project)

    events: list[dict[str, Any]] = []
    event_records: list[_JsonlRecord] = []
    if _selected(scope, "self-ingestion") or _selected(scope, "duplicates"):
        event_records = _read_jsonl_records(events_path, report)
        events = [
            record.parsed for record in event_records if record.parsed is not None
        ]

    if _selected(scope, "self-ingestion"):
        suspect_events = 0
        for record in event_records:
            event = record.parsed or {}
            has_oem_source_path = _is_oem_generated_source(_source_fields(event))
            has_ingest_text = _event_text(event).startswith(
                "Ingest | Materialized concept"
            )
            if has_oem_source_path or has_ingest_text:
                suspect_events += 1
                _add_finding(
                    report,
                    CleanFinding(
                        code="self_ingested_event",
                        scope="self-ingestion",
                        path=_relative_display(events_path, resolved_project),
                        line=record.line_no,
                        message="Runtime event appears to be derived from an OpenEmpiric-generated artifact.",
                        details={
                            "reason": (
                                "oem_source_path"
                                if has_oem_source_path
                                else "materialized_concept_text"
                            )
                        },
                    ),
                )
        report["self_ingestion"]["suspect_events"] = suspect_events

        registry = _read_registry(_registry_path(resolved_project), report)
        suspect_concepts = 0
        for cid, concept in registry.items():
            if not isinstance(concept, dict):
                continue
            title = _concept_title(str(cid), concept)
            slug = _concept_slug(str(cid), concept)
            source_fields = _source_fields(concept)
            source_is_oem_artifact = any(
                _is_oem_generated_source(field) for field in source_fields
            )
            concept_number_name = _is_concept_number(title) or _is_concept_number(slug)
            if source_is_oem_artifact or concept_number_name:
                suspect_concepts += 1
                _add_finding(
                    report,
                    CleanFinding(
                        code="self_ingested_concept",
                        scope="self-ingestion",
                        path=_relative_display(
                            _registry_path(resolved_project), resolved_project
                        ),
                        concept_id=str(cid),
                        message="Concept appears to be generated from OpenEmpiric artifacts or placeholder concept names.",
                        details={
                            "slug": slug,
                            "title": title,
                            "source_is_oem_artifact": source_is_oem_artifact,
                        },
                    ),
                )
        report["self_ingestion"]["suspect_concepts"] = suspect_concepts

    if _selected(scope, "duplicates"):
        _, duplicates = _duplicate_runtime_records(
            events_path, report, resolved_project
        )
        report["duplicates"]["duplicate_runtime_events"] = len(duplicates)

    if _selected(scope, "structure"):
        registry_path = _registry_path(resolved_project)
        registry = _read_registry(registry_path, report)
        wiki_dir = _wiki_dir(resolved_project)
        registry_ids = {str(cid) for cid in registry.keys()}
        wiki_ids = (
            {path.stem for path in wiki_dir.glob("*.md")}
            if wiki_dir.exists()
            else set()
        )
        concept_wiki_ids = wiki_ids - _RESERVED_WIKI_FILES

        missing = registry_ids - concept_wiki_ids
        orphan = concept_wiki_ids - registry_ids
        report["structure"]["orphan_wiki_files"] = len(orphan)
        report["structure"]["wiki_without_registry"] = len(orphan)
        report["structure"]["missing_wiki_files"] = len(missing)
        report["structure"]["registry_without_wiki"] = len(missing)

        for cid in sorted(missing):
            _add_finding(
                report,
                CleanFinding(
                    code="registry_entry_missing_wiki",
                    scope="structure",
                    path=_relative_display(registry_path, resolved_project),
                    concept_id=cid,
                    message="Registry entry exists but matching wiki file is missing.",
                ),
            )
        for wiki_id in sorted(orphan):
            _add_finding(
                report,
                CleanFinding(
                    code="wiki_missing_registry_entry",
                    scope="structure",
                    path=_relative_display(
                        wiki_dir / f"{wiki_id}.md", resolved_project
                    ),
                    concept_id=wiki_id,
                    message="Wiki file exists but matching registry entry is missing.",
                ),
            )

        slug_to_ids: dict[str, list[str]] = defaultdict(list)
        name_to_ids: dict[str, list[str]] = defaultdict(list)
        suspicious = 0
        source_artifacts = 0
        for cid, concept in registry.items():
            if not isinstance(concept, dict):
                continue
            cid_text = str(cid)
            title = _concept_title(cid_text, concept)
            slug = _concept_slug(cid_text, concept)
            canonical_key = _slug(str(concept.get("canonical_name") or title))
            slug_to_ids[slug].append(cid_text)
            name_to_ids[canonical_key].append(cid_text)

            if _is_suspicious_system_slug(slug) or _is_suspicious_system_slug(title):
                suspicious += 1
                _add_finding(
                    report,
                    CleanFinding(
                        code="suspicious_system_concept",
                        scope="structure",
                        path=_relative_display(registry_path, resolved_project),
                        concept_id=cid_text,
                        message="Concept slug/title resembles a generated system artifact; review manually unless it is clearly self-ingested.",
                        details={"slug": slug, "title": title},
                    ),
                )
            if any(
                _is_oem_generated_source(field) for field in _source_fields(concept)
            ):
                source_artifacts += 1
                _add_finding(
                    report,
                    CleanFinding(
                        code="concept_source_oem_artifact",
                        scope="structure",
                        path=_relative_display(registry_path, resolved_project),
                        concept_id=cid_text,
                        message="Concept source points to an OpenEmpiric-generated artifact.",
                        details={"slug": slug, "title": title},
                    ),
                )

        duplicate_slugs = {
            slug: ids for slug, ids in slug_to_ids.items() if len(ids) > 1
        }
        duplicate_names = {
            name: ids for name, ids in name_to_ids.items() if name and len(ids) > 1
        }
        report["structure"]["duplicate_slugs"] = len(duplicate_slugs)
        report["structure"]["duplicate_canonical_names"] = len(duplicate_names)
        report["structure"]["suspicious_system_concepts"] = suspicious
        report["structure"]["concept_sources_oem_artifacts"] = source_artifacts

        for slug, ids in sorted(duplicate_slugs.items()):
            _add_finding(
                report,
                CleanFinding(
                    code="duplicate_slug",
                    scope="structure",
                    path=_relative_display(registry_path, resolved_project),
                    message="Duplicate slug across concept IDs; no automatic merge will be performed.",
                    details={"slug": slug, "concept_ids": ids},
                ),
            )
        for name, ids in sorted(duplicate_names.items()):
            _add_finding(
                report,
                CleanFinding(
                    code="duplicate_canonical_name",
                    scope="structure",
                    path=_relative_display(registry_path, resolved_project),
                    message="Duplicate canonical name across concept IDs; no automatic merge will be performed.",
                    details={"canonical_name_slug": name, "concept_ids": ids},
                ),
            )

        missing_dirs = [
            dirname
            for dirname in _EXPECTED_OEM_DIRS
            if not (oem_root / dirname).is_dir()
        ]
        if missing_dirs:
            report["warnings"].append(
                f"Missing expected .oem dirs: {', '.join(missing_dirs)}"
            )

    if _selected(scope, "legacy"):
        legacy_root = resolved_project / ".harness"
        if legacy_root.exists():
            recognized = (
                legacy_root / "directives" / "wiki_concepts",
                legacy_root / "directives" / "sessions",
                legacy_root / "directives" / "wiki_inbox.md",
                legacy_root / "directives" / "index.md",
                legacy_root / "directives" / "log.md",
                legacy_root / "state" / "concept_registry.json",
                legacy_root / "state" / "events.jsonl",
            )
            recognized_existing = [path for path in recognized if path.exists()]
            report["structure"]["legacy_harness_artifacts"] = len(recognized_existing)
            for path in recognized_existing:
                _add_finding(
                    report,
                    CleanFinding(
                        code="legacy_harness_artifact",
                        scope="legacy",
                        path=_relative_display(path, resolved_project),
                        message="Recognized legacy OEM harness artifact; eligible for explicit migration under .oem only.",
                    ),
                )

            recognized_resolved = {path.resolve() for path in recognized_existing}
            unknown = [
                path
                for path in legacy_root.rglob("*")
                if path.is_file() and path.resolve() not in recognized_resolved
            ]
            report["structure"]["unknown_harness_files"] = len(unknown)
            for path in unknown:
                _add_finding(
                    report,
                    CleanFinding(
                        code="unknown_harness_file",
                        scope="legacy",
                        path=_relative_display(path, resolved_project),
                        message="Unknown file under legacy harness; leave untouched unless reviewed by a user.",
                    ),
                )

    if report["structure"].get("suspicious_system_concepts"):
        report["warnings"].append(
            "Suspicious system concepts require manual review; no quarantine mechanism is available in the current cleanup API."
        )

    _update_status(report)
    return report


def analyze_cleanliness(
    project: str | Path | None, scope: CleanScope = "all"
) -> CleanReport:
    """Backward-compatible alias for :func:`analyze_project`."""
    return analyze_project(project, scope)


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
    if resolved.name.lower() in _FORBIDDEN_NAMES and (
        "codex" in parts or ".codex" in parts
    ):
        return True
    if ("opencode" in parts or ".opencode" in parts) and (
        "plugins" in parts or "plugin" in parts
    ):
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


def _create_report_dir(project: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = project / ".oem" / "reports" / f"clean-{stamp}"
    suffix = 1
    candidate = report_dir
    while candidate.exists():
        suffix += 1
        candidate = report_dir.with_name(f"{report_dir.name}-{suffix}")
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


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _apply_duplicate_runtime_repair(
    project: Path, report: CleanReport, audit_dir: Path
) -> int:
    events_path = _events_path(project)
    if not events_path.exists() or not _safe_to_mutate(events_path, project):
        return 0
    scratch = _empty_report(project, str(report.get("scope", "all")))
    kept, duplicates = _duplicate_runtime_records(events_path, scratch, project)
    if not duplicates:
        return 0

    audit_path = audit_dir / "removed_duplicate_runtime_events.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("w", encoding="utf-8") as audit_handle:
        for duplicate in duplicates:
            audit_handle.write(
                json.dumps(
                    {"line": duplicate.line_no, "raw": duplicate.raw}, sort_keys=True
                )
                + "\n"
            )

    events_path.write_text(
        "".join(f"{record.raw}\n" for record in kept), encoding="utf-8"
    )
    report["changed_files"].append(_relative_display(events_path, project))
    report["changed_files"].append(_relative_display(audit_path, project))
    return len(duplicates)


def apply_repairs(
    project_path: str | Path | None, analysis: dict[str, Any], backup: bool = True
) -> CleanReport:
    """Apply safe repairs from a prior dry-run analysis.

    The only destructive repair currently performed is exact runtime-event
    deduplication. Ambiguous self-ingestion, suspicious system concepts, and
    registry/wiki consistency issues are retained as manual-review findings.
    """
    resolved_project = _resolve_project(project_path)
    apply_report = CleanReport(dict(analysis))
    apply_report["mode"] = "apply"
    apply_report["project"] = str(resolved_project)
    apply_report["changed_files"] = list(analysis.get("changed_files", []))
    apply_report["warnings"] = list(analysis.get("warnings", []))
    apply_report["findings"] = list(analysis.get("findings", []))
    apply_report["repair_plan"] = list(analysis.get("repair_plan", []))

    if not _project_allows_oem_mutation(resolved_project):
        apply_report["warnings"].append(
            "Error refusing to apply cleanups inside protected Codex/OpenCode configuration paths."
        )
        apply_report["status"] = "error"
        return apply_report

    audit_dir: Path
    if backup:
        try:
            backup_dir = _create_backup_dir(resolved_project)
            apply_report["backup_dir"] = str(backup_dir)
            for path in (
                *_runtime_event_paths(resolved_project),
                _registry_path(resolved_project),
            ):
                _copy_backup(path, backup_dir, resolved_project)
            audit_dir = backup_dir / "audit"
        except OSError as exc:
            apply_report["warnings"].append(f"Error creating clean backup: {exc}")
            apply_report["status"] = "error"
            return apply_report
    else:
        apply_report["backup_dir"] = None
        audit_dir = _create_report_dir(resolved_project)

    if analysis.get("duplicates", {}).get("duplicate_runtime_events", 0):
        try:
            repaired_duplicates = _apply_duplicate_runtime_repair(
                resolved_project, apply_report, audit_dir
            )
            if repaired_duplicates:
                apply_report.setdefault("duplicates", {})[
                    "duplicate_runtime_events"
                ] = max(
                    0,
                    int(
                        apply_report.get("duplicates", {}).get(
                            "duplicate_runtime_events", 0
                        )
                        or 0
                    )
                    - repaired_duplicates,
                )
                duplicate_codes = {"duplicate_runtime_event"}
                apply_report["findings"] = [
                    finding
                    for finding in apply_report.get("findings", [])
                    if not (
                        isinstance(finding, dict)
                        and finding.get("code") in duplicate_codes
                    )
                ]
                apply_report["repair_plan"] = [
                    finding
                    for finding in apply_report.get("repair_plan", [])
                    if not (
                        isinstance(finding, dict)
                        and finding.get("code") in duplicate_codes
                    )
                ]
        except OSError as exc:
            apply_report["warnings"].append(
                f"Error repairing duplicate runtime events: {exc}"
            )

    if analysis.get("self_ingestion", {}).get("suspect_events", 0) or analysis.get(
        "self_ingestion", {}
    ).get("suspect_concepts", 0):
        apply_report["warnings"].append(
            "Self-ingestion suspects are reported for manual review; no generated knowledge was deleted."
        )
    if any(
        analysis.get("structure", {}).get(key, 0)
        for key in (
            "orphan_wiki_files",
            "missing_wiki_files",
            "duplicate_slugs",
            "duplicate_canonical_names",
            "suspicious_system_concepts",
            "concept_sources_oem_artifacts",
            "legacy_harness_artifacts",
            "unknown_harness_files",
        )
    ):
        apply_report["warnings"].append(
            "Structure and suspicious system concept issues are reported for manual review; no wiki or registry files were merged or deleted."
        )

    report_path = audit_dir / "clean_report.json"
    apply_report["report_path"] = str(report_path)
    if (
        _relative_display(report_path, resolved_project)
        not in apply_report["changed_files"]
    ):
        apply_report["changed_files"].append(
            _relative_display(report_path, resolved_project)
        )
    _update_status(apply_report)
    try:
        _write_json(report_path, apply_report.to_dict())
    except OSError as exc:
        apply_report["warnings"].append(f"Error writing clean report: {exc}")
        _update_status(apply_report)

    return apply_report


def apply_cleanups(
    project: str | Path | None, report: dict[str, Any], backup: bool = True
) -> CleanReport:
    """Backward-compatible alias for :func:`apply_repairs`."""
    return apply_repairs(project, report, backup=backup)
