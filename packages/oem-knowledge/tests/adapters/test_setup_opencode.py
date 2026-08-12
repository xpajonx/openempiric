from __future__ import annotations

import sys
import json
import os
from unittest.mock import patch, MagicMock
import pytest
from pathlib import Path
import tempfile
import shutil

from oem_knowledge.cli import main


@pytest.fixture
def temp_home(monkeypatch):
    d = tempfile.mkdtemp()
    home_path = Path(d)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home_path / ".config"))
    yield home_path
    shutil.rmtree(d)


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(autouse=True)
def mock_setup_mcp_check():
    with patch("oem_knowledge.cli.commands.system.check_mcp_server", return_value=(True, True, 19, "")):
        yield


def test_setup_opencode_preserves_existing_mcp_servers(temp_home, tmp_proj):
    """Verify that oem setup opencode preserves unrelated user MCP configurations."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"mcp\": {\n"
        "    \"github\": {\"type\": \"local\", \"command\": \"github-mcp\"},\n"
        "    \"context7\": {\"type\": \"local\", \"command\": \"context7-mcp\"},\n"
        "    \"filesystem\": {\"type\": \"local\", \"command\": \"filesystem-mcp\"}\n"
        "  }\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert "github" in data["mcp"]
    assert "context7" in data["mcp"]
    assert "filesystem" in data["mcp"]
    assert "openempiric" in data["mcp"]


def test_setup_opencode_merges_oem_mcp_without_replacing_mcp_object(temp_home, tmp_proj):
    """Verify that merging openempiric mcp preserves structure and comments in JSONC."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  // Existing server configuration\n"
        "  \"mcp\": {\n"
        "    \"github\": {\"key\": \"val\"}\n"
        "  }\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    text = jsonc_file.read_text(encoding="utf-8")
    assert "Existing server configuration" in text
    assert '"github"' in text
    assert '"openempiric"' in text


def test_setup_opencode_preserves_existing_instructions(temp_home, tmp_proj):
    """Verify that existing user instructions paths are preserved in instructions array."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"instructions\": [\n"
        "    \"~/.config/opencode/instructions/user.md\"\n"
        "  ]\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert "instructions" in data
    assert "~/.config/opencode/instructions/user.md" in data["instructions"]
    assert any("memory-start.md" in p for p in data["instructions"])


def test_setup_opencode_appends_oem_instruction_once(temp_home, tmp_proj):
    """Verify that oem instruction is appended once and running setup again is idempotent."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = "{\n  \"instructions\": []\n}"
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert len(data["instructions"]) == 1


def test_setup_opencode_preserves_unknown_top_level_keys(temp_home, tmp_proj):
    """Verify that unknown/unrelated top-level keys like 'model' are kept."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = "{\n  \"model\": \"gpt-4-custom\",\n  \"theme\": \"dark\"\n}"
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert data["model"] == "gpt-4-custom"
    assert data["theme"] == "dark"


def test_setup_opencode_backup_before_every_config_write(temp_home, tmp_proj):
    """Verify that a timestamped backup with suffix '.bak-' is created before write."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("{\n  \"instructions\": []\n}", encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    backups = list(opencode_dir.glob("opencode.jsonc.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{\n  \"instructions\": []\n}"


def test_setup_opencode_restores_backup_if_validation_fails(temp_home, tmp_proj):
    """Verify that configuration is restored from backup if opencode validation fails."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("{\n  \"instructions\": []\n}", encoding="utf-8")
    
    # Mock validation run to fail
    class DummyFailedProcess:
        returncode = 1
        stdout = "Invalid config error"
        stderr = "Invalid config error"
        
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch("subprocess.run", return_value=DummyFailedProcess()):
            # Also need to make sure we are not bypassed under pytest
            with patch("sys.modules", {"pytest": None}):
                with patch.dict("os.environ", {"PYTEST_CURRENT_TEST": ""}):
                    with pytest.raises(SystemExit) as exc:
                        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
                            main()
                    assert exc.value.code == 1
                    
    # Should revert changes to the original file
    assert jsonc_file.read_text(encoding="utf-8") == "{\n  \"instructions\": []\n}"


def test_setup_opencode_removes_only_oem_invalid_plugins_entry(temp_home, tmp_proj):
    """Verify that setup removes oem plugin entry from top-level plugins array, preserving user plugins."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"plugins\": [\n"
        "    \"user-plugin.ts\",\n"
        "    \"/home/xpajonx/.config/opencode/plugins/openempiric.ts\"\n"
        "  ]\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert "plugins" in data
    assert "user-plugin.ts" in data["plugins"]
    assert any("openempiric.ts" in p for p in data["plugins"]) is False


def test_setup_opencode_does_not_delete_user_plugin_entries(temp_home, tmp_proj):
    """Verify that running setup does not delete the whole plugins array if user plugins exist."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = "{\n  \"plugins\": [\"user-plugin.ts\"]\n}"
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert "plugins" in data
    assert data["plugins"] == ["user-plugin.ts"]


def test_setup_opencode_idempotent_with_existing_user_mcp(temp_home, tmp_proj):
    """Verify setup is idempotent and leaves user MCP configurations intact on repeated runs."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"mcp\": {\n"
        "    \"github\": {\"command\": \"github-mcp\"}\n"
        "  }\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
        content1 = jsonc_file.read_text(encoding="utf-8")
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
        content2 = jsonc_file.read_text(encoding="utf-8")
        
    assert content1 == content2


def test_repair_does_not_wipe_mcp_config(temp_home, tmp_proj):
    """Verify running setup with --repair does not delete existing user MCP configurations."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"mcp\": {\n"
        "    \"github\": {\"command\": \"github-mcp\"}\n"
        "  }\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode", "--repair"]):
            main()
            
    data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert "github" in data["mcp"]
    assert "openempiric" in data["mcp"]


def test_run_opencode_aborts_if_config_invalid_without_mutating_config(temp_home, tmp_proj):
    """Verify that launching oem run opencode fails fast on invalid config and doesn't mutate config."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    # invalid config format
    invalid_content = "{\n  \"mcp\": {\n    \"github\": \n  // missing brace\n}"
    jsonc_file.write_text(invalid_content, encoding="utf-8")
    
    # Mock config validity status to fail
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch("oem_knowledge.runtime.readiness.check_opencode_config_valid", return_value=("failure", "Invalid brace structure")):
            with pytest.raises(SystemExit) as exc:
                with patch.object(sys, "argv", ["oem", "run", "opencode", "--skip-doctor"]):
                    main()
            assert exc.value.code == 1
            
    # Config file must not be modified or setup by run
    assert jsonc_file.read_text(encoding="utf-8") == invalid_content


def test_setup_installs_remember_skill_and_dream_agent(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
    skill = opencode_dir / "skills" / "remember" / "SKILL.md"
    agent = opencode_dir / "agent" / "dream.md"
    assert skill.exists()
    assert agent.exists()
    assert "source_type: oem_opencode_skill" in skill.read_text(encoding="utf-8")
    assert "source_type: oem_opencode_agent" in agent.read_text(encoding="utf-8")
    data = json.loads((opencode_dir / "opencode.jsonc").read_text(encoding="utf-8"))
    assert "agent" not in data
    assert "plugins" not in data


def test_setup_preserves_user_modified_skill(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    skill = opencode_dir / "skills" / "remember" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("my custom skill content", encoding="utf-8")
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
    assert skill.read_text(encoding="utf-8") == "my custom skill content"


def test_setup_repair_upgrades_verified_managed_skill(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    skill = opencode_dir / "skills" / "remember" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    old_content = "<!-- generated_by: openempiric --> old version"
    skill.write_text(old_content, encoding="utf-8")
    import hashlib
    manifest_path = opencode_dir / "openempiric-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "assets": {"skills/remember/SKILL.md": {"sha256": hashlib.sha256(old_content.encode("utf-8")).hexdigest()}},
    }), encoding="utf-8")
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode", "--repair"]):
            main()
    assert "source_type: oem_opencode_skill" in skill.read_text(encoding="utf-8")


def test_setup_force_assets_replaces_user_file_with_backup(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    skill = opencode_dir / "skills" / "remember" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("user content", encoding="utf-8")
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode", "--force-assets"]):
            main()
    assert "source_type: oem_opencode_skill" in skill.read_text(encoding="utf-8")
    assert (opencode_dir / "skills" / "remember" / "SKILL.md.oem.bak").exists()


def test_setup_writes_asset_manifest(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
    manifest = opencode_dir / "openempiric-manifest.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert "skills/remember/SKILL.md" in data["assets"]
    assert "agent/dream.md" in data["assets"]
    assert data["assets"]["agent/dream.md"]["sha256"]


def test_setup_repair_preserves_spoofed_marker_file(temp_home, tmp_proj):
    opencode_dir = temp_home / ".config" / "opencode"
    skill = opencode_dir / "skills" / "remember" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("<!-- generated_by: openempiric --> user content with marker", encoding="utf-8")
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode", "--repair"]):
            main()
    assert "user content with marker" in skill.read_text(encoding="utf-8")
