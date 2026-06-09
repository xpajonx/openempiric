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

@dataclass
class CleanBackupResult:
    backup_dir: Path
    files_backed_up: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


CleanReport = dict[str, Any]

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
                "removed_duplicate_runtime_events": 0,
                "duplicate_runtime_event_details": [],
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
            "system_concepts": {
                "suspicious_concepts": 0,
                "suspicious_concept_ids": [],
            },
            "checks_performed": [],
            "files_backed_up": [],
            "backup_warnings": [],
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


def _runtime_events_path(project: Path) -> Path:
    return _oem_dir(project) / "runtime_events.jsonl"


def _legacy_events_path(project: Path) -> Path:
    return _oem_dir(project) / "events.jsonl"


def _event_log_paths(project: Path) -> tuple[Path, ...]:
    return tuple(_runtime_event_paths(project))


def _outcomes_path(project: Path) -> Path:
    return _oem_dir(project) / "outcomes.jsonl"


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


def _is_suspicious_system_concept(value: str | None) -> bool:
    if value is None:
        return False
    slug = _slug(str(value))
    return slug in {
        "index",
        "log",
        "inbox",
        "schema",
        "purpose",
        "triggers",
        "runtime-events",
        "outcomes",
        "session-report",
    } or bool(re.fullmatch(r"concept-\d+", slug))


def _event_duplicate_key(event: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(event)
    normalized.pop("event_id", None)
    normalized.pop("id", None)
    return normalized


def _dedupe_key_for_line(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw:
        return None
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        return ("raw", raw)
    if not isinstance(item, dict):
        return ("raw", raw)
    return ("json", json.dumps(_event_duplicate_key(item), sort_keys=True))


def _dedupe_runtime_event_lines(path: Path, mutate: bool) -> dict[str, Any]:
    if not path.exists():
        return {"removed": 0, "details": [], "changed": False}

    original = path.read_text(encoding="utf-8").splitlines()
    seen: set[tuple[str, str]] = set()
    kept: list[str] = []
    details: list[dict[str, Any]] = []

    for line_no, line in enumerate(original, start=1):
        key = _dedupe_key_for_line(line)
        if key is None:
            kept.append(line)
            continue
        if key in seen:
            details.append({
                "path": str(path),
                "line": line_no,
                "preview": line[:160],
            })
            continue
        seen.add(key)
        kept.append(line)

    changed = len(details) > 0
    if mutate and changed:
        trailing_newline = "\n" if path.read_text(encoding="utf-8").endswith("\n") else ""
        path.write_text("\n".join(kept) + trailing_newline, encoding="utf-8")

    return {"removed": len(details), "details": details, "changed": changed}


def _update_status(report: dict[str, Any]) -> None:
    if report["warnings"] and any(
        str(w).lower().startswith("error") for w in report["warnings"]
    ):
        report["status"] = "error"
        return

    structure = report.get("structure", {})
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
        for event_path in _event_log_paths(resolved_project):
            events.extend(_read_jsonl(event_path, report))

    if _selected(scope, "self-ingestion"):
        report["checks_performed"].append("self-ingestion")
        report["self_ingestion"]["suspect_events"] = sum(
            1
            for event in events
            if _is_oem_generated_source(event.get("source"))
            or _is_oem_generated_source(event.get("evidence"))
        )

    if _selected(scope, "self-ingestion"):
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

    if _selected(scope, "self-ingestion"):
        registry = _read_registry(_registry_path(resolved_project), report)
        suspicious_ids = []
        for cid, concept in registry.items():
            name = concept.get("canonical_name") if isinstance(concept, dict) else cid
            if _is_suspicious_system_concept(str(name)) or _is_suspicious_system_concept(str(cid)):
                suspicious_ids.append(str(cid))
        report["system_concepts"]["suspicious_concepts"] = len(suspicious_ids)
        report["system_concepts"]["suspicious_concept_ids"] = suspicious_ids

    if _selected(scope, "duplicates"):
        report["checks_performed"].append("duplicates")
        seen: set[tuple[str, str, str, str, str]] = set()
        duplicate_count = 0
        duplicate_details: list[dict[str, Any]] = []
        for event_path in _event_log_paths(resolved_project):
            result = _dedupe_runtime_event_lines(event_path, mutate=False)
            duplicate_count += result["removed"]
            duplicate_details.extend(result["details"])
        report["duplicates"]["duplicate_runtime_events"] = duplicate_count
        report["duplicates"]["duplicate_runtime_event_details"] = duplicate_details

    if _selected(scope, "structure"):
        report["checks_performed"].append("structure")
        registry = _read_registry(_registry_path(resolved_project), report)
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
                    path=_relative_display(_registry_path(resolved_project), resolved_project),
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
                        path=_relative_display(_registry_path(resolved_project), resolved_project),
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
                        path=_relative_display(_registry_path(resolved_project), resolved_project),
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
                    path=_relative_display(_registry_path(resolved_project), resolved_project),
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
                    path=_relative_display(_registry_path(resolved_project), resolved_project),
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


def clean_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def collect_backup_candidates(harness: Path) -> list[Path]:
    """Return clean-apply backup candidates under ``harness/.oem``.

    The list intentionally includes only OEM memory artifacts that current or
    near-future clean slices may mutate. Missing paths are returned so backup
    creation can warn consistently while skipping them gracefully.
    """
    project = _resolve_project(harness)
    oem_root = _oem_dir(project)
    candidates = [
        _registry_path(project),
        _runtime_events_path(project),
        _outcomes_path(project),
    ]

    # Legacy event stores are still used by older projects and by the current
    # event layout helper, so back them up when present without making them a
    # required modern clean artifact.
    legacy_events = _legacy_events_path(project)
    if legacy_events.exists():
        candidates.append(legacy_events)

    wiki_dir = _wiki_dir(project)
    if wiki_dir.exists():
        candidates.extend(path for path in wiki_dir.rglob("*") if path.is_file())
    else:
        candidates.append(wiki_dir)

    return candidates


def create_clean_backup(harness: Path, timestamp: str) -> CleanBackupResult:
    project = _resolve_project(harness)
    oem_root = _oem_dir(project)
    backup_dir = oem_root / "backups" / f"clean-{timestamp}"
    suffix = 1
    candidate = backup_dir
    while candidate.exists():
        suffix += 1
        candidate = backup_dir.with_name(f"{backup_dir.name}-{suffix}")

    result = CleanBackupResult(backup_dir=candidate)
    candidate.mkdir(parents=True, exist_ok=False)

    for source in collect_backup_candidates(project):
        if not source.exists():
            result.warnings.append(f"Backup skipped missing path: {source}")
            continue
        if not source.is_file():
            result.warnings.append(f"Backup skipped non-file path: {source}")
            continue
        if not _safe_to_mutate(source, project):
            result.warnings.append(f"Backup skipped unsafe path: {source}")
            continue

        source_resolved = source.resolve()
        try:
            rel = source_resolved.relative_to(oem_root.resolve())
        except ValueError:
            rel = Path(source.name)
        destination = candidate / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_resolved, destination)
        result.files_backed_up.append(source_resolved)

    return result


def write_clean_report(
    harness: Path,
    report: CleanReport,
    timestamp: str,
) -> Path:
    project = _resolve_project(harness)
    reports_dir = _oem_dir(project) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"clean-{timestamp}.md"
    suffix = 1
    while report_path.exists():
        suffix += 1
        report_path = reports_dir / f"clean-{timestamp}-{suffix}.md"

    lines = [
        "---",
        "generated_by: openempiric",
        "source_type: oem_generated",
        "command: oem clean",
        f"mode: {report.get('mode', 'apply')}",
        "---",
        "",
        "# OEM Clean Report",
        "",
        f"- Timestamp: {timestamp}",
        f"- Project path: {report.get('project', str(project))}",
        f"- Mode: {report.get('mode', 'apply')}",
        f"- Scope: {report.get('scope', 'all')}",
        f"- Status: {report.get('status', '')}",
        "",
        "## Checks performed",
        *_format_clean_report_list(list(report.get("checks_performed", []))),
        "",
        "## Suspected self-ingestion events",
        f"- Suspect events: {report.get('self_ingestion', {}).get('suspect_events', 0)}",
        f"- Suspect concepts: {report.get('self_ingestion', {}).get('suspect_concepts', 0)}",
        "",
        "## Duplicate events removed",
        f"- Duplicate runtime events detected: {report.get('duplicates', {}).get('duplicate_runtime_events', 0)}",
        f"- Removed: {report.get('duplicates', {}).get('removed_duplicate_runtime_events', 0)}",
        "",
        "## Suspicious concepts",
        f"- Suspect concepts: {report.get('self_ingestion', {}).get('suspect_concepts', 0)}",
        "",
        "## Registry/wiki inconsistencies",
        f"- Orphan wiki files: {report.get('structure', {}).get('orphan_wiki_files', 0)}",
        f"- Missing wiki files: {report.get('structure', {}).get('missing_wiki_files', 0)}",
        f"- Duplicate slugs: {report.get('structure', {}).get('duplicate_slugs', 0)}",
        "",
        "## Files changed",
        *_format_clean_report_list(list(report.get("changed_files", []))),
        "",
        "## Files backed up",
        *_format_clean_report_list(list(report.get("files_backed_up", []))),
        "",
        "## Skipped unsafe repairs",
        *_format_clean_report_list(list(report.get("skipped_unsafe_repairs", []))),
        "",
        "## Warnings",
        *_format_clean_report_list(list(report.get("warnings", []))),
        "",
    ]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_apply_report(project: Path, report: dict[str, Any]) -> Path:
    reports_dir = project / ".oem" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = reports_dir / f"clean-{stamp}.md"
    suffix = 1
    while report_path.exists():
        suffix += 1
        report_path = reports_dir / f"clean-{stamp}-{suffix}.md"

    duplicate_details = report.get("duplicates", {}).get("duplicate_runtime_event_details", [])
    lines = [
        "# OEM Clean Report",
        "",
        f"Mode: {report.get('mode')}",
        f"Status: {report.get('status')}",
        f"Project: {report.get('project')}",
        f"Backup: {report.get('backup_dir') or 'none'}",
        "",
        "## Removed duplicates",
        f"Removed duplicate runtime events: {report.get('duplicates', {}).get('removed_duplicate_runtime_events', 0)}",
    ]
    for detail in duplicate_details:
        lines.append(
            f"- {detail.get('path')}:{detail.get('line')} — {detail.get('preview')}"
        )
    if not duplicate_details:
        lines.append("- none")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def apply_cleanups(project: str | Path | None, report: dict[str, Any], backup: bool = True) -> dict[str, Any]:
    """Apply safe cleanups from a prior analysis report."""
    resolved_project = _resolve_project(project)
    apply_report: dict[str, Any] = report.copy()
    apply_report["mode"] = "apply"
    backup_result: CleanBackupResult | None = None
    
    if backup:
        timestamp = clean_timestamp()
        try:
            backup_result = create_clean_backup(resolved_project, timestamp)
        except OSError as exc:
            apply_report["status"] = "error"
            apply_report["warnings"].append(f"Error creating clean backup: {exc}")
            apply_report["report_path"] = None
            return apply_report
        apply_report["backup_dir"] = str(backup_result.backup_dir)
        apply_report["files_backed_up"] = [str(p) for p in backup_result.files_backed_up]
        apply_report["backup_warnings"] = backup_result.warnings

    if backup and backup_result is not None:
        for path in (
            *_runtime_event_paths(resolved_project),
            _registry_path(resolved_project),
        ):
            _copy_backup(path, backup_result.backup_dir, resolved_project)
        audit_dir = backup_result.backup_dir / "audit"
    else:
        audit_dir = _oem_dir(resolved_project) / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)

    if apply_report.get("duplicates", {}).get("duplicate_runtime_events", 0):
        try:
            repaired_duplicates = _apply_duplicate_runtime_repair(
                resolved_project, apply_report, audit_dir
            )
            apply_report["duplicates"]["removed_duplicate_runtime_events"] = repaired_duplicates
            if repaired_duplicates:
                apply_report["duplicates"]["duplicate_runtime_events"] = max(
                    0,
                    int(
                        apply_report.get("duplicates", {}).get(
                            "duplicate_runtime_events", 0
                        )
                        or 0
                    )
                    - repaired_duplicates,
                )
                apply_report["changed_files"] = [
                    str((resolved_project / path).resolve())
                    if not Path(path).is_absolute()
                    else str(path)
                    for path in apply_report.get("changed_files", [])
                ]
        except Exception as exc:
            apply_report["status"] = "error"
            apply_report["warnings"].append(f"Error applying duplicate cleanup: {exc}")

    if (
        apply_report.get("self_ingestion", {}).get("suspect_events", 0)
        or apply_report.get("self_ingestion", {}).get("suspect_concepts", 0)
    ):
        try:
            repaired_self_ingestion = _apply_self_ingestion_repair(
                resolved_project, apply_report, audit_dir
            )
            apply_report["self_ingestion"]["repaired_events"] = repaired_self_ingestion.get("events", 0)
            apply_report["self_ingestion"]["repaired_concepts"] = repaired_self_ingestion.get("concepts", 0)
        except Exception as exc:
            apply_report["status"] = "error"
            apply_report["warnings"].append(f"Error applying self-ingestion cleanup: {exc}")

    try:
        report_path = write_clean_report(resolved_project, apply_report, timestamp if backup else "")
        apply_report["report_path"] = str(report_path)
    except OSError as exc:
        apply_report["warnings"].append(f"Error writing clean report: {exc}")
        apply_report["status"] = "error"

    return apply_report


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


def _apply_self_ingestion_repair(
    project: Path, report: CleanReport, audit_dir: Path
) -> dict[str, int]:
    result = {"events": 0, "concepts": 0}
    
    events_path = _events_path(project)
    if _safe_to_mutate(events_path, project):
        events_to_remove = []
        for event in _read_jsonl(events_path, report):
            if (_is_oem_generated_source(event.get("source")) or 
                _is_oem_generated_source(event.get("evidence")) or
                _event_text(event).startswith("Ingest | Materialized concept")):
                events_to_remove.append(event)
        
        if events_to_remove:
            lines = events_path.read_text(encoding="utf-8").splitlines()
            filtered_lines = []
            for line in lines:
                if line.strip() and not any(
                    json.loads(line).get("event_id") == event["event_id"]
                    for event in events_to_remove
                ):
                    filtered_lines.append(line)
            
            events_path.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")
            result["events"] = len(events_to_remove)
            report["changed_files"].append(_relative_display(events_path, project))

    registry_path = _registry_path(project)
    if _safe_to_mutate(registry_path, project):
        registry = _read_registry(registry_path, report)
        filtered_registry = {}
        for cid, concept in registry.items():
            if not _is_oem_generated_source(_source_fields(concept)):
                filtered_registry[cid] = concept
        
        if len(filtered_registry) != len(registry):
            _write_json(registry_path, filtered_registry)
            result["concepts"] = len(registry) - len(filtered_registry)
            report["changed_files"].append(_relative_display(registry_path, project))
    
    return result


def _format_clean_report_list(values: list[Any]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {value}" for value in values]


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
