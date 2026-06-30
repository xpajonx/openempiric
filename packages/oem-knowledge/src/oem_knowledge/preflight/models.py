from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PreflightMatch:
    kind: str
    id: str | None
    title: str
    score: float
    reason: str
    source_path: str | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class PreflightResult:
    status: str
    operation: str
    project_root: str
    memory_root: str
    task: str
    decision: str
    reason: str
    matched_skills: list[PreflightMatch] = field(default_factory=list)
    matched_concepts: list[PreflightMatch] = field(default_factory=list)
    matched_memory: list[PreflightMatch] = field(default_factory=list)
    source_suggestions: list[PreflightMatch] = field(default_factory=list)
    matched_directives: list[dict] = field(default_factory=list)
    selected_workflow: dict | None = None
    context: str = ""
    warnings: list[str] = field(default_factory=list)
    active_project: dict | None = None
    matched_memory_summary: list[dict] = field(default_factory=list)
    reason_detail: str = ""
    supporting_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillMetadata:
    id: str | None
    title: str
    status: str
    source_path: str
    triggers: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    behavior: str = ""
    snippet: str | None = None


@dataclass(frozen=True)
class ConceptMetadata:
    id: str | None
    title: str
    status: str
    source_path: str | None
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    summary: str = ""


@dataclass(frozen=True)
class MemoryMetadata:
    id: str | None
    title: str
    source_path: str | None
    snippet: str | None = None

