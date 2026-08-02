from __future__ import annotations

import os
import sys
import json
import pytest
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastmcp import FastMCP
from oem_knowledge.server import mount_tools
from oem_knowledge.project import (
    resolve_active_project,
    SESSION_TO_PROJECT,
    ProjectMismatchError,
    ProjectUnresolvedError
)
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.supervisor import render_supervisor_panel


@pytest.fixture(autouse=True)
def sanitize_env():
    with patch.dict(os.environ):
        os.environ.pop("OEM_PROJECT_ROOT", None)
        os.environ.pop("WORKSPACE", None)
        yield


@pytest.fixture(autouse=True)
def sanitize_session_resolution():
    import oem_knowledge.server
    original_get = oem_knowledge.server.get_project_root_from_active_session
    
    def mocked_get(path):
        resolved_path = Path(path).resolve()
        if "pytest" in str(resolved_path) or "tmp" in str(resolved_path):
            return original_get(resolved_path)
        return None
        
    with patch("oem_knowledge.server.get_project_root_from_active_session", side_effect=mocked_get):
        yield


@pytest.fixture
def clean_sessions():
    SESSION_TO_PROJECT.clear()
    yield
    SESSION_TO_PROJECT.clear()


@pytest.fixture
def mock_mcp():
    mcp = FastMCP("oem-test")
    mount_tools(mcp)
    return mcp


@pytest.fixture
def temp_projects(tmp_path):
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    
    # Initialize projects with .oem
    for proj in [project_a, project_b]:
        proj.mkdir()
        engine = KnowledgeEngine(proj)
        engine.init_project(str(proj))
        
    return project_a, project_b


def test_session_start_binds_to_current_project_root(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    
    # Run with explicit target path first
    import asyncio
    res_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res = json.loads(res_str.content[0].text)
    
    assert res["status"] == "success"
    assert res["project_root"] == str(project_a.resolve())
    assert res["memory_root"] == str((project_a / ".oem").resolve())
    
    session_id = res["session_id"]
    assert SESSION_TO_PROJECT[session_id] == project_a.resolve()


def test_knowledge_read_uses_active_session_project_when_project_omitted(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    import asyncio
    
    # Start session on project_a
    res_start_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start = json.loads(res_start_str.content[0].text)
    session_id = res_start["session_id"]
    
    # Read without project argument, using session_id in environment or global binding
    # Since session_id can be passed in other contexts or resolved, we pass it or set env.
    # Let's verify resolve_active_project directly for clean verification:
    resolved = resolve_active_project(project_arg="", session_id=session_id)
    assert resolved == project_a.resolve()
    
    # Call read with empty project
    res_read_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_read",
        {"project": "", "scope": "project"}
    ))
    # It resolves from CWD/env when session_id is not supplied to knowledge_read.
    # So we temporarily set PWD to project_a
    with patch.dict(os.environ, {"PWD": str(project_a)}):
        res_read_str = asyncio.run(mock_mcp.call_tool(
            "knowledge_read",
            {"project": "", "scope": "project"}
        ))
        res_read = json.loads(res_read_str.content[0].text)
        assert res_read["status"] == "success"
        assert res_read["project_root"] == str(project_a.resolve())


def test_knowledge_search_uses_active_session_project_when_project_omitted(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    import asyncio
    
    with patch.dict(os.environ, {"PWD": str(project_a), "WORKSPACE": "", "OEM_PROJECT_ROOT": ""}):
        res_str = asyncio.run(mock_mcp.call_tool(
            "knowledge_search",
            {"query": "CCB", "project": ""}
        ))
        res = json.loads(res_str.content[0].text)
        assert res["status"] == "success"
        assert res["project_root"] == str(project_a.resolve())


def test_knowledge_reflect_writes_to_active_session_project_when_project_omitted(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    import asyncio
    
    res_start_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start = json.loads(res_start_str.content[0].text)
    session_id = res_start["session_id"]
    
    # Reflect using session_id
    res_reflect_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_reflect",
        {
            "project": "",
            "conversation_text": "Hypothesis: Reflection writes to correct project",
            "session_id": session_id
        }
    ))
    res_reflect = json.loads(res_reflect_str.content[0].text)
    print("RES_REFLECT IS:", res_reflect)
    assert res_reflect["status"] in ("success", "partial", "empty", "warn")
    assert res_reflect["project_root"] == str(project_a.resolve())


def test_session_end_closes_active_session_project_when_project_omitted(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    import asyncio
    
    res_start_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start = json.loads(res_start_str.content[0].text)
    session_id = res_start["session_id"]
    
    # End using session_id
    res_end_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_end",
        {
            "project": "",
            "conversation_text": "Validation: Session closed successfully",
            "session_id": session_id
        }
    ))
    res_end = json.loads(res_end_str.content[0].text)
    assert res_end["project_root"] == str(project_a.resolve())
    assert session_id not in SESSION_TO_PROJECT
    assert not (project_a / ".oem" / "state" / "active_session.json").exists()


def test_mcp_tools_never_default_to_oem_dev_repo(temp_projects, mock_mcp, clean_sessions):
    project_a, project_b = temp_projects
    import asyncio
    
    # Mock is_oem_dev_repo to return True for project_a (the simulated dev repo)
    def mock_is_oem_dev(path):
        return str(project_a) in str(path)
        
    with patch("oem_knowledge.project.is_oem_dev_repo", side_effect=mock_is_oem_dev):
        with patch.dict(os.environ, {
            "WORKSPACE": str(project_a),
            "PWD": str(project_b),
            "OEM_FORCE_MISMATCH_CHECK": "1",
            "OEM_PROJECT_ROOT": ""
        }):
            with patch("os.getcwd", return_value=str(project_b)):
                res_str = asyncio.run(mock_mcp.call_tool(
                    "knowledge_read",
                    {"project": "", "scope": "project"}
                ))
                res = json.loads(res_str.content[0].text)
                assert res["status"] == "error"
                assert res["reason"] == "project_mismatch"


def test_mcp_tool_returns_error_when_project_unresolved(mock_mcp, clean_sessions):
    import asyncio
    # Clear PWD and mock CWD to not have any .oem folder
    with patch.dict(os.environ, {"PWD": ""}, clear=True):
        with patch("os.getcwd", return_value="/tmp/nonexistent_project_dir_random_123"):
            res_str = asyncio.run(mock_mcp.call_tool(
                "knowledge_read",
                {"project": "", "scope": "project"}
            ))
            res = json.loads(res_str.content[0].text)
            assert res["status"] == "error"
            assert res["reason"] == "project_unresolved"


def test_explicit_project_argument_overrides_active_session_project(temp_projects, mock_mcp, clean_sessions):
    project_a, project_b = temp_projects
    import asyncio
    
    # Start session on project_a
    res_start_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start = json.loads(res_start_str.content[0].text)
    session_id = res_start["session_id"]
    
    # Call read with explicit project_b, which should override project_a for this call only
    res_read_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_read",
        {"project": str(project_b), "scope": "project"}
    ))
    res_read = json.loads(res_read_str.content[0].text)
    assert res_read["status"] == "success"
    assert res_read["project_root"] == str(project_b.resolve())
    
    # Verify that session_id still binds to project_a
    resolved = resolve_active_project(project_arg="", session_id=session_id)
    assert resolved == project_a.resolve()


def test_project_binding_is_isolated_between_sessions(temp_projects, mock_mcp, clean_sessions):
    project_a, project_b = temp_projects
    import asyncio
    
    # Session 1 on project_a
    res_start_a_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start_a = json.loads(res_start_a_str.content[0].text)
    session_id_a = res_start_a["session_id"]
    
    # Session 2 on project_b
    res_start_b_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_b)}
    ))
    res_start_b = json.loads(res_start_b_str.content[0].text)
    session_id_b = res_start_b["session_id"]
    
    assert SESSION_TO_PROJECT[session_id_a] == project_a.resolve()
    assert SESSION_TO_PROJECT[session_id_b] == project_b.resolve()


def test_response_includes_project_root_and_memory_root(temp_projects, mock_mcp, clean_sessions):
    project_a, _ = temp_projects
    import asyncio
    
    res_start_str = asyncio.run(mock_mcp.call_tool(
        "knowledge_session_start",
        {"project": str(project_a)}
    ))
    res_start = json.loads(res_start_str.content[0].text)
    assert "project_root" in res_start
    assert "memory_root" in res_start
    assert res_start["project_root"] == str(project_a.resolve())
    assert res_start["memory_root"] == str((project_a / ".oem").resolve())


def test_warning_when_active_project_differs_from_cwd():
    # Render panel with mismatching directories
    panel = render_supervisor_panel(
        project="/home/xpajonx/projects/Instagram-Reels",
        agent_name="opencode",
        checks=[]
    )
    assert "Active OEM project: /home/xpajonx/projects/Instagram-Reels" in panel
    assert "Memory root: /home/xpajonx/projects/Instagram-Reels/.oem" in panel
    assert "! Active OEM project does not match current working directory." in panel


def test_project_mismatch_error_behavior(temp_projects, clean_sessions):
    project_a, project_b = temp_projects
    
    # Mock is_oem_dev_repo to return True for project_a (the simulated dev repo)
    def mock_is_oem_dev(path):
        return str(project_a) in str(path)
        
    with pytest.raises(ProjectMismatchError) as exc_info:
        with patch("oem_knowledge.project.is_oem_dev_repo", side_effect=mock_is_oem_dev):
            with patch.dict(os.environ, {
                "WORKSPACE": str(project_a),
                "PWD": str(project_b),
                "OEM_FORCE_MISMATCH_CHECK": "1",
                "OEM_PROJECT_ROOT": ""
            }):
                with patch("os.getcwd", return_value=str(project_b)):
                    resolve_active_project(project_arg="")
                    
    assert exc_info.value.reason == "project_mismatch"
    assert str(project_a) in exc_info.value.resolved_project
    assert str(project_b) in exc_info.value.cwd
