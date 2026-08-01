"""Integration tests for the dream (memory maintainer) cycle."""

import json
import time
import pytest
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def engine(tmp_path):
    """Create a fresh KnowledgeEngine with initialized project."""
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    yield eng
    eng.close()


class TestDreamNoop:
    """Dream with fewer than 2 concepts should noop."""

    def test_noop_with_zero_concepts(self, engine):
        result = engine.dream(project=str(engine.project_path))
        assert result["status"] == "noop"
        assert "fewer than 2 concepts" in result["reason"]

    def test_noop_with_one_concepts(self, engine, tmp_path):
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "only-concept",
            "status": "candidate",
            "confidence": 1,
            "evidence_count": 0,
            "sessions": [],
        }
        engine.state._save_registry(registry, str(tmp_path))
        result = engine.dream(project=str(tmp_path))
        assert result["status"] == "noop"

    def test_force_bypasses_noop(self, engine, tmp_path):
        result = engine.dream(project=str(tmp_path), force=True)
        assert result["status"] == "success"
        assert result["baseline"]["total_concepts"] == 0


class TestDreamDecay:
    """Dream should apply confidence decay to old concepts."""

    def test_decays_stale_concepts(self, engine, tmp_path):
        old_time = time.time() - (90 * 86400)  # 90 days ago
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "stale-concept",
            "status": "candidate",
            "confidence": 5,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": old_time,
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "fresh-concept",
            "status": "candidate",
            "confidence": 3,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": time.time(),
        }
        engine.state._save_registry(registry, str(tmp_path))

        result = engine.dream(project=str(tmp_path))
        assert result["status"] == "success"
        assert result["decay"]["candidates"] >= 1
        assert result["decay"]["applied"] >= 1

    def test_decay_logged_to_dream_log(self, engine, tmp_path):
        old_time = time.time() - (90 * 86400)
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "stale-concept",
            "status": "candidate",
            "confidence": 5,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": old_time,
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "fresh-concept",
            "status": "candidate",
            "confidence": 3,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": time.time(),
        }
        engine.state._save_registry(registry, str(tmp_path))

        engine.dream(project=str(tmp_path))

        dream_log_path = tmp_path / ".oem" / "state" / "dream_log.jsonl"
        assert dream_log_path.exists()
        entries = dream_log_path.read_text().strip().split("\n")
        decay_entries = [json.loads(e) for e in entries if json.loads(e)["action"] == "decay"]
        assert len(decay_entries) >= 1


class TestDreamPromotion:
    """Dream should promote concepts with enough evidence and sessions."""

    def test_promotes_emerging_concept(self, engine, tmp_path):
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "strong-concept",
            "status": "emerging",
            "confidence": 3,
            "evidence_count": 7,
            "sessions": ["s1", "s2", "s3", "s4"],
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "weak-concept",
            "status": "candidate",
            "confidence": 1,
            "evidence_count": 0,
            "sessions": [],
        }
        engine.state._save_registry(registry, str(tmp_path))

        result = engine.dream(project=str(tmp_path))
        assert result["status"] == "success"
        assert result["promotion"]["candidates"] >= 1
        assert result["promotion"]["applied"] == 1

        # Verify the promotion persisted
        updated_registry = engine.state._load_registry(str(tmp_path))
        assert updated_registry["concept_001"]["status"] == "validated"


class TestDreamArchive:
    """Dream should archive stale concepts."""

    def test_archives_old_concepts(self, engine, tmp_path):
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "ancient-concept",
            "status": "emerging",
            "confidence": 2,
            "evidence_count": 1,
            "sessions": ["s1"],
            "session_count": 1,
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "recent-concept",
            "status": "candidate",
            "confidence": 3,
            "evidence_count": 2,
            "sessions": ["s1", "s2", "s3", "s4", "s5"],
            "session_count": 50,
        }
        engine.state._save_registry(registry, str(tmp_path))

        # Add events so should_archive has a current_session_count to work with
        from oem_knowledge.models import KnowledgeEvent
        from datetime import datetime, timezone
        events = []
        for i in range(35):
            events.append(KnowledgeEvent(
                event_id=f"evt_{i:03d}",
                event_type="observation",
                summary=f"Event {i}",
                evidence=f"Evidence for event {i}",
                session_id=f"s{i}",
                project=str(tmp_path),
                source="test",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ).model_dump())
        engine.state.append_events(events, str(tmp_path))

        result = engine.dream(project=str(tmp_path))
        assert result["status"] == "success"
        assert result["archive"]["applied"] >= 1

        updated_registry = engine.state._load_registry(str(tmp_path))
        assert updated_registry["concept_001"]["status"] == "needs_review"


class TestDreamFullCycle:
    """Dream should run all four phases and produce a valid result."""

    def test_full_cycle_result_shape(self, engine, tmp_path):
        old_time = time.time() - (90 * 86400)
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "multi-action-concept",
            "status": "emerging",
            "confidence": 5,
            "evidence_count": 6,
            "sessions": ["s1", "s2", "s3"],
            "updated_at": old_time,
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "stable-concept",
            "status": "validated",
            "confidence": 4,
            "evidence_count": 10,
            "sessions": ["s1", "s2"],
            "updated_at": time.time(),
        }
        engine.state._save_registry(registry, str(tmp_path))

        result = engine.dream(project=str(tmp_path))

        assert result["status"] == "success"
        assert "baseline" in result
        assert "total_concepts" in result["baseline"]
        assert "by_status" in result["baseline"]
        assert "decay" in result
        assert "promotion" in result
        assert "archive" in result
        assert "merge" in result
        assert "candidates" in result["decay"]
        assert "applied" in result["decay"]


class TestDreamConfig:
    """Dream should respect config thresholds."""

    def test_half_life_from_config(self, engine, tmp_path):
        # Write config with very short half-life
        import yaml
        config_dir = tmp_path / ".oem" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "reflection": {
                "auto_dream": {
                    "enabled": True,
                    "half_life_days": 1,
                }
            }
        }
        (config_dir / "reflection.yml").write_text(yaml.dump(config))

        # Add concept from 10 days ago
        old_time = time.time() - (10 * 86400)
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "test-concept",
            "status": "candidate",
            "confidence": 5,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": old_time,
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "another-concept",
            "status": "candidate",
            "confidence": 5,
            "evidence_count": 0,
            "sessions": [],
            "updated_at": time.time(),
        }
        engine.state._save_registry(registry, str(tmp_path))

        result = engine.dream(project=str(tmp_path))
        # With 1-day half-life, 10-day-old concept should decay significantly
        assert result["decay"]["candidates"] >= 1


class TestDreamAutoTrigger:
    """Dream should auto-trigger from session_end when enabled."""

    def test_session_end_triggers_dream(self, engine, tmp_path):
        import yaml
        config_dir = tmp_path / ".oem" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "reflection": {
                "auto_dream": {
                    "enabled": True,
                    "half_life_days": 30,
                }
            }
        }
        (config_dir / "reflection.yml").write_text(yaml.dump(config))

        # Add concepts so dream has something to work with
        registry = engine.state._load_registry(str(tmp_path))
        registry["concept_001"] = {
            "concept_id": "concept_001",
            "canonical_name": "test-concept",
            "status": "candidate",
            "confidence": 3,
            "evidence_count": 0,
            "sessions": [],
        }
        registry["concept_002"] = {
            "concept_id": "concept_002",
            "canonical_name": "another-concept",
            "status": "candidate",
            "confidence": 3,
            "evidence_count": 0,
            "sessions": [],
        }
        engine.state._save_registry(registry, str(tmp_path))

        # Create an event so events_written > 0
        from oem_knowledge.models import KnowledgeEvent
        from datetime import datetime, timezone
        event = KnowledgeEvent(
            event_id="test_001",
            event_type="observation",
            summary="Test observation for dream trigger",
            evidence="Created during test",
            session_id="test_session",
            project=str(tmp_path),
            source="test",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        engine.state.append_events([event.model_dump()], str(tmp_path))

        # End session with structured events — dream should auto-trigger
        structured_events = [{
            "event_id": "test_001",
            "event_type": "observation",
            "summary": "Test observation for dream trigger",
            "evidence": "Created during test",
            "session_id": "test_session",
            "project": str(tmp_path),
            "source": "test",
            "confidence": 3,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
        result = engine.session_end(
            project=str(tmp_path),
            conversation_text="Observation: test session for dream trigger",
            session_id="test_session",
            update_index=False,
            events=structured_events,
        )

        assert "dream" in result
        assert result["dream"]["status"] == "success"
