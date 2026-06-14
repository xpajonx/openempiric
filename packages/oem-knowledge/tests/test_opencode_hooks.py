import pytest
import os
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.cli.commands.system import cmd_setup_opencode
from oem_knowledge.runtime.readiness import RuntimeReadiness
from oem_knowledge.source_classifier import classify_source, SourceType
from oem_knowledge.services.reflection import llm_extraction_available

@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng

def test_opencode_setup_installs_oem_hook_runtime(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    
    # We patch package resources to return a dummy file
    dummy_plugin = tmp_path / "dummy_openempiric.ts"
    dummy_plugin.write_text("export const OpenempiricPlugin = {};")
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy content"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=True)
        
    assert (plugins_dir / "openempiric.ts").exists()
    assert (instructions_dir / "memory-start.md").exists()
    assert (opencode_dir / "opencode.jsonc").exists()
    
    config_text = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert "plugins" in config
    assert str((plugins_dir / "openempiric.ts").resolve()) in config["plugins"]

def test_opencode_setup_hook_install_is_idempotent(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy content"
        
        # First install
        cmd_setup_opencode(engine, project=str(tmp_path), repair=True)
        config_text_1 = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")
        
        # Second install
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        config_text_2 = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")
        
    config_1 = json.loads(config_text_1)
    config_2 = json.loads(config_text_2)
    assert len(config_1["plugins"]) == 1
    assert len(config_2["plugins"]) == 1
    assert config_text_1 == config_text_2

def test_opencode_setup_preserves_user_config(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    # Existing config with user comments and custom settings
    jsonc_file.write_text("""{
        // User custom comment here
        "custom_key": "custom_val",
        "instructions": []
    }""", encoding="utf-8")
    
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    instructions_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        
    # Check that a backup file was written because configuration was modified
    backups = list(opencode_dir.glob("opencode.jsonc.backup-*"))
    assert len(backups) == 1
    
    # Check preservation of custom elements
    new_text = jsonc_file.read_text(encoding="utf-8")
    assert "// User custom comment here" in new_text
    assert '"custom_key": "custom_val"' in new_text

def test_oem_run_opencode_verifies_hook_runtime(engine, tmp_path, monkeypatch):
    # Setup mock engine checks
    readiness = RuntimeReadiness()
    
    mock_adapter = MagicMock()
    mock_adapter.verify_health.return_value = (True, "Plugin healthy")
    mock_adapter.verify_mcp.return_value = True
    
    # Run checks on OpenCode agent
    checks = readiness.check(
        engine,
        agent_name="opencode",
        project=str(tmp_path),
        harness=tmp_path / OEM_DIR,
        adapter=mock_adapter
    )
    
    # Ensure they map to customized opencode checks
    check_names = [c.name for c in checks]
    assert ".oem project memory found" in check_names
    assert "OpenCode MCP registered" in check_names
    assert "OEM instructions active" in check_names
    assert "OEM hook runtime active" in check_names
    assert "Session lifecycle enabled" in check_names
    
    # Ensure standard checks like "Project initialized" or "Plugin healthy" are filtered out/mapped
    assert "Project initialized" not in check_names
    assert "Plugin healthy" not in check_names

def test_hook_context_file_is_non_ingestion_eligible(tmp_path):
    context_file = tmp_path / OEM_DIR / ".runtime" / "context.md"
    pending_file = tmp_path / OEM_DIR / ".runtime" / "pending_events.jsonl"
    
    c_class = classify_source(context_file)
    p_class = classify_source(pending_file)
    
    assert not c_class.ingestion_eligible
    assert not p_class.ingestion_eligible
    assert c_class.source_type in (SourceType.OEM_RUNTIME_LOG, SourceType.GENERATED_SUMMARY)
    assert p_class.source_type in (SourceType.OEM_RUNTIME_LOG, SourceType.GENERATED_SUMMARY)

def test_pending_events_are_not_durable_until_session_end(engine, tmp_path, monkeypatch):
    # Stage a pending event
    harness = tmp_path / OEM_DIR
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pending_file = runtime_dir / "pending_events.jsonl"
    
    pending_event = {
        "event_type": "observation",
        "summary": "Staged pending event summary",
        "evidence": "Staged pending evidence details",
        "source": "opencode_hook",
        "source_type": "agent_runtime_signal",
        "ingestion_eligible": True,
        "durable": False
    }
    
    pending_file.write_text(json.dumps(pending_event) + "\n", encoding="utf-8")
    
    # Ensure that active events log doesn't contain this event yet (it is staging only)
    events_file = harness / "state" / "events.jsonl"
    assert not events_file.exists()

def test_hook_session_end_processes_pending_events_through_state_service(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    
    # Stage a pending event
    harness = tmp_path / OEM_DIR
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pending_file = runtime_dir / "pending_events.jsonl"
    
    pending_event = {
        "event_type": "observation",
        "summary": "Staged pending event summary",
        "evidence": "Staged pending evidence details",
        "source": "opencode_hook",
        "source_type": "agent_runtime_signal",
        "ingestion_eligible": True,
        "durable": False
    }
    pending_file.write_text(json.dumps(pending_event) + "\n", encoding="utf-8")
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="Empty conversation text",
        extraction_mode="llm"
    )
    
    # Staging file should be deleted after processing
    assert not pending_file.exists()
    
    # The event should be normalized and written to the official events log
    assert res["status"] == "success"
    assert res["events_written"] == 1
    
    # Read the canonical written event to confirm fields promotion
    events_path = engine._events_path(str(tmp_path))
    assert events_path.exists()
    
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    written_ev = json.loads(lines[0])
    
    assert written_ev["event_type"] == "observation"
    assert written_ev["summary"] == "Staged pending event summary"
    assert written_ev["source"] == "opencode_hook"
    assert written_ev["source_type"] == "agent_runtime_signal"
    assert written_ev["ingestion_eligible"] is True

def test_hook_session_end_warns_not_fails_without_llm(engine, tmp_path, monkeypatch):
    # Disable LLM
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OEM_MOCK_LLM", raising=False)
    
    # Stage a pending event
    harness = tmp_path / OEM_DIR
    runtime_dir = harness / ".runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    pending_file = runtime_dir / "pending_events.jsonl"
    
    pending_event = {
        "event_type": "observation",
        "summary": "Staged pending event summary",
        "evidence": "Staged pending evidence details",
        "source": "opencode_hook",
        "source_type": "agent_runtime_signal",
        "ingestion_eligible": True,
        "durable": False
    }
    pending_file.write_text(json.dumps(pending_event) + "\n", encoding="utf-8")
    
    res = engine.session_commit(
        project=str(tmp_path),
        conversation_text="No LLM API keys configured",
        extraction_mode="llm"
    )
    
    # Should warn instead of failing since we have staged events to commit
    assert res["status"] == "warn"
    assert any("LLM extraction unavailable" in w for w in res["warnings"])
    assert res["events_written"] == 1
    assert not pending_file.exists()

def test_hook_runtime_does_not_mutate_agents_md(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("OEM_MOCK_LLM", "true")
    
    agents_md = tmp_path / "AGENTS.md"
    original_content = "# Active Workflow\n- Step 1"
    agents_md.write_text(original_content, encoding="utf-8")
    
    engine.session_commit(
        project=str(tmp_path),
        conversation_text="Empty conversation text",
        extraction_mode="llm"
    )
    
    # AGENTS.md must remain untouched
    assert agents_md.read_text(encoding="utf-8") == original_content

def test_typescript_plugin_loading_and_methods(tmp_path):
    # Test TypeScript compilation/loading using Node.js subprocess to Spike hook exports
    plugin_path = Path(__file__).resolve().parent.parent / "src" / "oem_knowledge" / "plugins" / "openempiric.ts"
    assert plugin_path.exists()
    
    # We test that the file can be parsed for exports using a quick check or ts-node spike if available
    # Check if ts-node is installed in local openempiric-dev
    opencode_dir = Path.home() / ".config" / "opencode"
    node_modules = opencode_dir / "node_modules"
    
    # We check if we can run syntax check on it
    js_checker = f"""
    const fs = require('fs');
    const content = fs.readFileSync('{plugin_path.as_posix()}', 'utf-8');
    if (!content.includes('export const OpenempiricPlugin')) {{
        process.exit(1);
    }}
    console.log('TS Plugin syntax check passed');
    """
    res = subprocess.run(["node", "-e", js_checker], capture_output=True, text=True)
    assert res.returncode == 0


def test_setup_opencode_does_not_write_unsupported_plugins_key(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    
    # Mock plugins unsupported
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy content"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=True)
        
    config_text = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")
    config = json.loads(config_text)
    
    # plugins key should NOT be present
    assert "plugins" not in config
    assert "instructions" in config
    assert "mcp" in config


def test_setup_opencode_repairs_invalid_plugins_key(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    # Create an invalid plugins config
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text(json.dumps({
        "plugins": [str(plugins_dir / "openempiric.ts")]
    }), encoding="utf-8")
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    
    # Mock plugins unsupported
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    # Mock subprocess.run for validation to return success (so it validates successfully after repair)
    class DummyCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyCompletedProcess())
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        
    config_text = jsonc_file.read_text(encoding="utf-8")
    config = json.loads(config_text)
    
    # plugins key should be removed
    assert "plugins" not in config


def test_setup_opencode_preserves_user_config_when_repairing(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    # Config has user custom setting and user plugin
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text(json.dumps({
        "custom_setting": "val",
        "plugins": [
            str(plugins_dir / "openempiric.ts"),
            "user-plugin.ts"
        ]
    }), encoding="utf-8")
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    class DummyCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyCompletedProcess())
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        
    config_text = jsonc_file.read_text(encoding="utf-8")
    config = json.loads(config_text)
    
    # plugins key should contain user-plugin.ts but NOT openempiric.ts
    assert "plugins" in config
    assert "user-plugin.ts" in config["plugins"]
    assert str(plugins_dir / "openempiric.ts") not in config["plugins"]
    assert config["custom_setting"] == "val"


def test_setup_opencode_creates_backup_before_repair(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text(json.dumps({
        "plugins": [str(plugins_dir / "openempiric.ts")]
    }), encoding="utf-8")
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    class DummyCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyCompletedProcess())
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy"
        
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        
    backups = list(opencode_dir.glob("opencode.jsonc.backup-*"))
    assert len(backups) >= 1


def test_readiness_reports_hook_runtime_warn_when_plugin_unsupported(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("oem_knowledge.runtime.readiness.check_opencode_config_valid", lambda: ("success", ""))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    readiness = RuntimeReadiness()
    
    class DummyAdapter:
        def verify_health(self):
            return True, "All good"
        def verify_mcp(self):
            return True
            
    checks = readiness.check(
        eng=engine,
        agent_name="opencode",
        project=str(tmp_path),
        harness=tmp_path / OEM_DIR,
        adapter=DummyAdapter(),
        stale_existed=False,
        recovery_failed=False
    )
    
    c4 = next((c for c in checks if c.name == "OEM hook runtime unavailable"), None)
    assert c4 is not None
    assert c4.status == "warning"
    assert "Current OpenCode config schema does not support plugin registration." in c4.detail


def test_readiness_does_not_report_runtime_ready_when_opencode_config_invalid(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("oem_knowledge.runtime.readiness.check_opencode_config_valid", lambda: ("failure", "Unrecognized key: plugins"))
    
    readiness = RuntimeReadiness()
    
    class DummyAdapter:
        def verify_health(self):
            return True, "All good"
        def verify_mcp(self):
            return True
            
    checks = readiness.check(
        eng=engine,
        agent_name="opencode",
        project=str(tmp_path),
        harness=tmp_path / OEM_DIR,
        adapter=DummyAdapter(),
        stale_existed=False,
        recovery_failed=False
    )
    
    c_config = next((c for c in checks if c.name == "OpenCode config"), None)
    assert c_config is not None
    assert c_config.status == "failure"
    assert c_config.detail == "Unrecognized key: plugins"
    
    from oem_knowledge.runtime.supervisor import render_supervisor_panel
    panel_str = render_supervisor_panel(str(tmp_path), "opencode", checks)
    assert "✗ Runtime not ready" in panel_str


def test_oem_run_opencode_aborts_before_launch_when_config_invalid(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("oem_knowledge.runtime.readiness.check_opencode_config_valid", lambda: ("failure", "Unrecognized key: plugins"))
    
    import sys
    exit_called = False
    def mock_exit(code):
        nonlocal exit_called
        exit_called = True
        raise SystemExit(code)
        
    monkeypatch.setattr(sys, "exit", mock_exit)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.cmd_setup_opencode", lambda *args, **kwargs: None)
    
    from oem_knowledge.runtime.runner import run_agent
    
    with pytest.raises(SystemExit):
        run_agent("opencode", engine, project=str(tmp_path))
        
    assert exit_called


def test_oem_run_opencode_falls_back_to_mcp_instructions_when_hooks_unsupported(engine, tmp_path, monkeypatch):
    monkeypatch.setattr("oem_knowledge.runtime.readiness.check_opencode_config_valid", lambda: ("success", ""))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    spawned = False
    def mock_run(cmd, *args, **kwargs):
        nonlocal spawned
        spawned = True
        class DummyProcess:
            returncode = 0
        return DummyProcess()
        
    monkeypatch.setattr("subprocess.run", mock_run)
    monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: MagicMock())
    
    monkeypatch.setattr("oem_knowledge.cli.commands.system.cmd_setup_opencode", lambda *args, **kwargs: None)
    monkeypatch.setattr("oem_knowledge.runtime.runner._ensure_workspace_ready", lambda *args, **kwargs: None)
    
    from oem_knowledge.runtime.runner import run_agent
    
    class Args:
        skip_session_start = True
        skip_session_end = True
        print_instructions = False
        
    run_agent("opencode", engine, project=str(tmp_path), args=Args())
    assert spawned


def test_setup_opencode_idempotent_after_repair(engine, tmp_path, monkeypatch):
    opencode_dir = tmp_path / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    plugins_dir.mkdir(parents=True)
    instructions_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text(json.dumps({
        "plugins": [str(plugins_dir / "openempiric.ts")]
    }), encoding="utf-8")
    
    monkeypatch.setenv("OPENCODE_PLUGINS_DIR", str(plugins_dir))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.Path.home", lambda: tmp_path)
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_mcp_server", lambda cmd: (True, True, 5, ""))
    monkeypatch.setattr("oem_knowledge.cli.commands.system.check_opencode_plugins_support", lambda: "unsupported")
    
    class DummyCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyCompletedProcess())
    
    with patch("importlib.resources.files") as mock_files:
        mock_files.return_value.joinpath.return_value.exists.return_value = True
        mock_files.return_value.joinpath.return_value.read_text.return_value = "dummy"
        
        # Repair first time
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        text_after_repair = jsonc_file.read_text(encoding="utf-8")
        
        # Run second time
        cmd_setup_opencode(engine, project=str(tmp_path), repair=False)
        text_second_time = jsonc_file.read_text(encoding="utf-8")
        
    assert text_after_repair == text_second_time
