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


def test_setup_opencode_basic(temp_home, tmp_proj):
    """Verify that oem setup opencode initializes all folders and configuration correctly."""
    # Write a dummy opencode.jsonc beforehand
    opencode_dir = temp_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)
    jsonc_file = opencode_dir / "opencode.jsonc"
    jsonc_file.write_text("// dummy comment\n{\n  \"instructions\": []\n}", encoding="utf-8")

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

    # Check opencode.jsonc registers instructions path
    assert jsonc_file.exists()
    config_data = json.loads(jsonc_file.read_text(encoding="utf-8"))
    assert str(inst_file.resolve()) in config_data["instructions"]


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
    # Mock success for skills and other requirements that might fail due to fresh temp project
    with patch("pathlib.Path.home", return_value=temp_home):
        with patch("oem_knowledge.cli.shutil.which", return_value="/mock/bin"):
            with patch("oem_knowledge.engine.EventMigrator.get_schema_status", return_value={"status": "up_to_date", "message": "OK"}):
                with patch("oem_knowledge.cli.Path.exists", return_value=True):
                    with patch("oem_knowledge.adapters.get_adapter") as mock_adapter:
                        mock_adapter.return_value.verify_mcp.return_value = True
                        
                        # 1. Initially integration files are missing, doctor should still exit 0/None because workstation checks are warnings
                        with patch.object(sys, "argv", ["oem", "doctor", "--project", tmp_proj]):
                            try:
                                main()
                            except SystemExit as exc:
                                assert exc.value.code == 0 or exc.value.code is None

                        # 2. Perform setup
                        with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
                            main()

                        # 3. Running doctor after setup should also succeed
                        with patch.object(sys, "argv", ["oem", "doctor", "--project", tmp_proj]):
                            try:
                                main()
                            except SystemExit as exc:
                                assert exc.value.code == 0 or exc.value.code is None
