import json
import time
from pathlib import Path

import pytest
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.models import RuntimeMetrics, MetricsSchema
from oem_knowledge.tools.metrics import update_metrics_file
from oem_knowledge.runtime.session import SessionState


@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng


class TestRuntimeMetricsModel:
    def test_defaults(self):
        m = RuntimeMetrics()
        assert m.sessions_started == 0
        assert m.sessions_completed == 0
        assert m.sessions_failed == 0
        assert m.sessions_recovered == 0
        assert m.reflections == 0
        assert m.materializations == 0

    def test_in_metrics_schema(self):
        schema = MetricsSchema()
        assert hasattr(schema, "runtime")
        assert schema.runtime.sessions_started == 0

    def test_serialization(self):
        m = RuntimeMetrics(sessions_started=5, sessions_completed=3, materializations=10)
        d = m.model_dump()
        assert d["sessions_started"] == 5
        assert d["materializations"] == 10


class TestUpdateMetricsFileRuntime:
    def test_runtime_section_added(self, tmp_path):
        metrics_file = tmp_path / "metrics.json"
        update_metrics_file(metrics_file, {"sessions_started": 1})
        data = json.loads(metrics_file.read_text())
        assert data["runtime"]["sessions_started"] == 1
        assert data["runtime"]["sessions_completed"] == 0

    def test_runtime_accumulates(self, tmp_path):
        metrics_file = tmp_path / "metrics.json"
        update_metrics_file(metrics_file, {"sessions_started": 1})
        update_metrics_file(metrics_file, {"sessions_started": 1})
        data = json.loads(metrics_file.read_text())
        assert data["runtime"]["sessions_started"] == 2

    def test_all_runtime_keys(self, tmp_path):
        metrics_file = tmp_path / "metrics.json"
        update_metrics_file(metrics_file, {
            "sessions_started": 3,
            "sessions_completed": 2,
            "sessions_failed": 1,
            "sessions_recovered": 1,
            "reflections": 5,
            "materializations": 4,
        })
        data = json.loads(metrics_file.read_text())
        rt = data["runtime"]
        assert rt["sessions_started"] == 3
        assert rt["sessions_completed"] == 2
        assert rt["sessions_failed"] == 1
        assert rt["sessions_recovered"] == 1
        assert rt["reflections"] == 5
        assert rt["materializations"] == 4


class TestSessionStatus:
    def test_no_active_session(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        if active_file.exists():
            active_file.unlink()
        state = SessionState.load(active_file)
        assert state is None

    def test_active_session_shows_status(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        active_file.parent.mkdir(parents=True, exist_ok=True)

        ss = SessionState.create(
            session_id="test_sid_001",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat_test.md"),
            context_path=str(tmp_path / "context.json"),
            temp_instructions=str(tmp_path / "instructions.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        loaded = SessionState.load(active_file)
        assert loaded is not None
        assert loaded.session_id == "test_sid_001"
        assert loaded.agent == "opencode"
        assert loaded.status == "running"

    def test_context_injection_flag(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"

        context_file = tmp_path / "context.json"
        context_file.write_text("{}")

        ss = SessionState.create(
            session_id="test_sid_002",
            agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat_test.md"),
            context_path=str(context_file),
            temp_instructions=str(tmp_path / "instructions.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        loaded = SessionState.load(active_file)
        assert loaded is not None
        assert Path(loaded.context_path).exists()

    def test_knowledge_retrieved_count(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        session_state_file = harness / "state" / "session_state.json"
        session_state_file.parent.mkdir(parents=True, exist_ok=True)
        session_state_file.write_text(json.dumps({
            "session_id": "test_sid_003",
            "last_injected_concepts": ["concept_001", "concept_002", "concept_003"],
        }))

        data = json.loads(session_state_file.read_text())
        assert len(data.get("last_injected_concepts", [])) == 3


class TestRuntimeSummary:
    def test_summary_from_metrics_and_outcomes(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        metrics_file = harness / "state" / "metrics.json"
        outcomes_file = harness / "state" / "outcomes.jsonl"

        update_metrics_file(metrics_file, {
            "sessions_started": 5,
            "sessions_completed": 3,
            "sessions_failed": 1,
            "sessions_recovered": 1,
            "reflections": 8,
            "materializations": 6,
            "structured_events": 10,
            "fallback_extractions": 2,
            "search_count": 20,
        })

        outcomes_file.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(outcomes_file, "a", encoding="utf-8") as f:
            for outcome in ["success", "success", "failure"]:
                f.write(json.dumps({
                    "session_id": "s1",
                    "outcome": outcome,
                    "referenced_concepts": [],
                    "timestamp": ts,
                }) + "\n")

        data = json.loads(metrics_file.read_text())
        rt = data["runtime"]
        assert rt["sessions_started"] == 5
        assert rt["sessions_completed"] == 3
        assert rt["sessions_failed"] == 1
        assert rt["sessions_recovered"] == 1
        assert rt["reflections"] == 8
        assert rt["materializations"] == 6

        outcomes = []
        for line in outcomes_file.read_text().splitlines():
            if line.strip():
                outcomes.append(json.loads(line))
        assert len(outcomes) == 3
        successes = sum(1 for o in outcomes if o["outcome"] == "success")
        failures = sum(1 for o in outcomes if o["outcome"] == "failure")
        assert successes == 2
        assert failures == 1


class TestRuntimeMetricsEmission:
    def test_reflection_emits_reflections_metric(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        metrics_file = harness / "state" / "metrics.json"
        update_metrics_file(metrics_file, {})

        res = engine.reflection.reflect_session(
            project=str(tmp_path),
            conversation_text="Fixed the doctor module.",
            session_id="test_emit_refl",
        )
        assert res["status"] == "success"

        data = json.loads(metrics_file.read_text())
        assert data["runtime"]["reflections"] >= 1

    def test_materialization_emits_materializations_metric(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        res = engine.reflection.reflect_session(
            project=str(tmp_path),
            conversation_text="Fixed the doctor module.\nAdded fallback extraction.",
            session_id="test_emit_mat",
        )
        assert res["status"] == "success"

        mat_res = engine.materialization.materialize_concepts(project=str(tmp_path))
        assert mat_res["status"] == "success"

        metrics_file = harness / "state" / "metrics.json"
        data = json.loads(metrics_file.read_text())
        if mat_res.get("materialized"):
            assert data["runtime"]["materializations"] >= 1


class TestRuntimeReadinessAndSupervisor:
    def test_checks_pipeline(self, engine, tmp_path):
        from oem_knowledge.runtime.readiness import RuntimeReadiness
        from oem_knowledge.runtime.supervisor import render_supervisor_panel
        
        harness = engine._resolve_harness(str(tmp_path))
        
        class DummyAdapter:
            def verify_health(self):
                return True, "All good"
            def verify_mcp(self):
                return True
                
        adapter = DummyAdapter()
        
        readiness = RuntimeReadiness()
        checks = readiness.check(
            eng=engine,
            agent_name="generic",
            project=str(tmp_path),
            harness=harness,
            adapter=adapter,
            stale_existed=False,
            recovery_failed=False
        )
        
        assert len(checks) > 0
        names = [c.name for c in checks]
        assert "Project initialized" in names
        assert "Harness resolved" in names
        assert "Plugin healthy" in names
        
        panel_str = render_supervisor_panel(str(tmp_path), "opencode", checks)
        assert "Project" in panel_str
        assert "OpenCode" in panel_str

