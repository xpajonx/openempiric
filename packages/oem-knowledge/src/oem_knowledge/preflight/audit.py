from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .models import PreflightResult
from ..project_layout import ProjectLayout


def compute_task_hash(task: str) -> str:
    normalized = " ".join((task or "").casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_audit_event(result: PreflightResult) -> dict[str, object]:
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_hash": compute_task_hash(result.task),
        "decision": result.decision,
        "matched_skill_ids": [match.id for match in result.matched_skills if match.id],
        "matched_concept_ids": [match.id for match in result.matched_concepts if match.id],
        "project_root": result.project_root,
    }


def write_audit_event(layout: ProjectLayout, result: PreflightResult) -> None:
    event = build_audit_event(result)
    audit_path = layout.preflight_events_path
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")

