from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

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
        "rejected_memory_count": result.rejected_memory_count,
        "rejection_reasons": result.rejection_reasons,
    }


def write_audit_event(layout: ProjectLayout, result: PreflightResult) -> None:
    event = build_audit_event(result)
    audit_path = layout.preflight_events_path
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def summarize_audit_events(path: Path, max_lines: int = 1_000_000) -> dict[str, object]:
    """Return a bounded, read-only aggregate of the preflight audit stream."""
    empty = {
        "exists": False, "event_count": 0, "decision_counts": {},
        "rejection_reason_counts": {}, "rejected_memory_count": 0,
        "empty_line_count": 0, "malformed_line_count": 0,
        "first_timestamp": None, "latest_timestamp": None, "truncated": False,
    }
    if not path.exists():
        return empty
    decisions: dict[str, int] = {}
    reasons: dict[str, int] = {}
    timestamps: list[datetime] = []
    result = dict(empty)
    result["exists"] = True
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number > max_lines:
                result["truncated"] = True
                break
            if not line.strip():
                result["empty_line_count"] = int(result["empty_line_count"]) + 1
                continue
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                result["malformed_line_count"] = int(result["malformed_line_count"]) + 1
                continue
            if not isinstance(value, dict):
                result["malformed_line_count"] = int(result["malformed_line_count"]) + 1
                continue
            result["event_count"] = int(result["event_count"]) + 1
            decision = value.get("decision")
            key = decision if isinstance(decision, str) and decision.strip() else "unknown"
            decisions[key] = decisions.get(key, 0) + 1
            count = value.get("rejected_memory_count")
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                result["rejected_memory_count"] = int(result["rejected_memory_count"]) + count
            rejection_reasons = value.get("rejection_reasons")
            if isinstance(rejection_reasons, dict):
                for reason, amount in rejection_reasons.items():
                    if isinstance(reason, str) and isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
                        reasons[reason] = reasons.get(reason, 0) + amount
            stamp = value.get("timestamp")
            if isinstance(stamp, str):
                try:
                    parsed = datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
                    if parsed.tzinfo is not None:
                        timestamps.append(parsed)
                except ValueError:
                    pass
    result["decision_counts"] = dict(sorted(decisions.items()))
    result["rejection_reason_counts"] = dict(sorted(reasons.items()))
    if timestamps:
        result["first_timestamp"] = min(timestamps).isoformat()
        result["latest_timestamp"] = max(timestamps).isoformat()
    return result
