from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from oem_knowledge.cli.parser import _setup_parser
from oem_knowledge.cli.commands.session import run_session_command
from oem_knowledge.cli.commands.system import run_system_command
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.manifest import load_manifest, get_manifest_path
from oem_knowledge.runtime import run_agent
from oem_knowledge.source_classifier import is_ingestion_eligible


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    yield project_dir, engine
    shutil.rmtree(project_dir, ignore_errors=True)


def test_manifest_creation_during_init(temp_project):
    project_dir, engine = temp_project
    engine.init_project(str(project_dir))
    
    manifest_path = get_manifest_path(project_dir)
    assert manifest_path.exists()
    
    manifest = load_manifest(project_dir)
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["project_id"] == "test_project"
    assert manifest["memory_root"] == ".oem"
    assert manifest["created_by"] == "openempiric"


def test_manifest_updates_during_setup_opencode(temp_project):
    project_dir, engine = temp_project
    engine.init_project(str(project_dir))
    
    # Mock systems/dirs to avoid actually changing ~/.config/opencode
    opencode_dir = Path.home() / ".config" / "opencode"
    
    with patch("oem_knowledge.cli.commands.system.Path.home") as mock_home, \
         patch("oem_knowledge.cli.commands.system.check_mcp_server", return_value=(True, True, 5, "")):
        
        mock_home.return_value = project_dir / "user_home"
        
        parser = _setup_parser()
        args = parser.parse_args(["setup", "opencode"])
        args.project = str(project_dir)
        
        run_system_command(args)
        
        # Verify manifest is updated
        manifest = load_manifest(project_dir)
        assert manifest is not None
        assert "opencode" in manifest["agent_integrations"]
        assert manifest["agent_integrations"]["opencode"]["enabled"] is True


def test_knowledge_read_execution(temp_project):
    project_dir, engine = temp_project
    engine.init_project(str(project_dir))
    
    res = engine.knowledge_read(project=str(project_dir))
    assert res["status"] == "success"
    assert res["operation"] == "knowledge_read"
    assert "summary" in res
    assert "sections" in res
    
    sections = res["sections"]
    assert "runtime_status" in sections
    assert "important_concepts" in sections
    assert "approved_skills" in sections


def test_run_agent_flags_handling(temp_project):
    project_dir, engine = temp_project
    # Do NOT initialize engine first to test uninitialized checks
    
    parser = _setup_parser()
    
    # Test --no-init fails
    args = parser.parse_args(["run", "opencode", "--no-init", "--project", str(project_dir)])
    with pytest.raises(SystemExit) as excinfo:
        run_agent("opencode", engine, str(project_dir), args)
    assert excinfo.value.code == 1
    
    # Test non-TTY fails without init-if-missing
    with patch("sys.stdin.isatty", return_value=False):
        with pytest.raises(SystemExit) as excinfo:
            run_agent("opencode", engine, str(project_dir), None)
        assert excinfo.value.code == 1
        
    # Test --init-if-missing initializes and proceeds
    # Mock subprocess.run/Popen to not actually start opencode, and mock setup
    with patch("subprocess.Popen") as mock_popen, \
         patch("subprocess.run") as mock_run, \
         patch("oem_knowledge.cli.commands.system.cmd_setup_opencode") as mock_setup:
        
        # Mock Popen to behave like a successful process
        mock_process = MagicMock()
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process
        
        args = parser.parse_args(["run", "opencode", "--init-if-missing", "--project", str(project_dir), "--skip-doctor", "--skip-session-start", "--skip-session-end"])
        
        # Run agent
        run_agent("opencode", engine, str(project_dir), args)
        
        # Should now be initialized
        assert engine.is_initialized(str(project_dir))


def test_ingestion_filter_exclusions():
    assert is_ingestion_eligible("manifest.json") is False
    assert is_ingestion_eligible(".oem/manifest.json") is False
    assert is_ingestion_eligible("init.sh") is False
    assert is_ingestion_eligible("oem.md") is False
    assert is_ingestion_eligible("memory-start.md") is False
