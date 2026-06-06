from __future__ import annotations

import sys
import json
from unittest.mock import patch
import pytest
from pathlib import Path
import tempfile
import shutil

from oem_knowledge.cli import main


@pytest.fixture
def temp_home():
    d = tempfile.mkdtemp()
    home_path = Path(d)
    yield home_path
    shutil.rmtree(d)


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(autouse=True)
def mock_setup_mcp_check():
    with patch("oem_knowledge.cli.check_mcp_server", return_value=(True, True, 19, "")):
        yield


def test_setup_opencode_basic(temp_home, tmp_proj):
    """Verify that oem setup opencode initializes all folders and configuration correctly."""
    # Write a dummy opencode.jsonc beforehand
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("{\n  \"instructions\": []\n}", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()

    # Check plugin and instructions copied/created
    plugin_file = opencode_dir / "plugins" / "openempiric.ts"
    inst_file = opencode_dir / "instructions" / "memory-start.md"

    assert plugin_file.exists()
    assert inst_file.exists()
    
    inst_content = inst_file.read_text(encoding="utf-8")
    assert "OpenEmpiric is already active for this session" in inst_content
    assert "knowledge_session_start" not in inst_content

    # Check opencode.jsonc registers instructions path and MCP server
    assert jsonc_file.exists()
    config_data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert str(inst_file.resolve()) in config_data["instructions"]
    assert "openempiric" in config_data.get("mcp", {})
    assert config_data["mcp"]["openempiric"]["command"] in ("oem", "uv")


def test_setup_opencode_idempotency(temp_home, tmp_proj):
    """Verify that running setup multiple times is idempotent and does not break anything."""
    opencode_dir = temp_home / ".config" / "opencode"

    with patch("pathlib.Path.home", return_value=temp_home):
        # First setup run
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()

        plugin_content_1 = (opencode_dir / "plugins" / "openempiric.ts").read_text(encoding="utf-8")
        inst_content_1 = (opencode_dir / "instructions" / "memory-start.md").read_text(encoding="utf-8")
        config_content_1 = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")

        # Second setup run
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()

        plugin_content_2 = (opencode_dir / "plugins" / "openempiric.ts").read_text(encoding="utf-8")
        inst_content_2 = (opencode_dir / "instructions" / "memory-start.md").read_text(encoding="utf-8")
        config_content_2 = (opencode_dir / "opencode.jsonc").read_text(encoding="utf-8")

        assert plugin_content_1 == plugin_content_2
        assert inst_content_1 == inst_content_2
        assert config_content_1 == config_content_2


def test_setup_opencode_repair_forced(temp_home, tmp_proj):
    """Verify that --repair forces overwriting of existing config/plugin files."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    plugin_file = opencode_dir / "plugins" / "openempiric.ts"
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("old plugin", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode", "--repair"]):
            main()

    # Old plugin should be overwritten
    assert plugin_file.read_text(encoding="utf-8") != "old plugin"


def test_setup_opencode_legacy_migration(temp_home, tmp_proj):
    """Verify that legacy configurations (stale workflows) are auto-migrated/overwritten without --repair."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    plugin_file = opencode_dir / "plugins" / "openempiric.ts"
    plugin_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_file.write_text("knowledge_session_start references", encoding="utf-8")

    inst_file = opencode_dir / "instructions" / "memory-start.md"
    inst_file.parent.mkdir(parents=True, exist_ok=True)
    inst_file.write_text("verify plugin array references", encoding="utf-8")

    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()

    # Legacy content should be overwritten even without --repair
    assert "knowledge_session_start" not in plugin_file.read_text(encoding="utf-8")
    assert "verify plugin array" not in inst_file.read_text(encoding="utf-8")


def test_doctor_integration_diagnostics(temp_home, tmp_proj):
    """Verify that oem doctor outputs correct status for OpenCode workstation configuration."""
    # Write a dummy pyproject.toml and skills file under .oem so doctor check passes
    proj_path = Path(tmp_proj)
    (proj_path / "pyproject.toml").write_text("[project]\nname = \"user-app\"\n", encoding="utf-8")
    skills_dir = proj_path / ".oem" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "openempiric.yaml").write_text("name: openempiric\n", encoding="utf-8")

    # Mock success for skills and other requirements that might fail due to fresh temp project
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch("oem_knowledge.cli.shutil.which", return_value="/mock/bin"):
            with patch("oem_knowledge.services.event_migration.EventMigrator.get_schema_status", return_value={"status": "up_to_date", "message": "OK"}):
                with patch("oem_knowledge.cli.check_mcp_server", return_value=(True, True, 19, "")):
                    with patch("oem_knowledge.adapters.get_adapter") as mock_adapter:
                        mock_adapter.return_value.verify_mcp.return_value = True
                        
                        # 1. Initially integration files are missing, doctor should still exit 0/None because workstation checks are warnings
                        with patch.object(sys, "argv", ["oem", "doctor", "--project", tmp_proj]):
                            try:
                                main()
                            except SystemExit as exc:
                                assert exc.code == 0 or exc.code is None

                        # 2. Perform setup
                        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
                            main()

                        # 3. Running doctor after setup should also succeed
                        with patch.object(sys, "argv", ["oem", "doctor", "--project", tmp_proj]):
                            try:
                                main()
                            except SystemExit as exc:
                                assert exc.code == 0 or exc.code is None


def test_setup_opencode_config_merge(temp_home, tmp_proj):
    """Verify that setup opencode merges configuration cleanly and does not delete unrelated keys."""
    import re
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  // Existing server configuration\n"
        "  \"mcp\": {\n"
        "    \"github\": {\"key\": \"val\"}\n"
        "  },\n"
        "  \"model\": \"gpt-4\"\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    # Verify file text has instructions key but retains mcp and model and comments
    text = jsonc_file.read_text(encoding="utf-8")
    assert '"mcp"' in text
    assert '"github"' in text
    assert '"model"' in text
    assert 'Existing server configuration' in text
    assert '"instructions"' in text
    
    # Also verify loaded json parses correctly and matches merged state
    cleaned = re.sub(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/', lambda m: m.group(1) if m.group(1) else "", text)
    data = json.loads(cleaned)
    assert data["mcp"]["github"]["key"] == "val"
    assert "openempiric" in data.get("mcp", {})
    assert data["mcp"]["openempiric"]["command"] in ("oem", "uv")
    assert data["model"] == "gpt-4"
    assert len(data["instructions"]) == 1


def test_setup_opencode_backup_creation(temp_home, tmp_proj):
    """Verify that a timestamped backup of the config file is created before modification."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("{\n  \"instructions\": []\n}", encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    # Verify backup exists
    backups = list(opencode_dir.glob("opencode.jsonc.backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{\n  \"instructions\": []\n}"


def test_setup_opencode_invalid_json_abort(temp_home, tmp_proj):
    """Verify that setup aborts with exit code 1 and does not wipe the config if it contains invalid JSON."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    invalid_content = "{\n  \"mcp\": {\n    \"github\": \n  // missing brace/commas\n}"
    jsonc_file.write_text(invalid_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 1
            
    # File content should remain unchanged, NOT wiped or set to default {}
    assert jsonc_file.read_text(encoding="utf-8") == invalid_content


def test_setup_opencode_comment_preservation(temp_home, tmp_proj):
    """Verify that comments and array elements are preserved correctly."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  // Active array of instructions\n"
        "  \"instructions\": [\n"
        "    // prior comment\n"
        "    \"existing.md\"\n"
        "  ]\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    text = jsonc_file.read_text(encoding="utf-8")
    assert "Active array of instructions" in text
    assert "prior comment" in text
    assert "existing.md" in text
    assert "memory-start.md" in text


def test_setup_opencode_url_slashes_preservation(temp_home, tmp_proj):
    """Verify that schema URL slashes (https://) are not stripped by comment cleaners."""
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    initial_content = (
        "{\n"
        "  \"$schema\": \"https://opencode.ai/config.json\"\n"
        "}"
    )
    jsonc_file.write_text(initial_content, encoding="utf-8")
    
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
            main()
            
    text = jsonc_file.read_text(encoding="utf-8")
    assert "https://opencode.ai/config.json" in text


def test_setup_opencode_tool_enumeration():
    import asyncio
    from fastmcp import FastMCP
    from oem_knowledge.server import mount_tools
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    
    tools = asyncio.run(mcp.list_tools())
    tool_names = [t.name for t in tools]
    assert "knowledge_search" in tool_names
    assert "knowledge_explain_concept" in tool_names
    assert "knowledge_graph_query" in tool_names
    assert "knowledge_health_check" in tool_names
    assert "knowledge_stats" in tool_names
    assert "oem_todo_read" in tool_names
    assert "knowledge_usage_report" in tool_names



