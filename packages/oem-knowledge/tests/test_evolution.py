"""Tests for concept evolution: decay, promotion, archiving."""

import time
import pytest
from pathlib import Path


class TestApplyDecay:
    """Tests for exponential confidence decay."""

    def test_no_decay_for_recent_concept(self):
        from oem_knowledge.services.evolution import apply_decay
        concepts = [{
            "concept_id": "test_001",
            "confidence": 5,
            "updated_at": time.time(),
        }]
        results = apply_decay(concepts, half_life_days=30)
        assert len(results) == 0

    def test_decay_after_half_life(self):
        from oem_knowledge.services.evolution import apply_decay
        half_life_days = 30
        age_seconds = half_life_days * 86400
        concepts = [{
            "concept_id": "test_002",
            "confidence": 5,
            "updated_at": time.time() - age_seconds,
        }]
        results = apply_decay(concepts, half_life_days=half_life_days)
        assert len(results) >= 1
        assert results[0]["new_confidence"] in (2, 3)
        assert results[0]["old_confidence"] == 5

    def test_decay_floor_at_one(self):
        from oem_knowledge.services.evolution import apply_decay
        concepts = [{
            "concept_id": "test_003",
            "confidence": 2,
            "updated_at": time.time() - (365 * 86400),
        }]
        results = apply_decay(concepts, half_life_days=30, floor=1)
        assert len(results) >= 1
        assert results[0]["new_confidence"] == 1

    def test_decay_preserves_other_fields(self):
        from oem_knowledge.services.evolution import apply_decay
        concepts = [{
            "concept_id": "test_004",
            "confidence": 4,
            "updated_at": time.time() - (60 * 86400),
            "canonical_name": "Test Concept",
        }]
        results = apply_decay(concepts, half_life_days=30)
        assert len(results) >= 1
        assert results[0]["concept_id"] == "test_004"


class TestPromotion:
    """Tests for concept promotion logic."""

    def test_promotes_with_enough_evidence_and_sessions(self):
        from oem_knowledge.services.evolution import should_promote
        cdata = {
            "status": "emerging",
            "evidence_count": 7,
            "sessions": ["s1", "s2", "s3", "s4"],
        }
        should, reason = should_promote(cdata, evidence_threshold=5, session_threshold=3)
        assert should is True

    def test_does_not_promote_with_insufficient_evidence(self):
        from oem_knowledge.services.evolution import should_promote
        cdata = {
            "status": "emerging",
            "evidence_count": 2,
            "sessions": ["s1", "s2", "s3", "s4"],
        }
        should, reason = should_promote(cdata, evidence_threshold=5, session_threshold=3)
        assert should is False

    def test_does_not_promote_already_validated(self):
        from oem_knowledge.services.evolution import should_promote
        cdata = {
            "status": "validated",
            "evidence_count": 10,
            "sessions": ["s1", "s2", "s3", "s4", "s5"],
        }
        should, reason = should_promote(cdata)
        assert should is False
        assert "Already at validated" in reason


class TestArchiving:
    """Tests for concept archiving logic."""

    def test_archives_stale_concept(self):
        from oem_knowledge.services.evolution import should_archive
        cdata = {
            "status": "emerging",
            "session_count": 5,
        }
        should, reason = should_archive(cdata, stale_sessions=30, current_session_count=50)
        assert should is True

    def test_does_not_archive_canonical(self):
        from oem_knowledge.services.evolution import should_archive
        cdata = {
            "status": "canonical",
            "session_count": 5,
        }
        should, reason = should_archive(cdata, stale_sessions=10, current_session_count=50)
        assert should is False

    def test_does_not_archive_recent_concept(self):
        from oem_knowledge.services.evolution import should_archive
        cdata = {
            "status": "emerging",
            "session_count": 45,
        }
        should, reason = should_archive(cdata, stale_sessions=30, current_session_count=50)
        assert should is False


class TestDreamLog:
    """Tests for dream action logging."""

    def test_record_and_read(self, tmp_path):
        from oem_knowledge.services.evolution import DreamLog
        log = DreamLog(tmp_path / "dream_log.jsonl")
        log.record("decay", {"concept_id": "c1", "old": 5, "new": 3})
        log.record("promotion", {"concept_id": "c2", "from": "emerging", "to": "validated"})
        entries = log.read_recent()
        assert len(entries) == 2
        assert entries[0]["action"] == "decay"
        assert entries[1]["action"] == "promotion"

    def test_read_empty_log(self, tmp_path):
        from oem_knowledge.services.evolution import DreamLog
        log = DreamLog(tmp_path / "nonexistent.jsonl")
        entries = log.read_recent()
        assert entries == []
