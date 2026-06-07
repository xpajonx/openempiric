import json
import pytest
from unittest.mock import patch
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.event_migration import LATEST_SCHEMA_VERSION


def test_multi_hop_migration(tmp_path):
    # 1. Initialize engine on temporary path
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project("test_mig_proj")

    # 2. Write a v1 mock event to events.jsonl
    events_file = engine._events_path()
    mock_event = {
        "event_id": "test-uuid-123",
        "timestamp": "2026-06-04T00:00:00Z",
        "project": "test_mig_proj",
        "session_id": "session_001",
        "event_type": "observation",
        "concept_candidates": ["Old Name"],
        "summary": "Old Summary",
        "evidence": "Old Evidence",
        "confidence": 3,
        "source": "chat",
        "schema_version": 1,
    }
    with open(events_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(mock_event) + "\n")

    # 3. Register explicit upcasters (v1 -> v2 -> v3)
    def upcast_v1_to_v2(event: dict) -> dict:
        event["summary"] = event["summary"] + " (v2)"
        return event

    def upcast_v2_to_v3(event: dict) -> dict:
        event["summary"] = event["summary"] + " (v3)"
        event["extra_field"] = "new_value"
        return event

    engine.event_migrator.register_upcaster(1, 2, upcast_v1_to_v2)
    engine.event_migrator.register_upcaster(2, 3, upcast_v2_to_v3)

    # 4. Patch LATEST_SCHEMA_VERSION to 3 to verify multi-hop
    with patch("oem_knowledge.services.event_migration.LATEST_SCHEMA_VERSION", 3):
        # Verify schema status is outdated
        status = engine.event_migrator.get_schema_status()
        assert status["status"] == "outdated"
        assert status["current_versions"] == [1]

        # Verify dynamic upcasting on load
        events = engine.state.get_events()
        assert len(events) == 1
        loaded_event = events[0]
        assert loaded_event["schema_version"] == 3
        assert loaded_event["summary"] == "Old Summary (v2) (v3)"
        assert loaded_event["extra_field"] == "new_value"

        # Verify file is still at v1 on disk
        with open(events_file, "r", encoding="utf-8") as f:
            disk_event = json.loads(f.read().strip())
            assert disk_event.get("schema_version", 1) == 1

        # Run offline migration
        res = engine.event_migrator.migrate_file()
        assert res["status"] == "success"
        assert res["migrated_count"] == 1

        # Verify schema status is now up to date
        status_after = engine.event_migrator.get_schema_status()
        assert status_after["status"] == "up_to_date"
        assert status_after["current_versions"] == [3]

        # Verify file is upgraded on disk
        with open(events_file, "r", encoding="utf-8") as f:
            disk_event_after = json.loads(f.read().strip())
            assert disk_event_after["schema_version"] == 3
            assert disk_event_after["summary"] == "Old Summary (v2) (v3)"
            assert disk_event_after["extra_field"] == "new_value"
