from __future__ import annotations

import pytest
from oem_knowledge.health import calculate_concept_health


def test_calculate_concept_health():
    # Test typical/normal state
    cdata = {
        "concept_id": "concept_001",
        "canonical_name": "test-concept",
        "status": "validated",
        "confidence": 3,
        "evidence_count": 4,
        "failure_count": 0
    }
    score = calculate_concept_health(cdata)
    assert 0.0 <= score <= 100.0

    # Test deprecated state (should be 0)
    cdata_dep = {**cdata, "status": "deprecated"}
    assert calculate_concept_health(cdata_dep) == 0.0

    # Test global state (should have high score)
    cdata_global = {**cdata, "status": "global", "confidence": 5, "evidence_count": 10}
    score_global = calculate_concept_health(cdata_global)
    assert score_global == 100.0

    # Test heavy failure state (should penalize heavily)
    cdata_fail = {**cdata, "failure_count": 5}
    score_fail = calculate_concept_health(cdata_fail)
    assert score_fail < score
