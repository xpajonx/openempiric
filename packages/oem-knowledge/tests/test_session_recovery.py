"""Session recovery invariants (Wave 0 contracts)."""

import pytest


def test_intent_log_and_staging_live_under_oem_dir(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline
    pipeline = CommitPipeline(tmp_path)
    assert str(pipeline.intent_log.intent_path).startswith(str(tmp_path))
    assert str(pipeline.staging_dir).startswith(str(tmp_path))


def test_append_events_deduplicates_by_event_id(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    base = {
        "timestamp": "2026-07-01T10:00:00Z",
        "project": str(tmp_path),
        "session_id": "sess-test",
        "event_type": "observation",
        "summary": "dup event",
        "evidence": "evidence for dup event",
        "source": "test",
    }
    dup1 = dict(base, event_id="evt-dup-1")
    unique = dict(base, event_id="evt-uniq-1", summary="unique event")
    dup2 = dict(base, event_id="evt-dup-1", summary="dup event rewritten")
    # append_events dedups against the existing file, so duplicate must come in a second batch
    eng.state.append_events([dup1, unique], str(tmp_path))
    eng.state.append_events([dup2], str(tmp_path))
    events = eng.state.load_events(str(tmp_path))
    ids = [e.get("event_id") for e in events]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_intent_write_is_atomic(tmp_path, monkeypatch):
    from oem_knowledge.runtime.commit_pipeline import CommitIntentLog
    import os
    calls = []
    real_replace = os.replace
    def fake_replace(src, dst):
        calls.append((src, dst))
        return real_replace(src, dst)
    monkeypatch.setattr(os, "replace", fake_replace)
    log = CommitIntentLog(tmp_path / ".staging" / "intent.json")
    log.write_intent("reflect")
    assert calls, "write_intent must write atomically via os.replace"


def test_recover_completed_append_keeps_events(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline, prefix_checksum
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("line1\nline2\n", encoding="utf-8")
    offset = len("line1\nline2\n")
    pipeline = CommitPipeline(tmp_path)
    pipeline.staging.record_byte_offset("append_events", events_file)
    pipeline.intent_log.write_intent("append_events", byte_offset=offset, expected_bytes=6, prefix_checksum=prefix_checksum(events_file, offset))
    events_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = pipeline.recover_from_crash(events_path=events_file)
    assert result["action"] == "events_committed"
    assert events_file.read_text(encoding="utf-8") == "line1\nline2\nline3\n"


def test_recover_partial_append_rolls_back(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline, prefix_checksum
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("line1\n", encoding="utf-8")
    offset = len("line1\n")
    pipeline = CommitPipeline(tmp_path)
    pipeline.staging.record_byte_offset("append_events", events_file)
    pipeline.intent_log.write_intent("append_events", byte_offset=offset, expected_bytes=10, prefix_checksum=prefix_checksum(events_file, offset))
    events_file.write_text("line1\npartial_event2\n", encoding="utf-8")
    result = pipeline.recover_from_crash(events_path=events_file)
    assert result["action"] == "rolled_back_events"
    assert events_file.read_text(encoding="utf-8") == "line1\n"


def test_recover_checksum_mismatch_quarantines(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline, prefix_checksum
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("line1\n", encoding="utf-8")
    offset = len("line1\n")
    pipeline = CommitPipeline(tmp_path)
    pipeline.intent_log.write_intent("append_events", byte_offset=offset, expected_bytes=10, prefix_checksum="deadbeef")
    events_file.write_text("CORRUPTED\n", encoding="utf-8")
    result = pipeline.recover_from_crash(events_path=events_file)
    assert result["action"] == "quarantined_checksum_mismatch"
    assert list(tmp_path.rglob("intent.quarantine-*.json"))


def test_recover_malformed_intent_quarantines(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline
    intent_file = tmp_path / ".staging" / "intent.json"
    intent_file.parent.mkdir(parents=True, exist_ok=True)
    intent_file.write_text("not json {{{", encoding="utf-8")
    pipeline = CommitPipeline(tmp_path)
    result = pipeline.recover_from_crash()
    assert result["action"] == "quarantined_malformed_intent"
    assert list(tmp_path.rglob("intent.quarantine-*.json"))


def test_recover_failed_intent_quarantines(tmp_path):
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline
    pipeline = CommitPipeline(tmp_path)
    pipeline.intent_log.write_intent("failed", last_phase="materialize", reason="boom")
    result = pipeline.recover_from_crash()
    assert result["action"] == "quarantined_failed_intent"
    assert list(tmp_path.rglob("intent.quarantine-*.json"))
