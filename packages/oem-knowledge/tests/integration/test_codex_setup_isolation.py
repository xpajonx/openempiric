from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from oem_knowledge.adapters.codex_app.adapter import CodexAppAdapter
from oem_knowledge.cli import main
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def temp_env(monkeypatch):
    """Create a temporary environment directory mimicking the user's home."""
    d = tempfile.mkdtemp()
    home = Path(d)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    yield home
    shutil.rmtree(d)


@pytest.fixture(autouse=True)
def mock_mcp_check():
    """Mock checking the MCP server since we are running in tests."""
    with patch("oem_knowledge.cli.check_mcp_server", return_value=(True, True, 20, "")):
        with patch("oem_knowledge.cli.commands.system.check_mcp_server", return_value=(True, True, 20, "")):
            yield


def test_setup_codex_app_isolation(temp_env):
    """Verify oem setup codex-app does not touch opencode directories/files."""
    opencode_dir = temp_env / ".config" / "opencode"
    codex_home = temp_env / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    
    # Configure environment
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": "/home/xpajonx/project",
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env):
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
    # Codex app home should be written to
    assert (codex_home / "config.toml").exists()
    assert (codex_home / "skills" / "openempiric" / "SKILL.md").exists()
    
    # OpenCode should NOT be created or written to
    assert not opencode_dir.exists()


def test_setup_opencode_isolation(temp_env):
    """Verify oem setup opencode does not touch codex directories/files."""
    opencode_dir = temp_env / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    (opencode_dir / "opencode.jsonc").write_text("{}", encoding="utf-8")
    
    codex_home = temp_env / "codex-home"
    
    # Configure environment
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": "/home/xpajonx/project",
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env):
            with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
                main()
                
    # OpenCode files should be written to
    assert (opencode_dir / "opencode.jsonc").exists()
    assert (opencode_dir / "plugins" / "openempiric.ts").exists()
    
    # Codex app home should NOT be created or written to
    assert not codex_home.exists()


def test_codex_preserves_existing_config_and_backups_on_change(temp_env):
    """Verify existing config keys and other MCP configurations are preserved and backed up on modification."""
    codex_home = temp_env / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    config_file = codex_home / "config.toml"
    
    initial_toml = (
        '# Unrelated comment\n'
        'app_theme = "dark"\n'
        '\n'
        '[mcp_servers.other_mcp]\n'
        'command = "node"\n'
        'args = ["some-mcp.js"]\n'
    )
    config_file.write_text(initial_toml, encoding="utf-8")
    
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": "/home/xpajonx/project",
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env):
            # First run: writes config and should create a backup because it modifies the file
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
    # Check that backup was created
    backups = list(codex_home.glob("config.toml.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == initial_toml
    
    # Check that final config preserves other keys and has openempiric
    final_text = config_file.read_text(encoding="utf-8")
    assert "app_theme = \"dark\"" in final_text
    assert "[mcp_servers.other_mcp]" in final_text
    assert "[mcp_servers.openempiric]" in final_text
    
    final_data = tomllib.loads(final_text)
    assert final_data["app_theme"] == "dark"
    assert final_data["mcp_servers"]["other_mcp"]["command"] == "node"
    assert "openempiric" in final_data["mcp_servers"]


def test_codex_setup_idempotency(temp_env):
    """Verify multiple setup runs do not modify the file or write new backups if the config is up-to-date."""
    codex_home = temp_env / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": "/home/xpajonx/project",
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env):
            # First setup run
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
            config_content_1 = (codex_home / "config.toml").read_text(encoding="utf-8")
            backups_1 = len(list(codex_home.glob("config.toml.backup-*")))
            
            # Second setup run (should make no changes)
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
            config_content_2 = (codex_home / "config.toml").read_text(encoding="utf-8")
            backups_2 = len(list(codex_home.glob("config.toml.backup-*")))
            
            assert config_content_1 == config_content_2
            # No new backup should have been created on the second run (since it was up to date)
            assert backups_1 == backups_2


def test_safe_wsl_home_detection_failure(temp_env):
    """Verify RuntimeError is raised in WSL when Windows home cannot be automatically resolved."""
    eng = KnowledgeEngine(str(temp_env))
    adapter = CodexAppAdapter(eng, str(temp_env))
    
    # Mock behavior to simulate WSL but with failing detection
    with patch("oem_knowledge.adapters.codex_app.adapter.is_wsl", return_value=True):
        with patch.object(adapter, "_detect_windows_codex_home_from_wsl", return_value=None):
            with patch.dict(os.environ, {}, clear=True):
                with patch("sys.platform", "linux"):
                    with pytest.raises(RuntimeError) as exc_info:
                        adapter.get_codex_home()
                    
                    assert "Could not automatically detect your Windows Codex home directory from WSL" in str(exc_info.value)


def test_multi_adapter_skill_merging(temp_env):
    """Verify that setups for both opencode and codex-app merge non-destructively in openempiric.yaml."""
    import yaml
    # Initialize engine/project
    eng = KnowledgeEngine(temp_env)
    eng.init_project(str(temp_env))
    
    # Skills yaml path
    skills_file = temp_env / ".oem" / "skills" / "openempiric.yaml"
    
    # 1. Run setup for opencode
    with patch("pathlib.Path.home", return_value=temp_env), patch("os.getcwd", return_value=str(temp_env)):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    with open(skills_file, "r") as f:
        data = yaml.safe_load(f)
    assert "opencode" in data["adapters"]
    assert "codex-app" not in data["adapters"]
    
    # 2. Run setup for codex-app
    codex_home = temp_env / "codex-home"
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": str(temp_env),
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env), patch("os.getcwd", return_value=str(temp_env)):
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
    with open(skills_file, "r") as f:
        data = yaml.safe_load(f)
    assert "opencode" in data["adapters"]
    assert "codex-app" in data["adapters"]


def test_legacy_skill_migration_to_adapters(temp_env):
    """Verify that a legacy adapter: opencode is converted to adapters: [opencode] during setup."""
    import yaml
    eng = KnowledgeEngine(temp_env)
    eng.init_project(str(temp_env))
    
    skills_file = temp_env / ".oem" / "skills" / "openempiric.yaml"
    skills_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Write legacy format
    legacy_data = {
        "name": "openempiric",
        "version": "1.0.0",
        "schema_version": 1,
        "adapter": "opencode"
    }
    with open(skills_file, "w") as f:
        yaml.safe_dump(legacy_data, f)
        
    # Run setup to trigger migration
    codex_home = temp_env / "codex-home"
    env_vars = {
        "OEM_CODEX_HOME": str(codex_home),
        "OEM_CODEX_WSL_PROJECT_DIR": str(temp_env),
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    with patch.dict(os.environ, env_vars):
        with patch("pathlib.Path.home", return_value=temp_env), patch("os.getcwd", return_value=str(temp_env)):
            with patch.object(sys, "argv", ["oem", "setup", "codex-app"]):
                main()
                
    with open(skills_file, "r") as f:
        data = yaml.safe_load(f)
    assert "adapter" not in data
    assert "opencode" in data["adapters"]
    assert "codex-app" in data["adapters"]

