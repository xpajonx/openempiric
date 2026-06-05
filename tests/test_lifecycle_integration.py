from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from oem_knowledge.cli import main
from oem_knowledge.runtime.session import SessionState

def test_lifecycle_integration(tmp_path):
    """
    Test the full oem run lifecycle using black-box CLI invocation:
    oem run -> fake transcript -> session commit -> registry update -> outcome recorded.
    """
    # 1. Initialize project harness
    with patch.object(sys, "argv", ["oem", "init", str(tmp_path)]):
        main()
    
    harness = tmp_path / ".oem"
    assert harness.is_dir()
    
    # 2. Mock subprocess.run to simulate agent execution.
    # The mock will write the transcript to the expected location.
    def mock_subprocess_run(cmd, *args, **kwargs):
        # Retrieve the session ID from the environment
        env = kwargs.get("env", {})
        session_id = env.get("OEM_SESSION_ID")
        assert session_id is not None
        
        # Determine expected transcript path
        transcript_path = Path(harness / "state" / f"chat_{session_id}.md")
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write fake transcript with knowledge events
        transcript_content = (
            "decision: Integrate Python 3.12 runtime.\n"
            "failure: Port conflict on 8080 during integration test.\n"
        )
        transcript_path.write_text(transcript_content, encoding="utf-8")
        
        # Verify active session state exists and is marked as "running"
        active_session_file = harness / "state" / "active_session.json"
        assert active_session_file.exists()
        session_state = SessionState.load(active_session_file)
        assert session_state is not None
        assert session_state.status == "running"
        assert session_state.agent == "mock-agent"
        
        # Return mock process exit
        return MagicMock(returncode=0)

    # 3. Invoke oem run via patched sys.argv
    with patch("subprocess.run", side_effect=mock_subprocess_run):
        with patch.object(sys, "argv", ["oem", "run", "mock-agent", "--project", str(tmp_path)]):
            main()

    # 4. Verify post-session artifacts
    # Active session file should be unlinked/deleted on success
    active_session_file = harness / "state" / "active_session.json"
    assert not active_session_file.exists()

    # Verify outcomes.jsonl
    outcomes_file = harness / "state" / "outcomes.jsonl"
    assert outcomes_file.exists()
    outcomes_lines = outcomes_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(outcomes_lines) >= 1
    last_outcome = json.loads(outcomes_lines[-1])
    assert last_outcome["outcome"] == "success"
    assert last_outcome["schema_version"] == 1
    assert "session_id" in last_outcome

    # Verify events.jsonl
    events_file = harness / "events.jsonl"
    assert events_file.exists()
    events_content = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(events_content) >= 2  # We had 1 decision and 1 failure
    
    events = [json.loads(line) for line in events_content]
    event_types = [ev["event_type"] for ev in events]
    assert "decision" in event_types
    assert "failure" in event_types

    # Verify registry updates
    registry_file = harness / "concept_registry.json"
    assert registry_file.exists()
    registry = json.loads(registry_file.read_text(encoding="utf-8"))
    assert len(registry) >= 2  # The 2 concepts should be registered
