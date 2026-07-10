import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.working_set import (
    WorkingSet,
    load_working_set,
    save_working_set,
    update_working_set,
    merge_working_set,
)
from oem_knowledge.health import build_health_report
from oem_knowledge.cli import main

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_working_set_create(engine, tmp_path):
    ws = update_working_set(project=str(tmp_path), goal="My goal")
    assert ws.goal == "My goal"
    assert ws.schema_version == 1
    assert ws.workspace_root == str(tmp_path.resolve())
    
    # Verify file was written
    path = engine.layout().working_set_path
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["goal"] == "My goal"
    assert data["schema_version"] == 1
    assert data["workspace_root"] == str(tmp_path.resolve())

def test_working_set_roundtrip(engine, tmp_path):
    ws = WorkingSet(
        workspace_root=str(tmp_path),
        goal="Test goal",
        current_problem="Problem description",
        active_files=["file1.py", "file2.py"],
    )
    save_working_set(ws, project=str(tmp_path))
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is not None
    assert loaded.goal == "Test goal"
    assert loaded.current_problem == "Problem description"
    assert loaded.active_files == ["file1.py", "file2.py"]

def test_working_set_atomic_write(engine, tmp_path):
    ws = WorkingSet(workspace_root=str(tmp_path), goal="atomic test")
    path = engine.layout().working_set_path
    
    orig_replace = Path.replace
    replace_called = []
    
    def mock_replace(self, target):
        replace_called.append((self, target))
        return orig_replace(self, target)
        
    with patch.object(Path, "replace", mock_replace):
        save_working_set(ws, project=str(tmp_path))
        
    assert len(replace_called) == 1
    src, target = replace_called[0]
    assert target.resolve() == path.resolve()
    assert src.name.startswith(".working_set.json.")
    assert src.name.endswith(".tmp")

def test_working_set_health_visible(engine, tmp_path):
    # No working set exists initially
    report_empty = build_health_report(str(tmp_path), include_daemon_runtime=False)
    assert "working_set" in report_empty
    assert report_empty["working_set"]["exists"] is False
    assert report_empty["working_set"]["active_files_count"] == 0

    # Write a working set
    update_working_set(
        project=str(tmp_path),
        active_work_item="work-1",
        active_files=["src/foo.py", "src/bar.py"],
        active_concepts=["concept_a"],
    )
    
    report_active = build_health_report(str(tmp_path), include_daemon_runtime=False)
    assert report_active["working_set"]["exists"] is True
    assert report_active["working_set"]["active_work_item"] == "work-1"
    assert report_active["working_set"]["active_files_count"] == 2
    assert report_active["working_set"]["active_concepts_count"] == 1

def test_working_set_show_cli(engine, tmp_path):
    # CLI show should fail if working set does not exist
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path)]):
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    # Now create one
    update_working_set(project=str(tmp_path), goal="CLI goal")
    
    # Run show in text mode
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path)]):
        with patch("builtins.print") as mock_print:
            main()
        
        output = "\n".join(" ".join(str(arg) for arg in call[0]) for call in mock_print.call_args_list)
        assert "CLI goal" in output
        assert "OEM Working Set" in output

    # Run show in JSON mode
    with patch.object(sys, "argv", ["oem", "working-set", "show", "--project", str(tmp_path), "--json"]):
        with patch("builtins.print") as mock_print:
            main()
        
        output = "\n".join(" ".join(str(arg) for arg in call[0]) for call in mock_print.call_args_list)
        data = json.loads(output)
        assert data["goal"] == "CLI goal"
        assert data["schema_version"] == 1

def test_working_set_corruption(engine, tmp_path):
    path = engine.layout().working_set_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupted_json", encoding="utf-8")
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is None

def test_working_set_migration(engine, tmp_path):
    path = engine.layout().working_set_path
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_data = {
        "version": 1,
        "workspace_root": str(tmp_path),
        "goal": "legacy version test"
    }
    path.write_text(json.dumps(legacy_data), encoding="utf-8")
    
    loaded = load_working_set(project=str(tmp_path))
    assert loaded is not None
    assert loaded.schema_version == 1
    assert loaded.goal == "legacy version test"

def test_working_set_merge(engine, tmp_path):
    update_working_set(
        project=str(tmp_path),
        goal="original goal",
        active_files=["a.py"],
    )
    merged = merge_working_set(
        project=str(tmp_path),
        goal="merged goal",
        active_files=["b.py"],
    )
    assert merged.goal == "merged goal"
    assert merged.active_files == ["a.py", "b.py"]

def test_preflight_updates_working_set(engine, tmp_path):
    # Setup mock active work identity in layout.root (.oem/)
    layout = engine.layout()
    oem_dir = layout.root
    oem_dir.mkdir(parents=True, exist_ok=True)
    
    # Create mock session-handoff.json
    handoff_path = oem_dir / "session-handoff.json"
    handoff_path.write_text(json.dumps({
        "active_work_item": "task-abc",
        "active_topic": "topic-123",
        "active_task": "subtask-xyz"
    }), encoding="utf-8")
    
    # Run preflight
    engine.preflight(task="Resolve bug", project=str(tmp_path))
    
    # Verify working set got updated
    ws = load_working_set(project=str(tmp_path))
    assert ws is not None
    assert ws.active_work_item == "task-abc"
    assert ws.active_topic == "topic-123"
    assert ws.active_task == "subtask-xyz"

def test_source_search_updates_active_files(engine, tmp_path):
    # Mock run_preflight results to contain source suggestions
    from oem_knowledge.preflight.models import PreflightResult, PreflightMatch
    
    mock_result = PreflightResult(
        status="success",
        operation="run_preflight",
        project_root=str(tmp_path),
        memory_root=str(engine.layout().root),
        task="run search",
        decision="noop",
        reason="",
        source_suggestions=[
            PreflightMatch(
                kind="source",
                id="1",
                title="src/client.py",
                score=5.0,
                reason="match",
                source_path="src/client.py",
                metadata={"source_type": "client_code"}
            ),
            PreflightMatch(
                kind="source",
                id="2",
                title="tests/test_client.py",
                score=4.0,
                reason="match",
                source_path="tests/test_client.py",
                metadata={"source_type": "relevant_test"}
            )
        ]
    )
    
    with patch("oem_knowledge.preflight.run_preflight", return_value=mock_result):
        engine.preflight(task="run search", project=str(tmp_path))
        
    ws = load_working_set(project=str(tmp_path))
    assert ws is not None
    # Only src/client.py should be added, tests/test_client.py is ignored (not implementation code)
    assert "src/client.py" in ws.active_files
    assert "tests/test_client.py" not in ws.active_files

def test_memory_updates_active_concepts(engine, tmp_path):
    # Mock run_preflight results to contain matched concepts
    from oem_knowledge.preflight.models import PreflightResult, PreflightMatch
    
    mock_result = PreflightResult(
        status="success",
        operation="run_preflight",
        project_root=str(tmp_path),
        memory_root=str(engine.layout().root),
        task="find concepts",
        decision="noop",
        reason="",
        matched_concepts=[
            PreflightMatch(
                kind="concept",
                id="concept-99",
                title="Concept 99",
                score=9.0,
                reason="match"
            )
        ]
    )
    
    with patch("oem_knowledge.preflight.run_preflight", return_value=mock_result):
        with patch.object(engine.state, "concept_ids_from_retrieval_results", return_value=["concept-99"]):
            engine.preflight(task="find concepts", project=str(tmp_path))
            
    ws = load_working_set(project=str(tmp_path))
    assert ws is not None
    assert "concept-99" in ws.active_concepts

def test_no_write_when_unchanged(engine, tmp_path):
    # Set initial working set
    update_working_set(project=str(tmp_path), goal="unchanged goal")
    ws_path = engine.layout().working_set_path
    
    # Record current mtime
    first_mtime = ws_path.stat().st_mtime
    first_updated_at = load_working_set(project=str(tmp_path)).updated_at
    
    # Wait a brief moment to ensure time moves
    time.sleep(0.01)
    
    # Update with the exact same goal
    update_working_set(project=str(tmp_path), goal="unchanged goal")
    
    # Verify mtime and updated_at did not change
    assert ws_path.stat().st_mtime == first_mtime
    assert load_working_set(project=str(tmp_path)).updated_at == first_updated_at


def test_resume_prefers_newer_working_set(engine, tmp_path):
    # Set handoff timestamp (old)
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "handoff-work",
        "updated_at": "2026-07-09T09:00:00Z"
    }), encoding="utf-8")
    
    # Set working set timestamp (new)
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-07-09T10:00:00Z",
        "workspace_root": str(tmp_path.resolve()),
        "active_work_item": "ws-work",
        "active_topic": "ws-topic",
        "active_task": "ws-task",
        "active_files": ["src/helper.py"],
        "active_concepts": ["concept-1"]
    }), encoding="utf-8")
    
    # Call restore_session_state
    state = engine.restore_session_state(str(tmp_path))
    assert state["resume_source"] == "working_set"
    assert state["active_work_item"] == "ws-work"
    assert state["active_topic"] == "ws-topic"
    assert state["active_task"] == "ws-task"
    assert "src/helper.py" in state["recommended_files"]
    assert "concept-1" in state["active_concepts"]


def test_resume_falls_back_to_handoff(engine, tmp_path):
    # Set handoff timestamp (new)
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "handoff-work",
        "active_topic": "handoff-topic",
        "active_task": "handoff-task",
        "updated_at": "2026-07-09T11:00:00Z"
    }), encoding="utf-8")
    
    # Set working set timestamp (old)
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-07-09T10:00:00Z",
        "workspace_root": str(tmp_path.resolve()),
        "active_work_item": "ws-work"
    }), encoding="utf-8")
    
    # Call restore_session_state
    state = engine.restore_session_state(str(tmp_path))
    assert state["resume_source"] == "session_handoff"
    assert state["active_work_item"] == "handoff-work"
    assert state["active_topic"] == "handoff-topic"
    assert state["active_task"] == "handoff-task"
    assert "active_concepts" not in state


def test_missing_working_set(engine, tmp_path):
    # No working set exists, but handoff does
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "handoff-only-work",
        "updated_at": "2026-07-09T11:00:00Z"
    }), encoding="utf-8")
    
    state = engine.restore_session_state(str(tmp_path))
    assert state["resume_source"] == "session_handoff"
    assert state["active_work_item"] == "handoff-only-work"


def test_corrupt_working_set(engine, tmp_path):
    # Set handoff
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "handoff-work",
        "updated_at": "2026-07-09T11:00:00Z"
    }), encoding="utf-8")
    
    # Write corrupt working set
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("{corrupt_json", encoding="utf-8")
    
    # Call restore_session_state (should fall back to handoff due to corruption)
    state = engine.restore_session_state(str(tmp_path))
    assert state["resume_source"] == "session_handoff"
    assert state["active_work_item"] == "handoff-work"
    assert "resume_reason" in state
    assert "corrupt" in state["resume_reason"]


def test_health_reports_resume_source(engine, tmp_path):
    # Write working set (newer)
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-07-09T12:00:00Z",
        "workspace_root": str(tmp_path.resolve()),
        "active_work_item": "health-ws"
    }), encoding="utf-8")
    
    # Write handoff (older)
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "health-handoff",
        "updated_at": "2026-07-09T11:00:00Z"
    }), encoding="utf-8")
    
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)
    assert report["resume_source"] == "working_set"
    assert "working_set_source" in report
    assert report["working_set"]["resume_source"] == "working_set"
    assert report["working_set"]["resume_reason"] == "working_set is newer than session-handoff"


def test_resume_is_read_only_regression(engine, tmp_path):
    # Set newer working set
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "updated_at": "2026-07-09T12:00:00Z",
        "workspace_root": str(tmp_path.resolve()),
        "active_work_item": "ws-ro"
    }), encoding="utf-8")
    
    # Set older handoff
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_content = json.dumps({
        "schema_version": "1.0.0",
        "active_work_item": "handoff-ro",
        "updated_at": "2026-07-09T11:00:00Z"
    })
    handoff_path.write_text(handoff_content, encoding="utf-8")
    
    # Get initial mtimes
    ws_mtime = ws_path.stat().st_mtime
    handoff_mtime = handoff_path.stat().st_mtime
    
    # Call restore_session_state
    state = engine.restore_session_state(str(tmp_path))
    
    # Verify no files were mutated (read-only regression check)
    assert ws_path.stat().st_mtime == ws_mtime
    assert handoff_path.stat().st_mtime == handoff_mtime
    assert handoff_path.read_text(encoding="utf-8") == handoff_content


def test_checkpoint_creation_milestones(engine, tmp_path):
    from oem_knowledge.runtime.working_set import create_checkpoint, list_checkpoints, update_working_set
    
    update_working_set(project=str(tmp_path), active_work_item="milestone-test")
    
    cp = create_checkpoint(reason="manual", project=str(tmp_path))
    assert cp is not None
    assert cp.exists()
    
    cps = list_checkpoints(project=str(tmp_path))
    assert len(cps) == 1
    assert cps[0]["checkpoint_reason"] == "manual"
    assert cps[0]["active_work_item"] == "milestone-test"


def test_checkpoint_restore_validation(engine, tmp_path):
    from oem_knowledge.runtime.working_set import create_checkpoint, restore_checkpoint, load_working_set, update_working_set
    
    update_working_set(project=str(tmp_path), active_work_item="initial-state")
    cp_path = create_checkpoint(reason="manual", project=str(tmp_path))
    
    update_working_set(project=str(tmp_path), active_work_item="modified-state")
    assert load_working_set(project=str(tmp_path)).active_work_item == "modified-state"
    
    success = restore_checkpoint(cp_path.name, project=str(tmp_path))
    assert success is True
    assert load_working_set(project=str(tmp_path)).active_work_item == "initial-state"


def test_checkpoint_restore_corruption_regression(engine, tmp_path):
    from oem_knowledge.runtime.working_set import create_checkpoint, restore_checkpoint, load_working_set, update_working_set
    from pathlib import Path
    
    update_working_set(project=str(tmp_path), active_work_item="clean-state")
    
    harness = engine._resolve_harness(str(tmp_path))
    checkpoints_dir = harness / "state" / "checkpoints"
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    
    corrupt_path = checkpoints_dir / "checkpoint_20260709_120000_111111.json"
    corrupt_path.write_text("{invalid_json", encoding="utf-8")
    
    invalid_schema_path = checkpoints_dir / "checkpoint_20260709_130000_222222.json"
    invalid_schema_path.write_text(json.dumps({"schema_version": 1, "missing_root": "value"}), encoding="utf-8")
    
    success = restore_checkpoint(corrupt_path.name, project=str(tmp_path))
    assert success is False
    assert load_working_set(project=str(tmp_path)).active_work_item == "clean-state"
    
    success2 = restore_checkpoint(invalid_schema_path.name, project=str(tmp_path))
    assert success2 is False
    assert load_working_set(project=str(tmp_path)).active_work_item == "clean-state"


def test_checkpoint_pruning_limit(engine, tmp_path):
    from oem_knowledge.runtime.working_set import create_checkpoint, list_checkpoints, update_working_set
    import time
    
    update_working_set(project=str(tmp_path), active_work_item="prune-test")
    
    for i in range(22):
        create_checkpoint(reason=f"step_{i}", project=str(tmp_path))
        time.sleep(0.005)
        
    cps = list_checkpoints(project=str(tmp_path))
    assert len(cps) == 20
    assert cps[0]["checkpoint_reason"] == "step_2"


def test_working_set_compaction_pruning(engine, tmp_path):
    from oem_knowledge.runtime.working_set import compact_working_set, load_working_set
    
    large_files = [f"file_{i}.py" for i in range(30)]
    large_concepts = [f"concept_{i}" for i in range(40)]
    large_memory_ids = [f"mem_{i}" for i in range(60)]
    large_blocked_by = [f"blocker_{i}" for i in range(15)]
    large_open_questions = [f"question_{i}" for i in range(15)]
    
    ws_path = engine.layout(str(tmp_path)).working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "workspace_root": str(tmp_path.resolve()),
        "active_files": large_files,
        "active_concepts": large_concepts,
        "active_memory_ids": large_memory_ids,
        "blocked_by": large_blocked_by,
        "open_questions": large_open_questions,
    }), encoding="utf-8")
    
    ws = load_working_set(project=str(tmp_path))
    assert len(ws.active_files) == 30
    assert len(ws.blocked_by) == 15
    assert len(ws.open_questions) == 15
    
    success = compact_working_set(project=str(tmp_path))
    assert success is True
    
    ws_after = load_working_set(project=str(tmp_path))
    assert len(ws_after.active_files) == 20
    assert len(ws_after.active_concepts) == 30
    assert len(ws_after.active_memory_ids) == 50
    assert len(ws_after.blocked_by) == 10
    assert len(ws_after.open_questions) == 10
    
    assert ws_after.active_files[-1] == "file_29.py"
    assert ws_after.active_files[0] == "file_10.py"
    assert ws_after.blocked_by[-1] == "blocker_14"
    assert ws_after.blocked_by[0] == "blocker_5"


def test_working_set_compaction_preserves_text(engine, tmp_path):
    from oem_knowledge.runtime.working_set import compact_working_set, load_working_set, update_working_set
    
    goal_text = "This is a long goal " * 50
    update_working_set(
        project=str(tmp_path),
        goal=goal_text,
        current_problem="Problem description",
    )
    
    success = compact_working_set(project=str(tmp_path))
    assert success is True
    
    ws = load_working_set(project=str(tmp_path))
    assert ws.goal == goal_text
    assert ws.current_problem == "Problem description"


def test_create_checkpoint_merges_handoff_json(engine, tmp_path):
    from oem_knowledge.runtime.working_set import create_checkpoint, list_checkpoints
    
    # Create handoff json
    handoff_path = engine._resolve_harness(str(tmp_path)) / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "primary_objective": "Test primary objective",
        "next_action": "Test next action",
        "active_work_item": "src/active.py",
        "active_topic": "Test active topic",
        "active_task": "Test active task",
        "completed": ["Completed item 1", "Completed item 2"],
        "key_decisions": ["Decision 1"],
    }), encoding="utf-8")

    cp_path = create_checkpoint(reason="manual", project=str(tmp_path))
    assert cp_path is not None
    assert cp_path.exists()

    # Load checkpoint data
    cp_data = json.loads(cp_path.read_text(encoding="utf-8"))
    assert cp_data["goal"] == "Test primary objective"
    assert cp_data["next_action"] == "Test next action"
    assert cp_data["active_work_item"] == "src/active.py"
    assert cp_data["active_topic"] == "Test active topic"
    assert cp_data["active_task"] == "Test active task"
    assert cp_data["completed_items"] == ["Completed item 1", "Completed item 2"]
    assert cp_data["decisions"] == ["Decision 1"]


def test_update_structured_handoff_preserves_custom_fields(engine, tmp_path):
    # Setup outcomes trigger and active handoff json
    harness = engine._resolve_harness(str(tmp_path))
    handoff_path = harness / "session-handoff.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "workspace_root": str(tmp_path),
        "primary_objective": "Original Objective",
        "completed": ["Wrote code", "Ran tests"],
        "key_decisions": ["Used sqlite"],
        "artifacts": ["main.py"]
    }), encoding="utf-8")

    engine.state.record_outcome(
        outcome="success",
        project=str(tmp_path),
        session_id="session_123",
        reason="test preservation"
    )

    # Read back handoff JSON and verify completed, key_decisions, artifacts are preserved
    assert handoff_path.exists()
    data = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert data["completed"] == ["Wrote code", "Ran tests"]
    assert data["key_decisions"] == ["Used sqlite"]
    assert data["artifacts"] == ["main.py"]




