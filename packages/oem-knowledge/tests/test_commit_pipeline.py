"""Tests for the CommitPipeline crash recovery infrastructure."""

import json
import pytest
from pathlib import Path


class TestCommitIntentLog:
    """Tests for the write-ahead intent log."""

    def test_write_and_read_intent(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitIntentLog
        log = CommitIntentLog(tmp_path / "intent.json")
        log.write_intent("append_events", session_id="test-123")
        intent = log.read_intent()
        assert intent is not None
        assert intent["phase"] == "append_events"
        assert intent["session_id"] == "test-123"

    def test_has_uncompleted_intent(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitIntentLog
        log = CommitIntentLog(tmp_path / "intent.json")
        assert not log.has_uncompleted_intent()
        log.write_intent("materialize")
        assert log.has_uncompleted_intent()
        log.write_intent("complete")
        assert not log.has_uncompleted_intent()

    def test_clear_intent(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitIntentLog
        log = CommitIntentLog(tmp_path / "intent.json")
        log.write_intent("index")
        log.clear_intent()
        assert not tmp_path.joinpath("intent.json").exists()

    def test_corrupt_intent_file_is_none(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitIntentLog
        intent_path = tmp_path / "intent.json"
        intent_path.write_text("not valid json {{{")
        log = CommitIntentLog(intent_path)
        assert log.read_intent() is None


class TestStagingArea:
    """Tests for the staging area with byte-offset tracking."""

    def test_commit_phase(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import StagingArea
        staging = StagingArea(tmp_path)
        staging.commit_phase("reflect", {"events": 5})
        assert staging.get_results()["reflect"] == {"events": 5}

    def test_rollback_removes_failed_and_later_phases(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import StagingArea
        staging = StagingArea(tmp_path)
        staging.commit_phase("reflect", {"events": 5})
        staging.commit_phase("validate", {"valid": True})
        staging.commit_phase("append_events", {"written": 5})
        staging.rollback_to("append_events")
        results = staging.get_results()
        assert "reflect" in results
        assert "validate" in results
        assert "append_events" not in results

    def test_byte_offset_tracking(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import StagingArea
        staging = StagingArea(tmp_path)
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("line1\nline2\n")
        staging.record_byte_offset("append_events", events_file)
        offset = staging.get_byte_offset("append_events")
        assert offset == len("line1\nline2\n")

    def test_byte_offset_new_file_is_zero(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import StagingArea
        staging = StagingArea(tmp_path)
        nonexistent = tmp_path / "nonexistent.jsonl"
        staging.record_byte_offset("append_events", nonexistent)
        assert staging.get_byte_offset("append_events") == 0


class TestCommitPipeline:
    """Tests for the commit pipeline orchestrator."""

    def test_start_and_complete_phase(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitPipeline
        pipeline = CommitPipeline(tmp_path)
        pipeline.start_phase("append_events")
        intent = pipeline.intent_log.read_intent()
        assert intent["phase"] == "append_events"
        pipeline.complete_phase("append_events", {"written": 10})
        pipeline.complete_pipeline()
        assert not pipeline.intent_log.has_uncompleted_intent()

    def test_recover_from_crash_clear_stale_complete(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitPipeline
        pipeline = CommitPipeline(tmp_path)
        pipeline.intent_log.write_intent("complete")
        result = pipeline.recover_from_crash()
        assert result["recovered"] is True
        assert not pipeline.intent_log.has_uncompleted_intent()

    def test_recover_from_crash_append_events_rollback(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitPipeline
        pipeline = CommitPipeline(tmp_path)
        events_file = tmp_path / "events.jsonl"
        events_file.write_text("event1\n")
        pipeline.intent_log.write_intent("append_events")
        pipeline.staging.record_byte_offset("append_events", events_file)
        # Simulate crash: append extra data that should be rolled back
        with open(events_file, "a") as f:
            f.write("partially_written_event2\n")
        result = pipeline.recover_from_crash(events_path=events_file)
        assert result["recovered"] is True
        assert result["action"] == "rolled_back_events"
        # File should be truncated to original size
        assert events_file.read_text() == "event1\n"

    def test_rollback_events_preserves_original_content(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitPipeline
        pipeline = CommitPipeline(tmp_path)
        events_file = tmp_path / "events.jsonl"
        original = "event_a\nevent_b\n"
        events_file.write_text(original)
        pipeline.staging.record_byte_offset("append_events", events_file)
        # Simulate partial write
        with open(events_file, "a") as f:
            f.write("partial_event_c")
        pipeline.rollback_events(events_file)
        assert events_file.read_text() == original

    def test_unknown_phase_raises(self, tmp_path):
        from oem_knowledge.runtime.commit_pipeline import CommitPipeline
        import pytest
        pipeline = CommitPipeline(tmp_path)
        with pytest.raises(ValueError, match="Unknown phase"):
            pipeline.start_phase("nonexistent_phase")
