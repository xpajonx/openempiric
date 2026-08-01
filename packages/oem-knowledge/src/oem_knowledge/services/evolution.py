"""Concept evolution: decay and automated promotion/demotion."""

import math
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any


def apply_decay(
    concepts: list[dict[str, Any]],
    half_life_days: float = 30.0,
    floor: int = 1,
) -> list[dict[str, Any]]:
    """Apply exponential confidence decay to concepts based on time since last evidence.

    decay_score = confidence * exp(-0.693 * days_since_last_evidence / half_life)

    Args:
        concepts: List of concept dicts with 'confidence', 'updated_at' fields
        half_life_days: Days after which confidence halves
        floor: Minimum confidence value (default 1)

    Returns:
        List of dicts with concept_id, old_confidence, new_confidence, decay_factor
    """
    now = time.time()
    half_life_seconds = half_life_days * 86400
    results = []

    for cdata in concepts:
        concept_id = cdata.get("concept_id", cdata.get("canonical_name", "unknown"))
        old_conf = cdata.get("confidence", 1)
        
        updated_at = cdata.get("updated_at", now)
        if isinstance(updated_at, str):
            try:
                # Handle ISO format
                dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                updated_at = dt.timestamp()
            except (ValueError, TypeError):
                updated_at = now

        age_seconds = max(0, now - float(updated_at))
        age_days = age_seconds / 86400.0

        decay_factor = math.exp(-0.693 * age_days / half_life_days)
        new_conf = max(floor, round(old_conf * decay_factor))
        new_conf = min(new_conf, 5)  # Clamp to 1-5

        if new_conf != old_conf:
            results.append({
                "concept_id": concept_id,
                "old_confidence": old_conf,
                "new_confidence": new_conf,
                "decay_factor": round(decay_factor, 4),
                "age_days": round(age_days, 2),
            })

    return results


def should_promote(
    cdata: dict[str, Any],
    evidence_threshold: int = 5,
    session_threshold: int = 3,
) -> tuple[bool, str]:
    """Check if a concept should be promoted from emerging to validated.

    Returns:
        (should_promote, reason)
    """
    current_status = cdata.get("status", "candidate")
    if current_status not in ("candidate", "emerging"):
        return (False, f"Already at {current_status}")

    evidence_count = cdata.get("evidence_count", 0)
    sessions = cdata.get("sessions", [])
    session_count = len(sessions) if isinstance(sessions, list) else cdata.get("session_count", 0)

    if evidence_count >= evidence_threshold and session_count >= session_threshold:
        return (True, f"Evidence {evidence_count} >= {evidence_threshold}, sessions {session_count} >= {session_threshold}")
    return (False, f"Not enough evidence ({evidence_count}/{evidence_threshold}) or sessions ({session_count}/{session_threshold})")


def should_archive(
    cdata: dict[str, Any],
    stale_sessions: int = 30,
    current_session_count: int = 0,
) -> tuple[bool, str]:
    """Check if a concept should be archived to needs_review.

    Returns:
        (should_archive, reason)
    """
    sessions = cdata.get("sessions", [])
    if isinstance(sessions, list) and sessions:
        last_session_index = len(sessions)
    else:
        last_session_index = cdata.get("session_count") or 0

    sessions_since = current_session_count - last_session_index if current_session_count > 0 else last_session_index

    if sessions_since >= stale_sessions and cdata.get("status") not in ("canonical", "deprecated", "needs_review"):
        return (True, f"Not seen for {sessions_since} sessions (threshold: {stale_sessions})")
    return (False, "")


class DreamLog:
    """Persistent log of dream phase actions (consolidation, decay, promotion)."""

    def __init__(self, log_path: Path):
        self.log_path = log_path

    def record(self, action: str, details: dict[str, Any]) -> None:
        import json
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            **details,
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def read_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        import json
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, "r") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries[-limit:]
