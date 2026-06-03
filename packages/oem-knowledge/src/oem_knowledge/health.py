from __future__ import annotations

import math
import time
from pathlib import Path


def calculate_concept_health(cdata: dict) -> float:
    """Calculate a concept health score (0-100) based on confidence, evidence, failures, and recency."""
    # Start with base score
    score = 50.0

    # 1. Evidence Count: +5 points per evidence, up to +30
    ev_count = cdata.get("evidence_count", 0)
    score += min(30.0, ev_count * 5.0)

    # 2. Confidence Level (status/session weight): +5 points per level of confidence, up to +25
    conf = cdata.get("confidence", 1)
    score += min(25.0, conf * 5.0)

    # 3. Failures Penalty: -15 points per failure event
    # If the confidence was decayed, we also check if failures occurred in events
    # We will pass failures count if tracked in cdata, or default to 0
    failures = cdata.get("failure_count", 0)
    score -= (failures * 15.0)

    # 4. Status Multiplier/Additions
    status = cdata.get("status", "candidate").lower()
    if status == "global":
        score += 15.0
    elif status == "canonical":
        score += 10.0
    elif status == "validated":
        score += 5.0
    elif status == "deprecated":
        score = 0.0
        return score

    # Clamp score to [0.0, 100.0]
    score = max(0.0, min(100.0, score))
    return round(score, 2)
