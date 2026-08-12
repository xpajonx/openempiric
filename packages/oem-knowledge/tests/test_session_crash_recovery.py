"""Wave 4B: session_start recovers partial commits; session_end leaves a clean intent."""

import json


def _event(event_id="evt-crash-1", summary="Decision: crash-safe append"):
    return {
        "event_id": event_id,
        "timestamp": "2026-07-01T10:00:00Z",
        "project": "proj",
        "session_id": "sess-crash",
        "event_type": "decision",
        "summary": summary,
        "evidence": summary,
        "source": "test",
    }


def test_session_start_recovers_partial_append(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline, prefix_checksum
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    events_file = eng._events_path(str(tmp_path / "proj"))
    events_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.write_text("line1\n", encoding="utf-8")
    offset = len("line1\n")
    harness = eng._resolve_harness(str(tmp_path / "proj"))
    pipeline = CommitPipeline(harness)
    pipeline.intent_log.write_intent(
        "append_events",
        byte_offset=offset,
        expected_bytes=10,
        prefix_checksum=prefix_checksum(events_file, offset),
    )
    events_file.write_text("line1\npartial_garbage\n", encoding="utf-8")
    result = eng.session_start(str(tmp_path / "proj"))
    assert result.get("recovered") is True
    assert result.get("recovery_action") == "rolled_back_events"
    assert events_file.read_text(encoding="utf-8") == "line1\n"
    assert "Session commit recovery" in " ".join(result.get("warnings", []))


def test_session_start_no_intent_no_recovery_keys(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    result = eng.session_start(str(tmp_path / "proj"))
    assert "recovered" not in result
    assert result.get("status") == "success"


def test_session_start_clears_completed_append_intent(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime.commit_pipeline import CommitPipeline, prefix_checksum
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    events_file = eng._events_path(str(tmp_path / "proj"))
    events_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.write_text("line1\n", encoding="utf-8")
    offset = len("line1\n")
    harness = eng._resolve_harness(str(tmp_path / "proj"))
    pipeline = CommitPipeline(harness)
    pipeline.intent_log.write_intent(
        "append_events",
        byte_offset=offset,
        expected_bytes=6,
        prefix_checksum=prefix_checksum(events_file, offset),
    )
    events_file.write_text("line1\nline2\n", encoding="utf-8")  # exactly offset + 6
    result = eng.session_start(str(tmp_path / "proj"))
    assert result.get("recovery_action") == "events_committed"
    assert events_file.read_text(encoding="utf-8") == "line1\nline2\n"


def test_session_end_success_clears_intent(tmp_path):
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project_path=tmp_path / "proj")
    eng.init_project(str(tmp_path / "proj"))
    res = eng.session_end(
        project=str(tmp_path / "proj"),
        conversation_text="",
        events=[_event()],
        update_index=False,
    )
    assert res.get("status") in ("success", "partial")
    intent_path = eng._resolve_harness(str(tmp_path / "proj")) / ".staging" / "intent.json"
    if res.get("status") == "success":
        assert not intent_path.exists(), "successful session_end must clear the commit intent"
