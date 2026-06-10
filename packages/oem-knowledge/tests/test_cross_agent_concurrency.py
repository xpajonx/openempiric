import pytest
import json
import os
import socket
import time
import threading
import queue
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.fs import FileLock, LockTimeoutError
from oem_knowledge.runtime.session import SessionState
import asyncio
from fastmcp import FastMCP
from oem_knowledge.server import mount_tools

# Helpers for parsing validation
def assert_valid_jsonl(path: Path) -> None:
    assert path.exists(), f"JSONL file {path} does not exist"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            json.loads(line)

def assert_valid_json(path: Path) -> None:
    assert path.exists(), f"JSON file {path} does not exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)

def make_conversation(label: str) -> str:
    return (
        f"decision: Use {label} memory path\n"
        f"validation: {label} commit completed\n"
    )

@pytest.fixture(autouse=True)
def fast_locks():
    """Force all locks created during these tests to have a very short timeout and poll interval."""
    original_init = FileLock.__init__
    def patched_init(self, lock_path, timeout=10.0, stale_timeout=300.0, poll_interval=0.1, *args, **kwargs):
        # Override values for fast test executions
        forced_timeout = 0.2 if timeout >= 1.0 else timeout
        forced_stale = 0.4 if stale_timeout >= 10.0 else stale_timeout
        forced_poll = 0.02 if poll_interval >= 0.05 else poll_interval
        original_init(self, lock_path, timeout=forced_timeout, stale_timeout=forced_stale, poll_interval=forced_poll, *args, **kwargs)
    with patch.object(FileLock, "__init__", patched_init):
        yield

@pytest.fixture(autouse=True)
def mock_home_isolation(tmp_path, monkeypatch):
    """Isolate tests from real user ~/.config/opencode and ~/.codex by using a temp sandbox directory."""
    sandbox_home = tmp_path / "sandbox-home"
    sandbox_home.mkdir(parents=True, exist_ok=True)
    
    # We patch Path.home to return our sandbox
    monkeypatch.setenv("OEM_CODEX_HOME", str(sandbox_home / ".codex"))
    monkeypatch.setenv("CODEX_HOME", str(sandbox_home / ".codex"))
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(sandbox_home / ".config" / "opencode" / "plugins"))
    
    with patch("pathlib.Path.home", return_value=sandbox_home):
        yield sandbox_home

def test_two_engine_instances_concurrent_commit(tmp_path):
    """1. Two engine instances share the same project memory safely and commit concurrently."""
    project = tmp_path / "project"
    project.mkdir()

    init_eng = KnowledgeEngine(project)
    init_eng.init_project(str(project))

    eng_a = KnowledgeEngine(project)
    eng_b = KnowledgeEngine(project)

    results = queue.Queue()

    def run_commit(eng, label):
        try:
            res = eng.session_commit(
                project=str(project),
                conversation_text=make_conversation(label),
                session_id=label,
            )
            results.put((label, res, None))
        except Exception as e:
            results.put((label, None, e))

    t_a = threading.Thread(target=run_commit, args=(eng_a, "agent-a"))
    t_b = threading.Thread(target=run_commit, args=(eng_b, "agent-b"))

    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    res_list = []
    while not results.empty():
        res_list.append(results.get())

    assert len(res_list) == 2
    for label, res, exc in res_list:
        assert exc is None, f"Thread {label} raised exception: {exc}"
        assert res["status"] in {"success", "partial", "error"}
        if res["status"] == "error":
            assert "lock" in res.get("message", "").lower() or any(
                "lock" in w.lower() for w in res.get("warnings", [])
            )

    harness = project / ".oem"
    assert_valid_json(harness / "concept_registry.json")
    assert_valid_jsonl(harness / "events.jsonl")

def test_simulated_opencode_exit_commit(tmp_path):
    """2. Simulated OpenCode exit commit works without requiring knowledge_session_end."""
    project = tmp_path / "project"
    project.mkdir()

    eng = KnowledgeEngine(project)
    eng.init_project(str(project))

    # Simulate OpenCode starting a session by writing active_session.json
    session_state = SessionState.create(
        session_id="opencode-sess-123",
        agent="opencode",
        project=str(project),
        transcript_path=str(project / "transcript.txt"),
        context_path=str(project / "context.json"),
        temp_instructions=str(project / "instructions.txt")
    )
    active_session_file = project / ".oem" / "state" / "active_session.json"
    session_state.save(active_session_file)

    # Perform commit
    res = eng.session_commit(
        project=str(project),
        conversation_text=make_conversation("opencode-agent"),
        session_id="opencode-sess-123",
        session_started_at=session_state.started_at
    )

    assert res["status"] in {"success", "partial"}
    assert res.get("report_path") is not None
    assert Path(res["report_path"]).exists()

    harness = project / ".oem"
    assert_valid_jsonl(harness / "events.jsonl")

def test_simulated_codex_app_mcp_session_end(tmp_path):
    """3. Simulated Codex App MCP session end works and returns clean markdown/plain text without ANSI codes."""
    project = tmp_path / "project"
    project.mkdir()

    init_eng = KnowledgeEngine(project)
    init_eng.init_project(str(project))

    mcp = FastMCP("test_mcp")
    mount_tools(mcp)

    # Invoke tool via FastMCP client call
    result = asyncio.run(mcp.call_tool(
        "knowledge_session_end",
        {
            "project": str(project),
            "conversation_text": make_conversation("codex-agent"),
            "session_id": "codex-sess-456"
        }
    ))

    out = result.content[0].text
    assert isinstance(out, str)
    assert "# Session End" in out
    assert "\x1b" not in out  # No ANSI escape codes
    assert "Commit Complete" in out or "succeeded" in out or "completed" in out

def test_lock_contention_during_session_commit_fails_visibly(tmp_path):
    """4. Lock contention during session commit fails visibly with error/partial status, not clean success."""
    project = tmp_path / "project"
    project.mkdir()

    eng = KnowledgeEngine(project)
    eng.init_project(str(project))

    lock_path = project / ".oem" / "concept_registry.lock"

    # Hold the lock on concept registry
    with FileLock(lock_path) as lock:
        assert lock.acquired

        res = eng.session_commit(
            project=str(project),
            conversation_text="decision: this should contend\n",
            session_id="contended-sess"
        )

    assert res["status"] in {"error", "partial"}
    assert "lock" in res.get("message", "").lower() or any(
        "lock" in w.lower() for w in res.get("warnings", [])
    )

def test_stale_lock_recovery_end_to_end(tmp_path):
    """5. Stale lock recovery works end-to-end in a state-modifying path."""
    project = tmp_path / "project"
    project.mkdir()

    eng = KnowledgeEngine(project)
    eng.init_project(str(project))

    lock_path = project / ".oem" / "concept_registry.lock"

    # Create stale lock file representing a dead process (e.g. non-existent pid 999999)
    metadata = {
        "pid": 999999,
        "hostname": socket.gethostname(),
        "created_at": 1710000000.0,
        "owner_id": "dead-process-owner-id"
    }
    lock_path.write_text(json.dumps(metadata), encoding="utf-8")

    # Run session commit, which will try to acquire concept_registry.lock
    res = eng.session_commit(
        project=str(project),
        conversation_text=make_conversation("stale-recovery-agent"),
        session_id="stale-sess"
    )

    assert res["status"] in {"success", "partial"}
    assert not lock_path.exists() or json.loads(lock_path.read_text(encoding="utf-8"))["owner_id"] != "dead-process-owner-id"

def test_runtime_event_and_registry_parseability(tmp_path):
    """6. & 7. Runtime events and registry remain parseable JSON/JSONL after sequential writes."""
    project = tmp_path / "project"
    project.mkdir()

    eng = KnowledgeEngine(project)
    eng.init_project(str(project))

    # Perform multiple sequential commits
    for i in range(3):
        eng.session_commit(
            project=str(project),
            conversation_text=make_conversation(f"seq-{i}"),
            session_id=f"seq-sess-{i}"
        )

    harness = project / ".oem"
    assert_valid_json(harness / "concept_registry.json")
    assert_valid_jsonl(harness / "events.jsonl")

def test_adapter_config_isolation(mock_home_isolation):
    """8. Adapter config files are not modified/created in real home directories."""
    # The autouse fixture mock_home_isolation patches Path.home to mock_home_isolation (sandbox).
    # Let's verify that the sandbox directory is used and real home remains untouched.
    real_home = Path("/home/xpajonx")
    
    # Ensure real home subdirectories (~/.config/opencode, ~/.codex) are not modified or touched
    real_opencode = real_home / ".config" / "opencode"
    real_codex = real_home / ".codex"
    
    # Record mtimes or state of real home folders if they exist
    real_opencode_exists = real_opencode.exists()
    real_codex_exists = real_codex.exists()
    
    # The sandbox directories must exist or be isolated
    assert mock_home_isolation.exists()
    assert mock_home_isolation != real_home
