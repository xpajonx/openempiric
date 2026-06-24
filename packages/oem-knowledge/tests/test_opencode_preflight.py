import os
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.source_classifier import is_ingestion_eligible
from oem_knowledge.runtime.config import _REPO_ROOT


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir, engine
    shutil.rmtree(project_dir, ignore_errors=True)


def run_hook(tmp_path, hook_name, project_path, prompt_text=None, env=None):
    plugin_path = Path(__file__).resolve().parent.parent / "src" / "oem_knowledge" / "plugins" / "openempiric.ts"

    driver_content = f"""
import * as path from "path";
import * as fs from "fs";

async function main() {{
  const pluginPath = "{str(plugin_path.resolve())}";
  const hookName = "{hook_name}";
  const projectRoot = "{str(project_path.resolve())}";
  const promptText = {json.dumps(prompt_text) if prompt_text else "null"};
  
  const {{ OpenempiricPlugin }} = require(pluginPath);
  
  const pluginInstance = await OpenempiricPlugin({{}}, {{}});
  
  if (hookName === "config") {{
    const config = {{ directory: projectRoot, instructions: [] }};
    await pluginInstance.config(config);
    console.log(JSON.stringify(config));
  }} else if (hookName === "tui.prompt.append") {{
    const msgInput = {{ content: promptText }};
    const msgOutput = {{}};
    await pluginInstance["tui.prompt.append"](msgInput, msgOutput);
    console.log("SUCCESS:" + JSON.stringify(msgInput));
  }}
}}

main().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
    driver_file = tmp_path / "driver.ts"
    driver_file.write_text(driver_content, encoding="utf-8")

    cmd_env = dict(os.environ)
    cmd_env["OEM_PROJECT_ROOT"] = str(project_path.resolve())
    if env:
        cmd_env.update(env)

    # Force repo dir lookup to current repo
    cmd_env["OPENEMPIRIC_DIR"] = str(_REPO_ROOT.resolve())
    # Add virtualenv path to subprocess environment PATH so `uv` is found
    if "VIRTUAL_ENV" in os.environ:
        cmd_env["PATH"] = str(Path(os.environ["VIRTUAL_ENV"]) / "bin") + os.pathsep + os.environ.get("PATH", "")

    res = subprocess.run(
        ["npx", "tsx", str(driver_file)],
        capture_output=True,
        text=True,
        env=cmd_env,
        timeout=10
    )
    return res


def test_opencode_preflight_context_file_is_non_ingestion_eligible():
    assert is_ingestion_eligible(".oem/.runtime/preflight_context.md") is False


def test_opencode_preflight_does_not_modify_memory_files(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    # Snapshot memory files before
    registry_path = project_dir / ".oem" / "concept_registry.json"
    events_path = project_dir / ".oem" / "events.jsonl"
    outcomes_path = project_dir / ".oem" / "state" / "outcomes.jsonl"
    
    reg_before = registry_path.read_text(encoding="utf-8") if registry_path.exists() else None
    ev_before = events_path.read_text(encoding="utf-8") if events_path.exists() else None
    out_before = outcomes_path.read_text(encoding="utf-8") if outcomes_path.exists() else None
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"}
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement new widget feature", env)
    assert res.returncode == 0
    
    reg_after = registry_path.read_text(encoding="utf-8") if registry_path.exists() else None
    ev_after = events_path.read_text(encoding="utf-8") if events_path.exists() else None
    out_after = outcomes_path.read_text(encoding="utf-8") if outcomes_path.exists() else None
    
    assert reg_before == reg_after
    assert ev_before == ev_after
    assert out_before == out_after


def test_opencode_preflight_uses_active_project_root(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    # We can override via OEM_PROJECT_ROOT env var
    other_project_dir = tmp_path / "other_project"
    other_project_dir.mkdir()
    other_engine = KnowledgeEngine(other_project_dir)
    other_engine.init_project(str(other_project_dir))
    
    env = {
        "OEM_PREFLIGHT_AUTOMATIC": "1",
        "OEM_PROJECT_ROOT": str(other_project_dir.resolve())
    }
    
    # Run the hook with project_dir but env override pointing to other_project_dir
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement widget in other proj", env)
    assert res.returncode == 0
    
    # Context file should appear in other_project_dir instead of project_dir
    assert (other_project_dir / ".oem" / ".runtime" / "preflight_context.md").exists()
    assert not (project_dir / ".oem" / ".runtime" / "preflight_context.md").exists()


def test_opencode_preflight_never_falls_back_to_dev_repo(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    env = {
        "OEM_PREFLIGHT_AUTOMATIC": "1",
        "OEM_PROJECT_ROOT": "" # Unresolved/empty
    }
    
    # Unsetting active project root should NOT result in dev repo fallback
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement something", env)
    # The hook should unresolved gracefully without dev repo contamination
    assert not (_REPO_ROOT / ".oem" / ".runtime" / "preflight_context.md").exists()


def test_opencode_preflight_skips_trivial_prompt(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"}
    
    # Write an initial context file first
    preflight_context = project_dir / ".oem" / ".runtime" / "preflight_context.md"
    preflight_context.parent.mkdir(parents=True, exist_ok=True)
    preflight_context.write_text("<!-- generated_by: openempiric -->\n<!-- source_type: oem_preflight_runtime -->\n# OEM Preflight Context\n\nDecision: required\n", encoding="utf-8")
    
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "ok", env)
    assert res.returncode == 0
    
    # It should have safely deleted the file because "ok" is trivial
    assert not preflight_context.exists()


def test_opencode_preflight_runs_for_task_prompt(temp_project, tmp_path):
    project_dir, engine = temp_project
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"}
    
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement calendar copy feature", env)
    assert res.returncode == 0
    
    preflight_context = project_dir / ".oem" / ".runtime" / "preflight_context.md"
    assert preflight_context.exists()
    content = preflight_context.read_text(encoding="utf-8")
    assert "Decision:" in content
    assert "Reason:" in content


def test_opencode_preflight_injects_or_writes_bounded_context_for_required_decision(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    # Create approved skill that triggers on "calendar copy"
    skills_dir = project_dir / ".oem" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skills_dir / "calendar_copy.md"
    skill_file.write_text("""---
type: oem_skill
id: calendar_copy
title: Calendar Copy
status: approved
triggers:
  - calendar copy
---
Ensure warm tone in calendar copy.
""", encoding="utf-8")
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"}
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement calendar copy feature", env)
    assert res.returncode == 0
    
    # 1. File Context check
    preflight_context = project_dir / ".oem" / ".runtime" / "preflight_context.md"
    assert preflight_context.exists()
    content = preflight_context.read_text(encoding="utf-8")
    assert "Decision: required" in content
    assert "Calendar Copy" in content
    
    # 2. Direct injection check (should be prepended/appended to prompt in console output SUCCESS:)
    stdout = res.stdout
    success_line = next((l for l in stdout.splitlines() if l.startswith("SUCCESS:")), "")
    assert success_line != ""
    payload = json.loads(success_line.split("SUCCESS:", 1)[1])
    
    assert "[OEM Preflight Context]" in payload["content"]
    assert "Decision: required" in payload["content"]


def test_opencode_preflight_handles_project_unresolved_without_wrong_memory(temp_project, tmp_path):
    # Running hook in a non-project directory with no .oem folder and no config
    non_project_dir = tmp_path / "non_project"
    non_project_dir.mkdir()
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1", "OEM_PROJECT_ROOT": ""}
    res = run_hook(tmp_path, "tui.prompt.append", non_project_dir, "implement calendar copy feature", env)
    assert res.returncode == 0
    
    assert not (non_project_dir / ".oem" / ".runtime" / "preflight_context.md").exists()


def test_opencode_preflight_preserves_existing_opencode_config(temp_project, tmp_path):
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    jsonc_file.write_text("""{
      // Custom user comment
      "custom_user_key": "user_value"
    }""", encoding="utf-8")
    
    env = {
        "OEM_PREFLIGHT_AUTOMATIC": "1",
        "XDG_CONFIG_HOME": str(tmp_path)
    }
    
    res = run_hook(tmp_path, "config", temp_project[0], env=env)
    assert res.returncode == 0
    
    config_text = jsonc_file.read_text(encoding="utf-8")
    assert "custom_user_key" in config_text
    assert "user_value" in config_text
    assert "preflight_context.md" not in config_text
    
    # Parse the config returned in stdout to verify it has preflight_context.md in instructions
    config_obj = json.loads(res.stdout.strip())
    instructions = config_obj.get("instructions", [])
    assert any("preflight_context.md" in inst for inst in instructions)


def test_opencode_preflight_does_not_write_invalid_plugins_key(temp_project, tmp_path):
    opencode_dir = tmp_path / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("{}", encoding="utf-8")
    
    env = {
        "OEM_PREFLIGHT_AUTOMATIC": "1",
        "XDG_CONFIG_HOME": str(tmp_path)
    }
    
    res = run_hook(tmp_path, "config", temp_project[0], env=env)
    assert res.returncode == 0
    
    config_text = jsonc_file.read_text(encoding="utf-8")
    config = json.loads(config_text)
    assert "plugins" not in config


def test_opencode_preflight_can_be_disabled(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    # 1. Configured disabled in config.json
    config_dir = project_dir / ".oem"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({
        "preflight": {
            "automatic": {
                "enabled": False
            }
        }
    }), encoding="utf-8")
    
    # Write preflight context first
    preflight_context = project_dir / ".oem" / ".runtime" / "preflight_context.md"
    preflight_context.parent.mkdir(parents=True, exist_ok=True)
    preflight_context.write_text("<!-- generated_by: openempiric -->\n<!-- source_type: oem_preflight_runtime -->\n# OEM Preflight Context\n\nDecision: required\n", encoding="utf-8")
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"} # env says enabled but config overrides to disabled
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement calendar copy feature", env)
    assert res.returncode == 0
    
    # It should have safely deleted the file because automatic preflight is disabled
    assert not preflight_context.exists()


def test_opencode_preflight_audit_can_be_disabled(temp_project, tmp_path):
    project_dir, engine = temp_project
    
    # Configured enabled but write_audit: false in config.json
    config_dir = project_dir / ".oem"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({
        "preflight": {
            "automatic": {
                "enabled": True,
                "write_audit": False
            }
        }
    }), encoding="utf-8")
    
    env = {"OEM_PREFLIGHT_AUTOMATIC": "1"}
    res = run_hook(tmp_path, "tui.prompt.append", project_dir, "implement calendar copy feature", env)
    assert res.returncode == 0
    
    # preflight_events.jsonl should not exist
    audit_file = project_dir / ".oem" / "preflight" / "preflight_events.jsonl"
    assert not audit_file.exists()
