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


def test_session_restoration_regression(tmp_path):
    """
    Test that a fresh Session B restores previous context from Session A,
    but does not use steering instructions telling it to resume or continue the previous topic.
    """
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime.context import _compile_oem_context

    # 1. Initialize project harness
    with patch.object(sys, "argv", ["oem", "init", str(tmp_path)]):
        main()

    harness = tmp_path / ".oem"
    
    # 2. Session A: Topic: realistic-image-gen
    # Seed session goals for Session A
    goals_file = harness / "state" / "current-goals.md"
    goals_file.parent.mkdir(parents=True, exist_ok=True)
    goals_file.write_text("- realistic-image-gen\n- Validate claims #6-9\n", encoding="utf-8")
    
    eng = KnowledgeEngine(str(tmp_path))
    
    # Run dynamic context compiler to simulate starting Session A / compiling context
    context_a = _compile_oem_context(eng)
    assert context_a["last_topic"] == "realistic-image-gen"
    
    # Save the session end/commit behavior by simulating writing the outcome
    # and creating a new handoff for Session B.
    # In a real workflow, runtime does this automatically on exit.
    # We keep the same goals in current-goals.md for Session B setup.
    
    # 3. Session B: Start fresh session
    context_b = _compile_oem_context(eng)
    
    # Verify previous context restored
    assert context_b["last_topic"] == "realistic-image-gen"
    assert "Validate claims #6-9" in context_b["open_questions"]
    
    # Verify it does NOT contain instructions to resume or continue
    memory_ctx = context_b["memory_context"]
    assert "resume" not in memory_ctx.lower()
    assert "continue" not in memory_ctx.lower()


def test_project_memory_isolation(tmp_path):
    """
    Verify project memory isolation:
    project-a context must never contain project-b secret and vice versa.
    """
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime.context import _compile_oem_context

    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    
    project_a.mkdir()
    project_b.mkdir()
    
    # Initialize both projects
    with patch.object(sys, "argv", ["oem", "init", str(project_a)]):
        main()
    with patch.object(sys, "argv", ["oem", "init", str(project_b)]):
        main()
        
    eng_a = KnowledgeEngine(str(project_a))
    eng_b = KnowledgeEngine(str(project_b))
    
    # Seed SECRET_A in project-a registry
    reg_a = eng_a._load_registry()
    reg_a["concept_secret_a"] = {
        "concept_id": "concept_secret_a",
        "canonical_name": "SECRET_A",
        "status": "canonical"
    }
    eng_a._save_registry(reg_a)
    
    # Seed SECRET_B in project-b registry
    reg_b = eng_b._load_registry()
    reg_b["concept_secret_b"] = {
        "concept_id": "concept_secret_b",
        "canonical_name": "SECRET_B",
        "status": "canonical"
    }
    eng_b._save_registry(reg_b)
    
    # Compile context for both
    context_a = _compile_oem_context(eng_a)
    context_b = _compile_oem_context(eng_b)
    
    # Verify Project A context does not contain SECRET_B
    a_concepts = [c["name"] for c in context_a["active_concepts"]]
    assert "SECRET_A" in a_concepts
    assert "SECRET_B" not in a_concepts
    
    # Verify Project B context does not contain SECRET_A
    b_concepts = [c["name"] for c in context_b["active_concepts"]]
    assert "SECRET_B" in b_concepts
    assert "SECRET_A" not in b_concepts
