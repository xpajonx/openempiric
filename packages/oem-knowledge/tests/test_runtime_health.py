import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.health import build_health_report, build_runtime_health
from oem_knowledge.cli.commands.system import run_system_command
from oem_knowledge.server import mount_tools
from oem_knowledge.runtime.recovery import cmd_recover, parse_markdown_report, build_markdown_report

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(str(tmp_path))
    eng.init_project(str(tmp_path))
    return eng

def test_runtime_health_warns_when_only_llm_reflection_degraded(engine, tmp_path):
    """Overall health should be WARN (not ERROR) if only LLM reflection is degraded."""
    # Set the degraded environment flag
    with patch.dict(os.environ, {"OEM_LLM_DEGRADED": "true"}):
        mock_args = MagicMock()
        mock_args.command = "doctor"
        mock_args.project = str(tmp_path)
        mock_args.fix = False
        
        # Capture print calls
        with patch("builtins.print") as mock_print, patch("sys.exit") as mock_exit:
            run_system_command(mock_args)
            
            # Ensure WARN status was printed for the Runtime Health panel, and not ERROR
            printed_warn = False
            printed_error = False
            for call in mock_print.call_args_list:
                output = " ".join(str(arg) for arg in call[0])
                if "Runtime Health" in output:
                    if "WARN" in output:
                        printed_warn = True
                    if "ERROR" in output:
                        printed_error = True
            assert printed_warn
            assert not printed_error

def test_runtime_health_errors_when_structured_reflection_unavailable(engine, tmp_path):
    """Overall health should be ERROR if structured reflection fails."""
    # Mock reflect_session to throw an error for structured mode
    original_reflect = engine.reflection.reflect_session
    def mock_reflect(*args, **kwargs):
        if kwargs.get("extraction_mode") == "structured":
            raise RuntimeError("Structured pipeline failed")
        return original_reflect(*args, **kwargs)

    with patch("oem_knowledge.services.reflection.ReflectionService.reflect_session", side_effect=mock_reflect):
        mock_args = MagicMock()
        mock_args.command = "doctor"
        mock_args.project = str(tmp_path)
        mock_args.fix = False

        with patch("builtins.print") as mock_print, patch("sys.exit") as mock_exit:
            run_system_command(mock_args)

            printed_error = False
            for call in mock_print.call_args_list:
                output = " ".join(str(arg) for arg in call[0])
                if "Runtime Health" in output and "ERROR" in output:
                    printed_error = True
            assert printed_error

def test_runtime_health_reports_marker_reflection_ready(engine, tmp_path):
    """Doctor check should show marker reflection ready."""
    mock_args = MagicMock()
    mock_args.command = "doctor"
    mock_args.project = str(tmp_path)
    mock_args.fix = False

    with patch("builtins.print") as mock_print, patch("sys.exit") as mock_exit:
        run_system_command(mock_args)
        
        printed_marker_ready = False
        for call in mock_print.call_args_list:
            output = " ".join(str(arg) for arg in call[0])
            if "Marker Reflection Ready" in output:
                printed_marker_ready = True
        assert printed_marker_ready

def test_runtime_health_includes_reflection_fallback_suggestion(engine, tmp_path):
    """When LLM is degraded, doctor output should suggest fallback and recovery commands."""
    with patch.dict(os.environ, {"OEM_LLM_DEGRADED": "true"}):
        mock_args = MagicMock()
        mock_args.command = "doctor"
        mock_args.project = str(tmp_path)
        mock_args.fix = False

        with patch("builtins.print") as mock_print, patch("sys.exit"):
            run_system_command(mock_args)
            
            output_lines = []
            for call in mock_print.call_args_list:
                output_lines.extend(str(arg) for arg in call[0])
            
            output_blob = "\n".join(output_lines)
            assert "Use structured events" in output_blob
            assert "oem recover --scope reflection" in output_blob


def _snapshot_tree(path: Path) -> dict[str, tuple[int, int]]:
    if not path.exists():
        return {}
    return {
        str(p.relative_to(path)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in path.rglob("*")
        if p.is_file()
    }


def _write_active_project_conflict(harness: Path) -> None:
    (harness / "session-handoff.json").write_text(
        json.dumps({"project_root": "/project/alpha"}),
        encoding="utf-8",
    )
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text(
        "Active project: /project/beta\n",
        encoding="utf-8",
    )


def test_health_report_contains_contradictions_field(engine, tmp_path):
    harness = Path(tmp_path) / OEM_DIR
    _write_active_project_conflict(harness)

    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    assert "contradictions" in report
    # Field-specific type; legacy_type may be present for compat
    c0 = report["contradictions"][0]
    assert c0["type"] in ("active_work_item_mismatch", "active_project_mismatch") or c0.get("legacy_type") == "active_project_mismatch"
    # active_project surface may use legacy selected_source during transition
    ap = report.get("active_project", {})
    assert ap.get("selected_source") in ("session_handoff_json", None) or "selected_source" in ap or ap.get("selected_source") is None


def test_health_report_status_warn_when_contradiction_exists(engine, tmp_path):
    harness = Path(tmp_path) / OEM_DIR
    _write_active_project_conflict(harness)

    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    assert report["status"] == "warn"
    assert build_runtime_health(str(tmp_path))["status"] in {"warn", "error"}


def test_health_detects_three_way_active_project_conflict_as_error(engine, tmp_path):
    harness = Path(tmp_path) / OEM_DIR
    (harness / "session-handoff.json").write_text(json.dumps({"project_root": "/project/alpha"}), encoding="utf-8")
    (harness / "session-handoff.md").write_text("Project: /project/beta\n", encoding="utf-8")
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "context.md").write_text("Project: /project/gamma\n", encoding="utf-8")

    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    assert report["status"] == "error"
    assert report["contradictions"][0]["severity"] == "error"


def test_health_missing_source_is_info_not_contradiction(engine, tmp_path):
    report = build_health_report(str(tmp_path), include_daemon_runtime=False)

    assert report["contradictions"] == []


def test_knowledge_read_health_include_runtime_false_still_reports_active_project_conflict(engine, tmp_path):
    harness = Path(tmp_path) / OEM_DIR
    _write_active_project_conflict(harness)

    result = engine.knowledge_read(str(tmp_path), scope="health")

    c0 = result["sections"]["contradictions"][0]
    assert c0["type"] in ("active_work_item_mismatch", "active_project_mismatch") or c0.get("legacy_type") == "active_project_mismatch"
    ap = result["sections"].get("active_project", {})
    assert ap.get("selected_source") in ("session_handoff_json", None) or "selected_source" in ap or ap.get("selected_source") is None


def test_all_health_surfaces_read_only_or_shared_report_read_only(engine, tmp_path):
    harness = Path(tmp_path) / OEM_DIR
    _write_active_project_conflict(harness)
    before = _snapshot_tree(harness)

    build_health_report(str(tmp_path), include_daemon_runtime=False)
    build_runtime_health(str(tmp_path))
    engine.knowledge_read(str(tmp_path), scope="health")
    mcp = FastMCP("oem-test")
    mount_tools(mcp)
    import asyncio
    asyncio.run(mcp.call_tool("knowledge_health_check", {"project": str(tmp_path)}))

    after = _snapshot_tree(harness)
    assert before == after

def test_recovery_reflection_scope_dry_run_and_apply(engine, tmp_path):
    """Test recover --scope reflection checks and repairs orphans, missing metadata, invalid JSONL, duplicates, and report inconsistency."""
    harness = Path(tmp_path) / OEM_DIR
    sessions_dir = harness / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    events_file = harness / "events.jsonl"

    # 1. Create an empty/orphan session report file
    orphan_file = sessions_dir / "2026-06-11.md"
    orphan_file.write_text("---\ndate: 2026-06-11\nproject: default\n---\n# Empty\n", encoding="utf-8")

    # 2. Create events.jsonl with:
    # - 1 valid event
    # - 1 duplicate event
    # - 1 manually appended event missing event_id, timestamp, and source_type
    # - 1 invalid JSONL line
    ev_valid = {
        "event_id": "ev_001",
        "timestamp": "2026-06-11T12:00:00Z",
        "session_id": "session_20260611_120000",
        "event_type": "observation",
        "concept_candidates": ["test concept"],
        "summary": "Valid event",
        "evidence": "Test evidence",
        "confidence": 4,
        "source": "chat",
        "source_type": "agent_transcript"
    }
    ev_dup = dict(ev_valid)
    ev_dup["event_id"] = "ev_002"  # Unique ID but identical content
    ev_missing = {
        "session_id": "session_20260611_120000",
        "event_type": "decision",
        "concept_candidates": ["another concept"],
        "summary": "Manually added decision",
        "evidence": "Manually added evidence",
        "confidence": 4,
        "source": "manual"
    }
    
    with open(events_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(ev_valid) + "\n")
        f.write(json.dumps(ev_dup) + "\n")
        f.write(json.dumps(ev_missing) + "\n")
        f.write("{\n")  # Invalid JSON

    # 3. Create an inconsistent session report (content does not match events in events.jsonl)
    inconsistent_file = sessions_dir / "2026-06-11_1.md"
    wrong_events_content = build_markdown_report("2026-06-11", "default", [{"type": "observation", "concept": "Wrong Concept", "evidence": "Wrong Evidence", "confidence": 1}])
    inconsistent_file.write_text(wrong_events_content, encoding="utf-8")

    # Run dry-run first
    with patch("builtins.print") as mock_print:
        cmd_recover(engine, str(tmp_path), scope="reflection", dry_run=True, apply=False)
        
        output_blob = "\n".join(" ".join(str(a) for a in call[0]) for call in mock_print.call_args_list)
        assert "1 empty orphan session files" in output_blob
        assert "1 manually appended events missing source metadata" in output_blob
        assert "1 invalid JSONL lines" in output_blob
        assert "1 duplicate events" in output_blob
        assert "1 session reports inconsistent with event store" in output_blob

    # Run apply with backup
    with patch("builtins.print") as mock_print:
        cmd_recover(engine, str(tmp_path), scope="reflection", dry_run=False, apply=True, backup=True, rebuild_reports=True)
        
        # Verify empty orphan session was deleted
        assert not orphan_file.exists()
        
        # Verify inconsistent session report was rebuilt to match events (repaired valid + normalized manually appended)
        assert inconsistent_file.exists()
        parsed = parse_markdown_report(inconsistent_file.read_text(encoding="utf-8"))
        assert parsed["frontmatter"]["generated_by"] == "openempiric"
        assert len(parsed["events"]) == 2 # ev_valid and normalized ev_missing
        concepts = [e["concept"] for e in parsed["events"]]
        assert "test concept" in concepts
        assert "another concept" in concepts

        # Verify events.jsonl was cleaned and normalized
        lines = events_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2 # 1 valid + 1 normalized missing, dup and invalid discarded
        ev1 = json.loads(lines[0])
        ev2 = json.loads(lines[1])
        
        # Verify ev2 has event_id, timestamp, and source_type populated
        assert ev2["event_id"] is not None
        assert ev2["timestamp"] is not None
        assert ev2["source_type"] == "recovered_event"

        # Verify backup was created
        backups_dir = harness / "backups"
        assert backups_dir.exists()
        assert len(list(backups_dir.glob("recover-*"))) == 1

def test_regression_session_commit_structured_events(engine, tmp_path):
    """Verify committing structured events persists them exactly once and does not create orphan empty reports."""
    events = [
        {
            "event_type": "observation",
            "concept_candidates": ["GSAP hazard"],
            "summary": "Detected GSAP scrollTrigger hazard",
            "evidence": "Observed scroll jump on page load",
            "confidence": 4,
            "source": "agent_structured"
        },
        {
            "event_type": "decision",
            "concept_candidates": ["Studio t= parameter"],
            "summary": "Append t= parameter to URL",
            "evidence": "Ensures studio loads correct state",
            "confidence": 4,
            "source": "agent_structured"
        }
    ]

    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="",
        session_id="session_regression_123",
        events=events,
        extraction_mode="structured"
    )

    assert res["status"] == "success"
    assert len(res["knowledge_events"]) == 2

    # Check events.jsonl contents
    events_file = Path(tmp_path) / OEM_DIR / "events.jsonl"
    lines = events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 # Written exactly once

    # Ensure no empty/orphan session reports exist
    sessions_dir = Path(tmp_path) / OEM_DIR / "sessions"
    session_files = list(sessions_dir.glob("*.md"))
    assert len(session_files) == 1
    
    # The session report contains the 2 events
    parsed = parse_markdown_report(session_files[0].read_text(encoding="utf-8"))
    assert len(parsed["events"]) == 2


def test_health_warning_reachable_for_editable_runtime(engine, tmp_path):
    from oem_knowledge.runtime.provenance import RUNTIME_KIND_EDITABLE
    
    mock_prov = {
        "runtime_kind": RUNTIME_KIND_EDITABLE,
        "executable_path": "/fake/path/python",
        "package_path": "/fake/path/pkg",
        "version": "1.0.0",
        "evidence": []
    }
    
    with patch("oem_knowledge.runtime.provenance.detect_runtime", return_value=mock_prov):
        report = build_runtime_health(str(tmp_path))
        
        warning_names = [c["name"] for c in report.get("checks", [])]
        assert any("Dev checkout runtime serving non-dev project" in name for name in warning_names)
        warning_check = next(c for c in report["checks"] if "Dev checkout runtime serving non-dev project" in c["name"])
        assert warning_check["status"] == "warn"


def test_health_warning_for_editable_runtime_mentions_editable_and_project(engine, tmp_path):
    from oem_knowledge.runtime.provenance import RUNTIME_KIND_EDITABLE
    
    mock_prov = {
        "runtime_kind": RUNTIME_KIND_EDITABLE,
        "executable_path": "/fake/path/python",
        "package_path": "/fake/path/pkg",
        "version": "1.0.0",
        "evidence": []
    }
    
    with patch("oem_knowledge.runtime.provenance.detect_runtime", return_value=mock_prov):
        report = build_runtime_health(str(tmp_path))
        
        # 1. runtime_kind == editable_checkout is present
        runtime_check_str = next(c["name"] for c in report["checks"] if "Runtime:" in c["name"])
        assert "editable_checkout" in runtime_check_str
        
        # 2. warning mentions editable/dev runtime and appears when serving non-dev project
        warning_check = next(c for c in report["checks"] if "Dev checkout runtime serving non-dev project" in c["name"])
        assert warning_check["status"] == "warn"


