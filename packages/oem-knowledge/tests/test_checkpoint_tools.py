from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.working_set import update_working_set


@pytest.fixture
def engine(tmp_path):
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    return eng


def test_checkpoint_tools_registered():
    try:
        from fastmcp import FastMCP
    except ImportError:
        pytest.skip("FastMCP not installed")

    mcp = FastMCP("oem")
    from oem_knowledge.server import mount_tools
    mount_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    tool_names = [t.name for t in tools]

    for name in ("oem_checkpoint_list", "oem_checkpoint_create", "oem_checkpoint_restore"):
        assert name in tool_names, f"Tool '{name}' not registered"


def test_checkpoint_list_empty_no_oem(tmp_path):
    from oem_knowledge.tools.checkpoint import oem_checkpoint_list

    result = json.loads(oem_checkpoint_list(project=str(tmp_path)))
    assert result["status"] == "success"
    assert result["count"] == 0
    assert result["checkpoints"] == []


def test_checkpoint_create_and_list(engine, tmp_path):
    from oem_knowledge.tools.checkpoint import oem_checkpoint_create, oem_checkpoint_list

    ws_path = engine.layout().working_set_path
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text(json.dumps({
        "schema_version": 1,
        "workspace_root": str(tmp_path.resolve()),
        "goal": "test goal",
        "active_work_item": "task-1",
    }), encoding="utf-8")

    create_result = json.loads(oem_checkpoint_create(project=str(tmp_path)))
    assert create_result["status"] == "success"
    assert "checkpoint_path" in create_result
    assert create_result["checkpoint_path"].endswith(".json")

    list_result = json.loads(oem_checkpoint_list(project=str(tmp_path)))
    assert list_result["status"] == "success"
    assert list_result["count"] >= 1
    assert any(cp["goal"] == "test goal" for cp in list_result["checkpoints"])


def test_checkpoint_restore(engine, tmp_path):
    from oem_knowledge.tools.checkpoint import (
        oem_checkpoint_create,
        oem_checkpoint_restore,
        oem_checkpoint_list,
    )

    update_working_set(project=str(tmp_path), active_work_item="original-state")

    cp_result = json.loads(oem_checkpoint_create(project=str(tmp_path)))
    assert cp_result["status"] == "success"

    update_working_set(project=str(tmp_path), active_work_item="modified-state")

    checkpoints = json.loads(oem_checkpoint_list(project=str(tmp_path)))
    target_id = checkpoints["checkpoints"][0]["name_id"]

    restore_result = json.loads(oem_checkpoint_restore(target=target_id, project=str(tmp_path)))
    assert restore_result["status"] == "success"
    assert restore_result["target"] == target_id

    from oem_knowledge.runtime.working_set import load_working_set
    ws = load_working_set(project=str(tmp_path))
    assert ws.active_work_item == "original-state"


def test_checkpoint_restore_not_found(tmp_path):
    from oem_knowledge.tools.checkpoint import oem_checkpoint_restore

    result = json.loads(oem_checkpoint_restore(target="nonexistent", project=str(tmp_path)))
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_checkpoint_restore_no_target(tmp_path):
    from oem_knowledge.tools.checkpoint import oem_checkpoint_restore

    result = json.loads(oem_checkpoint_restore(target="", project=str(tmp_path)))
    assert result["status"] == "error"
    assert "required" in result["message"]


def test_checkpoint_create_default_project(engine, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from oem_knowledge.tools.checkpoint import oem_checkpoint_create

    ws = update_working_set(project=str(tmp_path), goal="default project")
    assert ws.goal == "default project"

    result = json.loads(oem_checkpoint_create(project=""))
    assert result["status"] == "success"


def test_checkpoint_tools_contract():
    try:
        from fastmcp import FastMCP
    except ImportError:
        pytest.skip("FastMCP not installed")

    mcp = FastMCP("oem")
    from oem_knowledge.server import mount_tools
    mount_tools(mcp)

    tool_names = [t.name for t in asyncio.run(mcp.list_tools())]

    for tool in ("oem_checkpoint_list", "oem_checkpoint_create", "oem_checkpoint_restore"):
        assert tool in tool_names, f"Required checkpoint tool '{tool}' missing from MCP surface"
