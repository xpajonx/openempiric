"""Classify source files before admitting them to the knowledge index.

The classifier keeps OpenEmpiric-generated artifacts out of ingestion so that
runtime state, generated summaries, and materialized knowledge do not get fed
back into the corpus as if they were user-authored project evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class SourceType:
    """String constants describing known source categories."""

    USER_TRANSCRIPT = "user_transcript"
    AGENT_TRANSCRIPT = "agent_transcript"
    PROJECT_FILE = "project_file"
    OEM_WIKI = "oem_wiki"
    OEM_REGISTRY = "oem_registry"
    OEM_RUNTIME_LOG = "oem_runtime_log"
    OEM_SESSION_REPORT = "oem_session_report"
    OEM_HANDOFF = "oem_handoff"
    OEM_CONFIG = "oem_config"
    OEM_SKILL = "oem_skill"
    OEM_SKILL_CANDIDATE = "oem_skill_candidate"
    GENERATED_SUMMARY = "generated_summary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceClassification:
    """Result of classifying a source path and optional source content."""

    source_type: str
    ingestion_eligible: bool
    reason: str
    source_path: str | None = None


def _normalized_parts(path: str | Path | None) -> tuple[str | None, tuple[str, ...], str | None]:
    if path is None:
        return None, (), None

    source_path = str(path)
    posix_path = PurePosixPath(source_path.replace("\\", "/"))
    parts = posix_path.parts
    name = parts[-1] if parts else None
    return source_path, parts, name


def _parts_after_oem(parts: tuple[str, ...]) -> tuple[str, ...]:
    try:
        index = parts.index(".oem")
    except ValueError:
        return ()
    return parts[index + 1 :]


def _has_oem_metadata(content: str | None) -> bool:
    if not content:
        return False

    lowered = content.lower()
    return any(
        marker in lowered
        for marker in (
            "generated_by: openempiric",
            "source_type: oem_generated",
            "source_type: oem_skill_candidate",
            "source_type: oem_skill",
            "<!-- generated_by: openempiric -->",
        )
    )


def _classification(
    source_type: str,
    ingestion_eligible: bool,
    reason: str,
    source_path: str | None,
) -> SourceClassification:
    return SourceClassification(
        source_type=source_type,
        ingestion_eligible=ingestion_eligible,
        reason=reason,
        source_path=source_path,
    )


def classify_source(
    path: str | Path | None,
    content: str | None = None,
) -> SourceClassification:
    """Classify a path and report whether it can be ingested.

    OpenEmpiric-owned generated artifacts are intentionally marked ineligible.
    Ordinary project files are eligible unless their content contains explicit
    OpenEmpiric generated-source metadata.
    """

    source_path, parts, name = _normalized_parts(path)

    if path is None:
        return _classification(
            SourceType.UNKNOWN,
            False,
            "missing source path",
            source_path,
        )


    if len(parts) >= 2 and parts[-2:] == ("directives", "session-handoff.md"):
        return _classification(
            SourceType.OEM_HANDOFF,
            False,
            "OpenEmpiric handoff directive is generated operational context",
            source_path,
        )

    if name in {"AGENTS.generated.md", "memory-start.md"}:
        return _classification(
            SourceType.GENERATED_SUMMARY,
            False,
            "OpenEmpiric generated summary files are not ingestion sources",
            source_path,
        )

    oem_parts = _parts_after_oem(parts)
    if oem_parts:
        if oem_parts[0] == "wiki":
            return _classification(
                SourceType.OEM_WIKI,
                False,
                ".oem/wiki files are materialized knowledge outputs",
                source_path,
            )

        if oem_parts[0] == "skills":
            return _classification(
                SourceType.OEM_SKILL,
                False,
                ".oem/skills files are approved project skills",
                source_path,
            )

        if oem_parts[0] == "skill_candidates":
            return _classification(
                SourceType.OEM_SKILL_CANDIDATE,
                False,
                ".oem/skill_candidates files are proposed project skills",
                source_path,
            )

        if oem_parts == ("skill_promotions.jsonl",):
            return _classification(
                SourceType.OEM_CONFIG,
                False,
                "OpenEmpiric skill promotions log is an operational artifact",
                source_path,
            )

        if oem_parts[0] == "state":
            return _classification(
                SourceType.OEM_CONFIG,
                False,
                ".oem/state files are OpenEmpiric state/configuration",
                source_path,
            )

        if oem_parts[0] in {"session_reports", "reports"}:
            return _classification(
                SourceType.OEM_SESSION_REPORT,
                False,
                ".oem generated report files are not source evidence",
                source_path,
            )

        if oem_parts[0] in {".runtime", ".cache"}:
            return _classification(
                SourceType.OEM_RUNTIME_LOG,
                False,
                ".oem runtime/cache files are operational artifacts",
                source_path,
            )

        if oem_parts == ("runtime_events.jsonl",) or oem_parts == ("outcomes.jsonl",):
            return _classification(
                SourceType.OEM_RUNTIME_LOG,
                False,
                "OpenEmpiric runtime JSONL logs are operational artifacts",
                source_path,
            )

        if oem_parts == ("concept_registry.json",):
            return _classification(
                SourceType.OEM_REGISTRY,
                False,
                "OpenEmpiric concept registry is generated knowledge state",
                source_path,
            )

    if _has_oem_metadata(content):
        return _classification(
            SourceType.GENERATED_SUMMARY,
            False,
            "content metadata marks this source as OpenEmpiric generated",
            source_path,
        )

    return _classification(
        SourceType.PROJECT_FILE,
        True,
        "ordinary project file is eligible for ingestion",
        source_path,
    )


def is_ingestion_eligible(path: str | Path | None, content: str | None = None) -> bool:
    """Return whether a source should be ingested into the knowledge index."""

    return classify_source(path, content).ingestion_eligible
