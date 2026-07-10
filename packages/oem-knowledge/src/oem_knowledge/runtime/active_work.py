from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Semantic field types for active work
# ---------------------------------------------------------------------------

ActiveWorkField = Literal[
    "workspace_root",
    "memory_root",
    "active_work_item",
    "active_topic",
    "active_task",
    "unknown",
]

FIELD_PRIORITY = [
    "workspace_root",
    "memory_root",
    "active_work_item",
    "active_topic",
    "active_task",
]


# ---------------------------------------------------------------------------
# Legacy data structures (kept for backward compatibility)
# ---------------------------------------------------------------------------

@dataclass
class ActiveWorkItem:
    source: str
    key: str
    detail: str
    score: float = 0.0


@dataclass
class ActiveWorkResult:
    has_active_work: bool
    items: list[ActiveWorkItem] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    score: float = 0.0


# ---------------------------------------------------------------------------
# New data structures for active-work resolution (field-aware)
# ---------------------------------------------------------------------------

@dataclass
class ActiveWorkFieldValue:
    """A single semantic field value extracted from a source, with its own confidence."""
    value: str | None
    confidence: str  # "high", "medium", or "low"
    evidence: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class ActiveWorkSource:
    """All active-work fields contributed by one source file."""
    source: str
    path: str | None = None
    fields: dict[ActiveWorkField, ActiveWorkFieldValue] = field(default_factory=dict)

    def get(self, field: ActiveWorkField) -> ActiveWorkFieldValue | None:
        return self.fields.get(field)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "path": self.path,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
        }


@dataclass
class ActiveWorkConflict:
    """Conflict on a specific semantic field."""
    semantic_field: ActiveWorkField
    type: str  # e.g. "active_work_item_mismatch"
    sources: list[str]
    severity: str  # "error" or "warning"
    message: str
    source_details: dict[str, dict[str, str | None]] = field(default_factory=dict)
    suggestion: str = "Inspect sources and align the specific field."

    def to_dict(self) -> dict:
        d = {
            "field": self.semantic_field,
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "sources": self.source_details,
            "suggestion": self.suggestion,
        }
        # Back-compat alias for old consumers
        if self.semantic_field == "active_work_item":
            d["legacy_type"] = "active_project_mismatch"
        return d


@dataclass
class ActiveWorkIdentity:
    """Canonical field-aware active work identity."""
    workspace_root: str | None = None
    memory_root: str | None = None
    active_work_item: str | None = None
    active_topic: str | None = None
    active_task: str | None = None

    sources: list[ActiveWorkSource] = field(default_factory=list)
    conflicts: list[ActiveWorkConflict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "workspace_root": self.workspace_root,
            "memory_root": self.memory_root,
            "active_work_item": self.active_work_item,
            "active_topic": self.active_topic,
            "active_task": self.active_task,
            "sources": [s.to_dict() for s in self.sources],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "warnings": self.warnings,
        }

    def to_legacy_active_project_result(self) -> "ActiveProjectResult":
        """Compatibility wrapper: produce old-shaped result."""
        # Legacy "project" value: prefer active_work_item, then active_topic, then null.
        # NEVER fall back to workspace_root here (prevents old bug).
        legacy_value = self.active_work_item or self.active_topic
        legacy_source = None
        legacy_ev = None

        # Find the actual source that provided the chosen legacy value so we can preserve its name
        for s in self.sources:
            awi = s.get("active_work_item")
            if awi and awi.value == legacy_value:
                legacy_source = s.source
                legacy_ev = awi.evidence
                break
            at = s.get("active_topic")
            if at and at.value == legacy_value:
                legacy_source = s.source
                legacy_ev = at.evidence
                break

        if legacy_source is None:
            if self.active_work_item:
                legacy_source = "active_work_item"
            elif self.active_topic:
                legacy_source = "active_topic"

        ap_sources: list["ActiveProjectSource"] = []
        for s in self.sources:
            # For legacy surface, emit one "project" value per source if possible
            val = None
            conf = "low"
            ev = None
            awi = s.get("active_work_item")
            if awi:
                val = awi.value
                conf = awi.confidence
                ev = awi.evidence
            else:
                at = s.get("active_topic")
                if at:
                    val = at.value
                    conf = at.confidence
                    ev = at.evidence
            # Do NOT treat workspace_root as legacy project value.
            ap_sources.append(ActiveProjectSource(
                source=s.source,
                project=val,
                raw_value=val,
                confidence=conf,
                path=s.path,
                evidence=ev,
            ))

        conflicts = []
        for c in self.conflicts:
            if c.semantic_field in ("active_work_item", "active_topic"):
                conflicts.append(ActiveProjectConflict(
                    type="active_project_mismatch",
                    sources=c.sources,
                    severity=c.severity,
                    message=c.message,
                    source_details=c.source_details,
                    suggestion=c.suggestion,
                ))

        return ActiveProjectResult(
            latest_project=legacy_value,
            selected_source=legacy_source,
            active_projects_by_source={s.source: (s.project if s.project else None) for s in ap_sources},
            sources=ap_sources,
            conflicts=conflicts,
            warnings=self.warnings,
        )


# ---------------------------------------------------------------------------
# Legacy ActiveProjectSource kept for compatibility (used by wrapper and old code)
# ---------------------------------------------------------------------------

@dataclass
class ActiveProjectSource:
    source: str
    project: str | None
    raw_value: str | None
    confidence: str  # "high", "medium", or "low"
    path: str | None = None
    evidence: str | None = None

    @property
    def value(self) -> str | None:
        return self.project

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source": self.source,
            "value": self.project,
            "project": self.project,
            "confidence": self.confidence,
            "path": self.path,
            "evidence": self.evidence,
        }


@dataclass
class ActiveProjectConflict:
    type: str  # "active_project_mismatch"
    sources: list[str]
    severity: str  # "error" or "warning"
    message: str = "Active-project sources disagree."
    source_details: dict[str, dict[str, str | None]] = field(default_factory=dict)
    suggestion: str = "Inspect session handoff and runtime context before continuing current-project work."

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "sources": self.source_details,
            "suggestion": self.suggestion,
        }


@dataclass
class ActiveProjectResult:
    latest_project: str | None
    selected_source: str | None
    active_projects_by_source: dict[str, str | None]
    sources: list[ActiveProjectSource]
    conflicts: list[ActiveProjectConflict]
    warnings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "selected_project": self.latest_project,
            "latest_project": self.latest_project,
            "selected_source": self.selected_source,
            "projects_by_source": self.active_projects_by_source,
            "active_projects_by_source": self.active_projects_by_source,
            "sources": [s.to_dict() for s in self.sources],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# Continuation prompt detection
# ---------------------------------------------------------------------------

CONTINUATION_TRIGGERS = frozenset({
    "continue", "resume", "what next", "what now",
    "what did we do", "what have we done", "what is the current state",
    "where were we", "pick up where we left", "session start",
})


def is_continuation_prompt(task: str) -> bool:
    normalized = task.strip().casefold()
    for trigger in CONTINUATION_TRIGGERS:
        if trigger in normalized:
            return True
    return False


# ---------------------------------------------------------------------------
# Safe file readers
# ---------------------------------------------------------------------------

def _read_text_safe(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _read_json_safe(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl_tail(path: Path, n: int) -> list[str]:
    try:
        with path.open(encoding="utf-8") as f:
            lines = f.readlines()
        return [l for l in (lines[-n:] if len(lines) > n else lines) if l.strip()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Project identity normalization
# ---------------------------------------------------------------------------

def _normalize_project_identity(value: str | None) -> str:
    if not value:
        return ""
    v = value.strip()
    if not v:
        return ""
    v = os.path.expanduser(v)
    if v.startswith("/"):
        try:
            v = str(Path(v).resolve())
        except Exception:
            pass
    if len(v) > 1 and v.endswith("/"):
        v = v.rstrip("/")
    if not v.startswith("/") and not v.startswith("~"):
        v = " ".join(v.split())
    return v


def _projects_match(p1: str | None, p2: str | None) -> bool:
    if p1 is None or p2 is None:
        return False
    n1 = _normalize_project_identity(p1)
    n2 = _normalize_project_identity(p2)
    if not n1 or not n2:
        return False
    return n1 == n2


# ---------------------------------------------------------------------------
# Value shape classifier (existence is a signal, not a hard requirement)
# ---------------------------------------------------------------------------

_KNOWN_EXTENSIONS = {
    ".md", ".txt", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".rst", ".html", ".css",
}

def classify_active_work_value(
    value: str | None,
    *,
    workspace_root: Path | None = None,
) -> ActiveWorkField:
    """Classify a raw string into a semantic active-work field.

    Rules (shape first, existence as helpful signal only):
    - absolute path ending exactly in .oem (or under .oem) -> memory_root
    - absolute dir that looks like repo root (has .git or .oem, or is workspace_root) -> workspace_root
    - absolute path under workspace_root (or relative with / or known extension) -> active_work_item
    - value ending in known doc/code extension -> active_work_item
    - short noun-phrase like title -> active_topic
    - imperative sentence / verb phrase -> active_task
    - otherwise unknown
    """
    if not value:
        return "unknown"
    v = value.strip()
    if not v:
        return "unknown"

    v_exp = os.path.expanduser(v)
    is_abs = v_exp.startswith("/")

    # Normalize for comparisons
    try:
        v_norm = str(Path(v_exp).resolve()) if is_abs else v_exp
    except Exception:
        v_norm = v_exp
    if len(v_norm) > 1 and v_norm.endswith("/"):
        v_norm = v_norm.rstrip("/")

    # 1. memory_root: absolute path ending in .oem
    if is_abs and (v_norm.endswith("/.oem") or v_norm.endswith(".oem")):
        return "memory_root"

    # 2. workspace_root signals
    if is_abs:
        # explicit workspace root if matches provided
        if workspace_root:
            try:
                if Path(v_norm) == Path(workspace_root).resolve():
                    return "workspace_root"
            except Exception:
                pass
        # looks like repo root
        p = Path(v_norm)
        if p.is_dir() or True:  # even if not exist yet
            try:
                if (p / ".git").exists() or (p / ".oem").exists():
                    return "workspace_root"
            except Exception:
                pass
            # If it is a plausible root (no obvious file extension, not ending in known item)
            if not any(v_norm.endswith(ext) for ext in _KNOWN_EXTENSIONS):
                # Heuristic: absolute path without file extension often a root
                # But only if it has typical project signals or is the workspace_root parent
                pass

    # 3. active_work_item: relative path with slash or known extension, or absolute under workspace
    has_slash = "/" in v or "\\" in v
    ends_with_ext = any(v.lower().endswith(ext) for ext in _KNOWN_EXTENSIONS)

    if is_abs and workspace_root:
        try:
            if Path(v_norm).is_relative_to(Path(workspace_root).resolve()):
                return "active_work_item"
        except Exception:
            pass

    if (not is_abs and (has_slash or ends_with_ext)) or ends_with_ext:
        return "active_work_item"

    # 4. active_task: looks like an imperative / sentence with verb
    # Simple heuristic: contains common action verbs or ends with action-like
    lower = v.lower()
    action_verbs = ("polish", "fix", "write", "edit", "continue", "implement", "add", "remove",
                    "refactor", "update", "test", "review", "tighten", "clean", "investigate")
    if any(verb in lower for verb in action_verbs) or (len(v.split()) >= 3 and any(lower.startswith(vb) for vb in action_verbs)):
        return "active_task"

    # 5. active_topic: short noun phrase (no verb, no path chars)
    # Heuristic: relatively short, no path separators, no sentence punctuation at end
    if not has_slash and not ends_with_ext:
        # strip trailing punctuation
        clean = re.sub(r"[.!?]+$", "", v).strip()
        if len(clean.split()) <= 8 and not any(ch in clean for ch in ":/\\"):
            # If it looks like a title (capitalized words or kebab)
            if re.search(r"[A-Z]", clean) or "-" in clean or len(clean.split()) >= 2:
                return "active_topic"
            # fallback: treat short descriptive as topic
            if len(clean) <= 80:
                return "active_topic"

    # 6. absolute path that looks like a file path even without existence
    if is_abs and (has_slash or ends_with_ext):
        return "active_work_item"

    return "unknown"


def _normalize_path_for_comparison(p: str | None) -> str:
    if not p:
        return ""
    v = os.path.expanduser(p.strip())
    if not v:
        return ""
    if len(v) > 1 and v.endswith("/"):
        v = v.rstrip("/")
    if v.startswith("/"):
        try:
            v = str(Path(v).resolve())
        except Exception:
            pass
    return v


# ---------------------------------------------------------------------------
# Per-source project parsers
# ---------------------------------------------------------------------------

_EXPLICIT_PROJECT_MARKERS = [
    ("Active project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+project\s*:\s*(.+?)\s*$")),
    ("Open project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Open\s+project\s*:\s*(.+?)\s*$")),
    ("Current project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Current\s+project\s*:\s*(.+?)\s*$")),
    ("Project root:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s+root\s*:\s*(.+?)\s*$")),
    ("Project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s*:\s*(.+?)\s*$")),
    ("Memory root:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Memory\s+root\s*:\s*(.+?)\s*$")),
    ("Primary file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Primary\s+file\s*:\s*(.+?)\s*$")),
    ("Current file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Current\s+file\s*:\s*(.+?)\s*$")),
]
_OPEN_PROJECT_SENTENCE = re.compile(r"(?im)^\s*(?:[-*]\s+)?(.+?)\s+is\s+the\s+open\s+project\s*\.?.*$")
_NEXT_ACTION = re.compile(r"(?im)^\s*(?:[-*]\s+)?Next\s+action\s*:\s*(.+?)\s*$")
_FILE_SIGNAL = re.compile(r"(?:^|\s)([\w./~-]+(?:/|\\)[\w./~-]+|[\w.-]+\.(?:md|txt|py|ts|tsx|js|jsx|json|yaml|yml))")
_CONSTITUTIVE_OPEN_CURRENT_SENTENCE = re.compile(r"(?im)^\s*(?:[-*]\s+)?(.+?)\s+is\s+the\s+(open|current)\s+project\s*\.?.*$")


def _is_conservative_project_identifier(value: str) -> bool:
    if not value or len(value) > 120:
        return False
    if "/" in value or "\\" in value:
        return True
    if any(value.lower().endswith(ext) for ext in _KNOWN_EXTENSIONS):
        return True
    if "-" in value or "_" in value:
        return True
    if re.search(r"[A-Z]", value) and re.search(r"[a-z]", value):
        return True
    return False


def _parse_project_from_markdown(text: str) -> tuple[str | None, str, str]:
    for evidence, pattern in _EXPLICIT_PROJECT_MARKERS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip(), "high", evidence

    m = _OPEN_PROJECT_SENTENCE.search(text)
    if m:
        return m.group(1).strip(), "high", "open_project_sentence"

    m = _NEXT_ACTION.search(text)
    if m:
        signal = _FILE_SIGNAL.search(m.group(1).strip())
        if signal:
            return signal.group(1).strip(), "medium", "Next action:"

    return None, "low", "no_explicit_project_marker"


def _parse_project_from_handoff_md(text: str) -> tuple[str | None, str]:
    project, confidence, _evidence = _parse_project_from_markdown(text)
    return project, confidence


def _parse_project_from_context_md(text: str) -> tuple[str | None, str]:
    project, confidence, _evidence = _parse_project_from_markdown(text)
    return project, confidence


def _parse_project_from_handoff_json(data: Any) -> tuple[str | None, str, str | None]:
    if not isinstance(data, dict):
        return None, "low", None
    project_root = data.get("project_root")
    if project_root and isinstance(project_root, str):
        return project_root.strip(), "high", "project_root"
    project_label = data.get("project_label")
    if project_label and isinstance(project_label, str):
        return project_label.strip(), "high", "project_label"
    project = data.get("project")
    if project and isinstance(project, str):
        return project.strip(), "high", "project"
    return None, "low", None


def _parse_project_from_outcome(record: dict) -> tuple[str | None, str]:
    project = record.get("project")
    if project and isinstance(project, str):
        return project.strip(), "high"
    project_root = record.get("project_root")
    if project_root and isinstance(project_root, str):
        return project_root.strip(), "high"
    return None, "low"


def _parse_project_from_active_session(data: Any) -> tuple[str | None, str, str | None]:
    if not isinstance(data, dict):
        return None, "low", None
    project = data.get("project")
    if project and isinstance(project, str):
        return project.strip(), "high", "project"
    project_root = data.get("project_root")
    if project_root and isinstance(project_root, str):
        return project_root.strip(), "high", "project_root"
    return None, "low", None


# ---------------------------------------------------------------------------
# Field-aware parsers (new model)
# ---------------------------------------------------------------------------

_EXPLICIT_SEMANTIC_MARKERS = [
    # workspace
    ("Workspace root:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Workspace\s+root\s*:\s*(.+?)\s*$")),
    ("Project root:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s+root\s*:\s*(.+?)\s*$")),
    # memory
    ("Memory root:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Memory\s+root\s*:\s*(.+?)\s*$")),
    # active work item
    ("Active work item:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+work\s+item\s*:\s*(.+?)\s*$")),
    ("Active file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+file\s*:\s*(.+?)\s*$")),
    ("Open file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Open\s+file\s*:\s*(.+?)\s*$")),
    ("Current file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Current\s+file\s*:\s*(.+?)\s*$")),
    ("Primary file:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Primary\s+file\s*:\s*(.+?)\s*$")),
    # legacy that may map to active_work_item or topic
    ("Active project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+project\s*:\s*(.+?)\s*$")),
    ("Open project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Open\s+project\s*:\s*(.+?)\s*$")),
    ("Current project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Current\s+project\s*:\s*(.+?)\s*$")),
    ("Project:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Project\s*:\s*(.+?)\s*$")),
    # topic
    ("Active topic:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+topic\s*:\s*(.+?)\s*$")),
    ("Current topic:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Current\s+topic\s*:\s*(.+?)\s*$")),
    # task / next action
    ("Active task:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Active\s+task\s*:\s*(.+?)\s*$")),
    ("Next action:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Next\s+action\s*:\s*(.+?)\s*$")),
    # primary objective → classified by shape (topic, item, or task)
    ("Primary objective:", re.compile(r"(?im)^\s*(?:[-*]\s+)?Primary\s+objective\s*:\s*(.+?)\s*$")),
]

def _parse_semantic_fields_from_text(text: str, workspace_root: Path | None = None) -> dict[ActiveWorkField, ActiveWorkFieldValue]:
    """Parse explicit semantic markers into field->value map. Confidence per field."""
    result: dict[ActiveWorkField, ActiveWorkFieldValue] = {}
    for evidence, pattern in _EXPLICIT_SEMANTIC_MARKERS:
        m = pattern.search(text)
        if not m:
            continue
        raw = m.group(1).strip()
        if not raw:
            continue
        ev_lower = evidence.lower()
        # Special handling for Next action: extract file signal for active_work_item (medium)
        if ev_lower.startswith("next action"):
            signal = _FILE_SIGNAL.search(raw)
            if signal:
                pathish = signal.group(1).strip()
                result["active_work_item"] = ActiveWorkFieldValue(value=pathish, confidence="medium", evidence="Next action:")
            # also keep full phrase as task
            result["active_task"] = ActiveWorkFieldValue(value=raw, confidence="medium", evidence=evidence)
            continue

        # Explicit semantic markers are authoritative. Shape heuristics are only
        # for legacy labels like "Project:" where the field is ambiguous.
        if ev_lower.startswith(("workspace root", "project root")):
            result["workspace_root"] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue
        if ev_lower.startswith("memory root"):
            result["memory_root"] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue
        if ev_lower.startswith(("active work item", "active file", "open file", "current file", "primary file")):
            result["active_work_item"] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue
        if ev_lower.startswith(("active topic", "current topic")):
            result["active_topic"] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue
        if ev_lower.startswith("active task"):
            result["active_task"] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue

        if ev_lower.startswith("primary objective"):
            fld = classify_active_work_value(raw, workspace_root=workspace_root)
            if fld == "unknown":
                fld = "active_topic"
            result[fld] = ActiveWorkFieldValue(value=raw, confidence="high", evidence=evidence)
            continue

        # Classify by shape (existence not required)
        fld = classify_active_work_value(raw, workspace_root=workspace_root)
        if fld == "unknown":
            # Try to be more generous for legacy markers
            if ev_lower.startswith(("active project", "open project", "current project", "project:")):
                fld = classify_active_work_value(raw, workspace_root=workspace_root)
                if fld == "unknown":
                    fld = "active_work_item" if ("/" in raw or raw.endswith(".md")) else "active_topic"
            else:
                continue
        conf = "high"
        if "next action" in ev_lower:
            conf = "medium"
        result[fld] = ActiveWorkFieldValue(value=raw, confidence=conf, evidence=evidence)
    # Fallback: open/current project sentence (conservative, only if no explicit markers matched)
    _active_fields = {f for f in result if f != "unknown"}
    if not _active_fields:
        m = _CONSTITUTIVE_OPEN_CURRENT_SENTENCE.search(text)
        if m:
            raw = m.group(1).strip()
            if raw and _is_conservative_project_identifier(raw):
                fld = classify_active_work_value(raw, workspace_root=workspace_root)
                if fld in ("active_work_item", "active_topic", "active_task"):
                    result[fld] = ActiveWorkFieldValue(value=raw, confidence="medium", evidence="open_project_sentence")
    return result


def _parse_fields_from_handoff_json(data: Any, workspace_root: Path | None = None) -> dict[ActiveWorkField, ActiveWorkFieldValue]:
    if not isinstance(data, dict):
        return {}
    out: dict[ActiveWorkField, ActiveWorkFieldValue] = {}
    # Preferred explicit fields
    preferred: list[tuple[str, ActiveWorkField]] = [
        ("workspace_root", "workspace_root"),
        ("memory_root", "memory_root"),
        ("active_work_item", "active_work_item"),
        ("active_topic", "active_topic"),
        ("active_task", "active_task"),
    ]
    for key, fld in preferred:
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            out[fld] = ActiveWorkFieldValue(value=val.strip(), confidence="high", evidence=key)

    # Legacy project_root / project_label / project -> classify
    for legacy_key in ("project_root", "project_label", "project"):
        val = data.get(legacy_key)
        if isinstance(val, str) and val.strip():
            fld = classify_active_work_value(val, workspace_root=workspace_root)
            if fld != "unknown" and fld not in out:
                out[fld] = ActiveWorkFieldValue(value=val.strip(), confidence="high", evidence=legacy_key)
    return out


def _parse_fields_from_active_session(data: Any, workspace_root: Path | None = None) -> dict[ActiveWorkField, ActiveWorkFieldValue]:
    if not isinstance(data, dict):
        return {}
    out: dict[ActiveWorkField, ActiveWorkFieldValue] = {}
    for key in ("project_root", "project"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            fld = classify_active_work_value(val, workspace_root=workspace_root)
            # Special rule: active_session "project" is almost always workspace_root
            if fld == "unknown" or fld in ("active_work_item", "active_topic"):
                # Force to workspace_root for this source
                fld = "workspace_root"
            out[fld] = ActiveWorkFieldValue(value=val.strip(), confidence="high", evidence=key)
    return out


# ---------------------------------------------------------------------------
# Canonical field-aware resolver
# ---------------------------------------------------------------------------

SOURCE_HANDOFF_JSON = "session_handoff_json"
SOURCE_HANDOFF_MD = "session_handoff_md"
SOURCE_STATE_HANDOFF_MD = "state_session_handoff_md"
SOURCE_RUNTIME_CONTEXT = "runtime_context_md"
SOURCE_LATEST_OUTCOME = "latest_outcome"
SOURCE_ACTIVE_SESSION = "active_session_json"


def resolve_active_work_identity(memory_root: Path) -> ActiveWorkIdentity:
    """New canonical resolver. Returns field-aware ActiveWorkIdentity."""
    ws_root: Path | None = None
    try:
        ws_root = memory_root.parent if memory_root.name == ".oem" else memory_root
    except Exception:
        ws_root = None

    collected: dict[str, list[tuple[str, ActiveWorkFieldValue, str | None]]] = {
        f: [] for f in ["workspace_root", "memory_root", "active_work_item", "active_topic", "active_task"]
    }
    warnings: list[dict] = []
    sources_list: list[ActiveWorkSource] = []

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(memory_root))
        except ValueError:
            return str(path)

    # 1. session-handoff.json
    jpath = memory_root / "session-handoff.json"
    jdata = _read_json_safe(jpath)
    if jdata is not None:
        fields = _parse_fields_from_handoff_json(jdata, workspace_root=ws_root)
        src = ActiveWorkSource(source=SOURCE_HANDOFF_JSON, path=rel(jpath), fields=fields)
        sources_list.append(src)
        for f, fv in fields.items():
            collected[f].append((SOURCE_HANDOFF_JSON, fv, rel(jpath)))
    elif jpath.exists():
        warnings.append({"reason": "malformed_handoff_json", "severity": "warning", "path": rel(jpath)})

    # 2. session-handoff.md
    for src_name, mdp in [
        (SOURCE_HANDOFF_MD, memory_root / "session-handoff.md"),
        (SOURCE_STATE_HANDOFF_MD, memory_root / "state" / "session-handoff.md"),
    ]:
        if mdp.exists():
            txt = _read_text_safe(mdp)
            if txt:
                fields = _parse_semantic_fields_from_text(txt, workspace_root=ws_root)
                src = ActiveWorkSource(source=src_name, path=rel(mdp), fields=fields)
                sources_list.append(src)
                for f, fv in fields.items():
                    collected[f].append((src_name, fv, rel(mdp)))

    # 3. runtime context
    cpath = memory_root / ".runtime" / "context.md"
    ctxt = _read_text_safe(cpath)
    if ctxt:
        fields = _parse_semantic_fields_from_text(ctxt, workspace_root=ws_root)
        src = ActiveWorkSource(source=SOURCE_RUNTIME_CONTEXT, path=rel(cpath), fields=fields)
        sources_list.append(src)
        for f, fv in fields.items():
            collected[f].append((SOURCE_RUNTIME_CONTEXT, fv, rel(cpath)))

    # 4. outcomes (best effort, usually low signal for work item)
    opath = memory_root / "state" / "outcomes.jsonl"
    recent = _read_jsonl_tail(opath, 3)
    if recent:
        for line in reversed(recent):
            try:
                rec = json.loads(line)
                for k in ("project", "project_root", "active_work_item"):
                    val = rec.get(k)
                    if isinstance(val, str) and val.strip():
                        fld = classify_active_work_value(val, workspace_root=ws_root)
                        if fld in collected:
                            collected[fld].append((SOURCE_LATEST_OUTCOME, ActiveWorkFieldValue(value=val.strip(), confidence="medium", evidence=k), rel(opath)))
                            break
                break
            except Exception:
                continue

    # 5. active_session.json -> workspace_root (per spec)
    apath = memory_root / "state" / "active_session.json"
    adata = _read_json_safe(apath)
    if adata is not None:
        fields = _parse_fields_from_active_session(adata, workspace_root=ws_root)
        src = ActiveWorkSource(source=SOURCE_ACTIVE_SESSION, path=rel(apath), fields=fields)
        sources_list.append(src)
        for f, fv in fields.items():
            collected[f].append((SOURCE_ACTIVE_SESSION, fv, rel(apath)))

    # Build final identity by taking highest-confidence per field
    final: dict[str, str | None] = {f: None for f in ["workspace_root", "memory_root", "active_work_item", "active_topic", "active_task"]}

    def _pick(values: list[tuple[str, ActiveWorkFieldValue, str | None]]) -> ActiveWorkFieldValue | None:
        if not values:
            return None
        # Prefer high, then medium; first wins on tie
        ordered = sorted(values, key=lambda t: ({"high": 3, "medium": 2, "low": 1}.get(t[1].confidence, 0)), reverse=True)
        return ordered[0][1]

    for f in list(final.keys()):
        picked = _pick(collected[f])
        if picked and picked.value:
            final[f] = picked.value

    # Conflict detection per field
    conflicts: list[ActiveWorkConflict] = []
    for f in ["workspace_root", "memory_root", "active_work_item", "active_topic", "active_task"]:
        high_vals: dict[str, str] = {}
        all_details: dict[str, dict[str, str | None]] = {}
        for src_name, fv, pth in collected[f]:
            if not fv.value:
                continue
            norm = _normalize_path_for_comparison(fv.value) if f in ("workspace_root", "memory_root", "active_work_item") else fv.value.strip()
            all_details[src_name] = {"value": fv.value, "path": pth, "confidence": fv.confidence, "evidence": fv.evidence}
            if fv.confidence == "high":
                if norm not in high_vals:
                    high_vals[norm] = src_name
                elif high_vals[norm] != src_name:
                    # conflict
                    pass
        if len(high_vals) >= 3:
            conflicts.append(ActiveWorkConflict(
                semantic_field=f,  # type: ignore[arg-type]
                type=f"{f}_mismatch",
                sources=list(high_vals.values()),
                severity="error",
                message=f"Three or more high-confidence {f} sources disagree.",
                source_details={k: all_details.get(k, {}) for k in high_vals.values() if k in all_details},
            ))
        elif len(high_vals) >= 2:
            conflicts.append(ActiveWorkConflict(
                semantic_field=f,  # type: ignore[arg-type]
                type=f"{f}_mismatch",
                sources=list(high_vals.values()),
                severity="warning",
                message=f"High-confidence {f} sources disagree.",
                source_details={k: all_details.get(k, {}) for k in high_vals.values() if k in all_details},
            ))

    # Cross-field markdown source disagreement (warning-level only)
    _md_sources = [s for s in sources_list if s.source in (SOURCE_HANDOFF_MD, SOURCE_STATE_HANDOFF_MD, SOURCE_RUNTIME_CONTEXT)]
    _md_with_fields = [s for s in _md_sources if any(f in s.fields for f in ("active_work_item", "active_topic", "active_task"))]
    if len(_md_with_fields) >= 2:
        for i in range(len(_md_with_fields)):
            for j in range(i + 1, len(_md_with_fields)):
                s1, s2 = _md_with_fields[i], _md_with_fields[j]

                def _ctx_map(s):
                    out = {}
                    for f in ("active_work_item", "active_topic", "active_task"):
                        fv = s.fields.get(f)
                        if fv and fv.value:
                            out[f] = fv.value
                    return out

                ctx1 = _ctx_map(s1)
                ctx2 = _ctx_map(s2)
                if not ctx1 or not ctx2:
                    continue
                shared = set(ctx1.keys()) & set(ctx2.keys())
                if shared and all(ctx1[f] == ctx2[f] for f in shared):
                    continue
                if not any(f in ctx1 for f in ("active_work_item", "active_topic")):
                    continue
                if not any(f in ctx2 for f in ("active_work_item", "active_topic")):
                    continue
                if any(c for c in conflicts if c.type == "active_work_source_disagreement" and set(c.sources) == {s1.source, s2.source}):
                    continue
                conflicts.append(ActiveWorkConflict(
                    semantic_field="unknown",
                    type="active_work_source_disagreement",
                    sources=[s1.source, s2.source],
                    severity="warning",
                    message="Markdown sources point to different active work context.",
                    source_details={s1.source: ctx1, s2.source: ctx2},
                ))

    identity = ActiveWorkIdentity(
        workspace_root=final["workspace_root"],
        memory_root=final["memory_root"],
        active_work_item=final["active_work_item"],
        active_topic=final["active_topic"],
        active_task=final["active_task"],
        sources=sources_list,
        conflicts=conflicts,
        warnings=warnings,
    )
    return identity


# ---------------------------------------------------------------------------
# Legacy resolve_active_project kept as thin wrapper (deprecated for new code)
# ---------------------------------------------------------------------------

def resolve_active_project(memory_root: Path) -> ActiveProjectResult:
    """Deprecated compatibility wrapper. New code should call resolve_active_work_identity."""
    ident = resolve_active_work_identity(memory_root)
    return ident.to_legacy_active_project_result()


# ---------------------------------------------------------------------------
# Legacy resolve_active_work (updated to use new resolver for contradictions)
# ---------------------------------------------------------------------------

def resolve_active_work(memory_root: Path) -> ActiveWorkResult:
    items: list[ActiveWorkItem] = []

    # 1. todos.json — high signal
    todos_path = memory_root / "state" / "todos.json"
    todos_data = _read_json_safe(todos_path)
    if isinstance(todos_data, list):
        for t in todos_data:
            if not isinstance(t, dict):
                continue
            content = str(t.get("content", "unknown"))
            status = str(t.get("status", "")).casefold()
            if status == "in_progress":
                items.append(ActiveWorkItem(
                    source="todos.json",
                    key=content,
                    detail=f"In-progress: {content}",
                    score=3.0,
                ))
            elif status == "pending":
                items.append(ActiveWorkItem(
                    source="todos.json",
                    key=content,
                    detail=f"Pending: {content}",
                    score=1.0,
                ))

    # 2. context.md — medium signal
    context_path = memory_root / ".runtime" / "context.md"
    context_text = _read_text_safe(context_path)
    context_topic: str | None = None
    if context_text:
        for line in context_text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("*"):
                context_topic = stripped[:120]
                items.append(ActiveWorkItem(
                    source="context.md",
                    key=context_topic,
                    detail=stripped[:200],
                    score=2.0,
                ))
                break

    # 3. session-handoff.md — check root level first, then state level
    handoff_path = memory_root / "session-handoff.md"
    handoff_text = _read_text_safe(handoff_path)
    if not handoff_text:
        handoff_path = memory_root / "state" / "session-handoff.md"
        handoff_text = _read_text_safe(handoff_path)
    handoff_topic: str | None = None
    if handoff_text:
        first_line = handoff_text.strip().split("\n", 1)[0][:80]
        handoff_topic = first_line
        items.append(ActiveWorkItem(
            source="session-handoff.md",
            key=first_line,
            detail=handoff_text[:200].strip(),
            score=2.5,
        ))

    # 4. events.jsonl tail — low signal
    events_path = memory_root / "events" / "events.jsonl"
    recent = _read_jsonl_tail(events_path, 5)
    if recent:
        for line in reversed(recent):
            try:
                ev = json.loads(line)
                event_type = ev.get("type", ev.get("event", "unknown"))
                items.append(ActiveWorkItem(
                    source="events.jsonl",
                    key=event_type,
                    detail=f"Recent event: {event_type}",
                    score=0.5,
                ))
            except json.JSONDecodeError:
                pass

    # 5. outcomes.jsonl tail — low signal
    outcomes_path = memory_root / "state" / "outcomes.jsonl"
    recent_outcomes = _read_jsonl_tail(outcomes_path, 3)
    if recent_outcomes:
        items.append(ActiveWorkItem(
            source="outcomes.jsonl",
            key=f"{len(recent_outcomes)} recent outcomes",
            detail="Recent outcomes recorded",
            score=1.0,
        ))

    # Contradiction detection uses the field-aware resolver.
    contradictions: list[str] = []
    ident = resolve_active_work_identity(memory_root)
    for c in ident.conflicts:
        contradictions.append(
            f"{c.type}: {', '.join(c.sources)} (severity: {c.severity})"
        )
    for w in ident.warnings:
        message = w.get("message", str(w)) if isinstance(w, dict) else str(w)
        if message not in contradictions:
            contradictions.append(message)

    has_active_work = len(items) > 0
    score = min(sum(item.score for item in items), 10.0)

    return ActiveWorkResult(
        has_active_work=has_active_work,
        items=items,
        contradictions=contradictions,
        score=score,
    )


# ---------------------------------------------------------------------------
# Repair: smallest safe active-work repair (dry-run / apply)
# ---------------------------------------------------------------------------

import datetime as _dt


def _active_work_backup_dir(harness: Path, timestamp: str) -> Path:
    # Use .oem/.backups/active-work-repair/<timestamp>/ per spec
    return harness / ".backups" / "active-work-repair" / timestamp


def repair_active_work(
    memory_root: Path,
    *,
    dry_run: bool = True,
    apply: bool = False,
    backup: bool | None = None,
) -> dict:
    """Smallest safe active-work repair.

    - Detects via resolve_active_work_identity
    - Backs up before mutation (if apply and backup is not False)
    - Updates session-handoff.json to explicit canonical fields
    - Neutralizes stale active-work claims in session-handoff.md
    - Preserves outcomes.jsonl (no rewrite)
    - Returns structured report with planned/applied changes
    """
    if dry_run and apply:
        raise ValueError("dry_run and apply are mutually exclusive")
    if backup is False and not apply:
        raise ValueError("--no-backup is only valid with --apply")

    ident = resolve_active_work_identity(memory_root)
    conflicts = ident.conflicts or []

    # Determine if we have a stale/active-work conflict to repair
    has_conflict = any(
        c.type in ("active_topic_mismatch", "active_work_item_mismatch", "active_work_source_disagreement")
        or "active_project_mismatch" in str(c.type)
        for c in conflicts
    )

    ws = ident.workspace_root
    target_active_work_item = None
    for source in ident.sources:
        if source.source == SOURCE_RUNTIME_CONTEXT:
            field = source.get("active_work_item")
            if field and field.value:
                target_active_work_item = field.value
                break
    if not target_active_work_item:
        target_active_work_item = ident.active_work_item

    target_active_topic = None
    for source in ident.sources:
        if source.source == SOURCE_RUNTIME_CONTEXT:
            field = source.get("active_topic")
            if field and field.value:
                target_active_topic = field.value
                break

    target_active_task = None
    for source in ident.sources:
        if source.source == SOURCE_RUNTIME_CONTEXT:
            field = source.get("active_task")
            if field and field.value:
                target_active_task = field.value
                break

    report: dict = {
        "status": "noop",
        "mode": "dry_run" if (dry_run or not apply) else "apply",
        "memory_root": str(memory_root),
        "workspace_root": ws,
        "detected_conflicts": [c.to_dict() for c in conflicts],
        "planned_changes": [],
        "changes_applied": [],
        "backup_dir": None,
    }

    if not target_active_work_item:
        report["status"] = "unsupported_repair_case"
        report["reason"] = "no_runtime_context_active_work_item"
        return report

    now_iso = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    harness = memory_root if memory_root.name == ".oem" else memory_root.parent / ".oem"

    # 1) session-handoff.json: ensure explicit fields, no project_label as active work
    jpath = harness / "session-handoff.json"
    current_json = {}
    if jpath.exists():
        try:
            current_json = _read_json_safe(jpath) or {}
        except Exception:
            current_json = {}

    planned_json: dict = {
        "schema_version": "1.0.0",
        "workspace_root": ws or current_json.get("workspace_root") or current_json.get("project_root"),
        "memory_root": current_json.get("memory_root"),
        "active_work_item": target_active_work_item,
        "active_topic": target_active_topic,
        "active_task": target_active_task,
        "updated_at": now_iso,
        "source_session_id": current_json.get("source_session_id") or current_json.get("source_session") or "",
        "status": "active",
        "primary_objective": current_json.get("primary_objective", ""),
        "next_action": current_json.get("next_action", ""),
        "previous": current_json.get("previous", {}),
    }
    # Strip legacy fields that could be misinterpreted
    planned_json = {k: v for k, v in planned_json.items() if v is not None or k in ("active_work_item", "active_topic", "active_task")}
    # Remove any project_label if present (never treat as active work)
    if "project_label" in planned_json:
        del planned_json["project_label"]

    def _norm(d: dict) -> dict:
        return {k: (str(v) if isinstance(v, (Path,)) else v) for k, v in d.items() if v is not None or k in ("active_work_item", "active_topic", "active_task")}

    def _semantic_norm(d: dict) -> dict:
        out = _norm(d)
        out.pop("updated_at", None)
        return out

    if _semantic_norm(current_json) != _semantic_norm(planned_json):
        report["planned_changes"].append({"file": str(jpath), "action": "update", "before": _norm(current_json), "after": _norm(planned_json)})

    # 2) session-handoff.md: neutralize stale active-work claims while preserving history
    md_candidates = [
        harness / "session-handoff.md",
        harness / "state" / "session-handoff.md",
    ]
    for mdp in md_candidates:
        if not mdp.exists():
            continue
        txt = _read_text_safe(mdp) or ""
        # Remove or comment out active project/topic lines that contradict canonical state
        # Keep historical context under a "Historical Context" heading
        new_lines = []
        for line in txt.splitlines():
            stripped = line.strip()
            lower = line.lower()
            if stripped.lower().startswith("# historical:"):
                new_lines.append(line)
                continue
            # Detect and suppress stale active-work claims
            if any(k in lower for k in ["primary objective:", "active project:", "open project:", "current project:", "project:"]):
                # Keep the line but mark as historical
                new_lines.append("# Historical: " + line.lstrip())
                continue
            new_lines.append(line)
        new_txt = "\n".join(new_lines).rstrip() + "\n"
        if new_txt != txt:
            report["planned_changes"].append({"file": str(mdp), "action": "neutralize_stale_aw", "before_preview": txt[:200], "after_preview": new_txt[:200]})

    if dry_run or not apply:
        if report["planned_changes"]:
            report["status"] = "conflict_detected" if has_conflict else "repair_needed"
        else:
            report["status"] = "no_changes_needed"
        return report

    # Apply path
    backup_dir = None
    do_backup = backup is not False
    if do_backup:
        backup_dir = _active_work_backup_dir(harness, ts)
        backup_dir.mkdir(parents=True, exist_ok=True)

    changes: list[dict] = []

    # Backup first
    if do_backup and backup_dir:
        for change in report["planned_changes"]:
            src = Path(change["file"])
            if src.exists():
                rel = src.relative_to(harness) if src.is_relative_to(harness) else src.name
                dst = backup_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                except Exception:
                    pass
        report["backup_dir"] = str(backup_dir)

    # Apply JSON
    if any(c["file"].endswith("session-handoff.json") for c in report["planned_changes"]):
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(json.dumps(planned_json, indent=2) + "\n", encoding="utf-8")
        changes.append({"file": str(jpath), "action": "updated"})

    # Apply MD neutralization
    for mdp in md_candidates:
        if not mdp.exists():
            continue
        txt = _read_text_safe(mdp) or ""
        new_lines = []
        for line in txt.splitlines():
            stripped = line.strip()
            lower = line.lower()
            if stripped.lower().startswith("# historical:"):
                new_lines.append(line)
                continue
            if any(k in lower for k in ["primary objective:", "active project:", "open project:", "current project:", "project:"]):
                new_lines.append("# Historical: " + line.lstrip())
                continue
            new_lines.append(line)
        new_txt = "\n".join(new_lines).rstrip() + "\n"
        if new_txt != txt:
            mdp.write_text(new_txt, encoding="utf-8")
            changes.append({"file": str(mdp), "action": "neutralized_stale_aw"})

    report["changes_applied"] = changes
    report["status"] = "repaired" if changes else "ok"
    if apply and changes:
        from oem_knowledge.runtime.working_set import create_checkpoint
        try:
            create_checkpoint(reason="health_repair", project=str(memory_root))
        except Exception:
            pass
    return report
