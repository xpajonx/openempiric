import json
from pathlib import Path

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.services.state import is_ingestion_noise_event
from oem_knowledge.tools.metrics import update_metrics_file


def event(kind, summary, evidence, source="opencode_hook", source_type="agent_runtime_signal"):
    return {"event_type": kind, "summary": summary, "evidence": evidence, "source": source, "source_type": source_type, "ingestion_eligible": True}


def test_ingestion_noise_policy_matrix():
    cases = [
        (event("observation", "Tool returned successfully", "results"), True),
        (event("observation", "Staged pending event", "details"), True),
        (event("failure", "Command failed: pytest", "Exit code: 1"), True),
        (event("failure", "Database migration failed", "lock owner identified"), False),
        (event("failure", "Durable failure", "Command failed while applying", "agent_structured", "agent_transcript"), False),
        (event("decision", "We decided", "offline requirement", "agent_structured", "agent_transcript"), False),
        (event("outcome", "Gate passed", "suite passed", "agent_structured", "agent_transcript"), False),
        (event("telemetry", "Session Metrics", "Tool calls: 4", "orchestrator"), True),
        (event("observation", "Session End / Commit Complete", "Extracted Knowledge Events: 0", "orchestrator"), True),
        (event("observation", "Report", "x", "agent_structured", "agent_transcript") | {"source_path": ".oem/sessions/x.md"}, True),
    ]
    assert [is_ingestion_noise_event(e) for e, _ in cases] == [expected for _, expected in cases]


def test_metrics_counters_initialize_and_accumulate(tmp_path):
    path = tmp_path / "metrics.json"
    update_metrics_file(path, {"noise_events_filtered": 2, "telemetry_events_skipped": 1})
    update_metrics_file(path, {"noise_events_filtered": 1, "telemetry_events_skipped": 3})
    data = json.loads(path.read_text())
    assert data["reflection"]["noise_events_filtered"] == 3
    assert data["reflection"]["telemetry_events_skipped"] == 4


def _rich_event(event_id, event_type, concept, summary, evidence, source, source_type):
    payload = {
        "event_id": event_id,
        "timestamp": "2026-09-06T00:00:00Z",
        "project": "test-project",
        "session_id": "test-session",
        "event_type": event_type,
        "concept_candidates": [concept],
        "summary": summary,
        "evidence": evidence,
        "confidence": 4,
        "source": source,
        "source_type": source_type,
        "ingestion_eligible": True,
        "schema_version": 1,
    }
    return payload


def test_reflection_filters_noise_persists_durable_events_and_records_counters(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project(str(tmp_path))
    events = [
        event("observation", "Tool returned successfully", "knowledge_search returned 3 results"),
        event("failure", "Command failed: uv run pytest", "Exit code: 1"),
        event(
            "failure",
            "Database migration failed because the lock was held",
            "The migration rolled back and the lock owner was identified",
        ),
        event(
            "failure",
            "The fallback path failed and must be retained",
            "Command failed while applying the migration; rollback was verified",
            "agent_structured",
            "agent_transcript",
        ),
        event(
            "decision",
            "We decided to keep SQLite for local state",
            "The embedded store meets the offline requirement",
            "agent_structured",
            "agent_transcript",
        ),
    ]
    result = engine.reflection.reflect_session(
        project=str(tmp_path),
        session_id="r1-filter-session",
        telemetry={"duration_sec": 2, "total_tool_calls": 4},
        events=events,
        extraction_mode="structured",
    )
    assert result["status"] == "success"
    assert result["events_written"] == 3
    assert result["noise_events_filtered"] == 2
    assert result["telemetry_events_skipped"] == 1
    assert result["explainability"]["noise_events_filtered"] == 2
    assert result["explainability"]["telemetry_events_skipped"] == 1

    events_path = engine._events_path(str(tmp_path))
    stored = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(stored) == 3
    assert {event["event_type"] for event in stored} == {"failure", "decision"}
    assert all("Tool returned successfully" not in event["summary"] for event in stored)
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "Tool returned successfully" not in report_text
    assert "Command failed while applying the migration; rollback was verified" in report_text

    metrics_path = tmp_path / OEM_DIR / "state" / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["reflection"]["noise_events_filtered"] == 2
    assert metrics["reflection"]["telemetry_events_skipped"] == 1


def test_rebuild_registry_filters_legacy_noise_but_keeps_durable_failure(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project(str(tmp_path))
    events_path = engine._events_path(str(tmp_path))
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "".join(
            json.dumps(event) + "\n"
            for event in [
                _rich_event(
                    "legacy-tool-observation", "observation", "legacy-tool-observation",
                    "Tool returned successfully", "knowledge_search returned 3 results",
                    "opencode_hook", "agent_runtime_signal",
                ),
                _rich_event(
                    "legacy-failure", "failure", "legacy-failure",
                    "The cache invalidation failed and needs a retry",
                    "The cache write rolled back after a genuine database error",
                    "agent_structured", "agent_transcript",
                ),
            ]
        ),
        encoding="utf-8",
    )
    result = engine.state.rebuild_registry(str(tmp_path))
    assert result["status"] == "success"
    registry = engine.state._load_registry(str(tmp_path))
    assert not any(data.get("canonical_name") == "legacy-tool-observation" for data in registry.values())
    durable = next(data for data in registry.values() if data.get("canonical_name") == "legacy-failure")
    assert "legacy-failure" in durable["source_event_ids"]


def test_materialization_filters_legacy_session_noise(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.init_project(str(tmp_path))
    report_path = engine._sessions_dir(str(tmp_path)) / "legacy-r1.md"
    report_path.write_text(
        "# Legacy session\n\n## Knowledge Events\n```json\n"
        + json.dumps(
            {
                "knowledge_events": [
                    {
                        "event_id": "legacy-report-noise",
                        "type": "observation",
                        "concept": "legacy-report-noise",
                        "evidence": "Session End / Commit Complete",
                        "source": "orchestrator",
                        "source_type": "agent_runtime_signal",
                    },
                    {
                        "event_id": "legacy-report-failure",
                        "type": "failure",
                        "concept": "legacy-report-failure",
                        "evidence": "The durable migration failure was reproduced",
                        "source": "agent_structured",
                        "source_type": "agent_transcript",
                    },
                ]
            }
        )
        + "\n```\n",
        encoding="utf-8",
    )
    result = engine.materialization.materialize_concepts(str(tmp_path))
    assert result["status"] == "success"
    assert result["skipped_ingestion_noise_events"] == 1
    registry = engine.state._load_registry(str(tmp_path))
    assert any(data.get("canonical_name") == "legacy-report-failure" for data in registry.values())
    assert not any(data.get("canonical_name") == "legacy-report-noise" for data in registry.values())
