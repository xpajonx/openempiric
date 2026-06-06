from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from oem_knowledge.cli import main
from oem_knowledge.engine import KnowledgeEngine


def test_session_recovery_flow(tmp_path):
    # Setup paths
    harness = tmp_path / ".oem"
    state_dir = harness / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    
    # Write virtual pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[project]\nname = \"oem-mcp\"\n", encoding="utf-8")

    # Mocks for subprocess and agent adapters
    mock_run = MagicMock()

    # Define a custom mock subprocess.run that writes the transcript path file and then exits
    def run_side_effect(*args, **kwargs):
        # Write dummy transcript file so recovery can find it
        transcript_file = state_dir / "chat_dummy.md"
        transcript_file.write_text("User: query info\nAgent: concept_001 resolved\n", encoding="utf-8")

    mock_run.side_effect = run_side_effect

    # Mock session_commit to fail on the first run (simulating a crash) and succeed on recovery
    call_count = 0
    def commit_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Simulated crash")
        return {
            "status": "success",
            "report_path": str(tmp_path / "sessions/report.md"),
            "knowledge_events": [],
            "materialized_log": [],
            "links_updated": 0,
            "index_stats": {}
        }

    # 1. Start session by mocking `subprocess.run` and `subprocess.Popen`
    with patch("subprocess.run", mock_run), patch("subprocess.Popen") as mock_popen:
        with patch.object(KnowledgeEngine, "session_commit", side_effect=commit_side_effect):
            with patch.object(sys, "argv", ["oem", "run", "custom-agent", "--project", str(tmp_path)]):
                try:
                    main()
                except SystemExit:
                    pass

    active_session_file = state_dir / "active_session.json"
    assert active_session_file.exists()

    # Read state object
    session_data = json.loads(active_session_file.read_text(encoding="utf-8"))
    assert session_data["status"] == "failed"  # status should be failed since commit raised
    assert session_data["agent"] == "custom-agent"
    
    # 2. Check recover --status
    with patch.object(sys, "argv", ["oem", "recover", "--status", "--project", str(tmp_path)]):
        with patch("builtins.print") as mock_print:
            try:
                main()
            except SystemExit:
                pass
            # Verify status output includes active session details
            output_found = False
            for call in mock_print.call_args_list:
                args = call[0]
                if args and any("Active Session Status" in str(arg) for arg in args):
                    output_found = True
                    break
            assert output_found

    # 3. Check oem recover (successful commit on second call)
    # Write a dummy transcript matching transcript_path
    t_file = Path(session_data["transcript_path"])
    t_file.parent.mkdir(parents=True, exist_ok=True)
    t_file.write_text("User: query info\nAgent: concept_001 resolved\n", encoding="utf-8")

    with patch.object(KnowledgeEngine, "session_commit", side_effect=commit_side_effect):
        with patch.object(sys, "argv", ["oem", "recover", "--project", str(tmp_path)]):
            try:
                main()
            except SystemExit:
                pass

    # Active session file should be unlinked upon recovery completion
    assert not active_session_file.exists()


def test_session_recovery_abort(tmp_path):
    harness = tmp_path / ".oem"
    state_dir = harness / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    
    # Write a pre-existing active session file (unfinished)
    active_session_file = state_dir / "active_session.json"
    session_state = {
        "session_id": "test_sess_123",
        "agent": "opencode",
        "status": "running",
        "started_at": 1000.0,
        "project": str(tmp_path),
        "transcript_path": str(state_dir / "chat_test.md"),
        "context_path": str(state_dir / "context.json"),
        "temp_instructions": str(state_dir / "instructions.md")
    }
    active_session_file.write_text(json.dumps(session_state), encoding="utf-8")

    # Create dummy temp files
    Path(session_state["context_path"]).write_text("{}", encoding="utf-8")
    Path(session_state["temp_instructions"]).write_text("instructions", encoding="utf-8")

    # Check abort unlinks session state and cleans up
    with patch.object(sys, "argv", ["oem", "recover", "--abort", "--project", str(tmp_path)]):
        try:
            main()
        except SystemExit:
            pass

    assert not active_session_file.exists()
    assert not Path(session_state["context_path"]).exists()
    assert not Path(session_state["temp_instructions"]).exists()
