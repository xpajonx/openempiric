from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

def test_mcp_knowledge_session_start_registered(tmp_path):
    """Verify that knowledge_session_start is registered on MCP and returns session lifecycle status."""
    try:
        from fastmcp import FastMCP
    except ImportError:
        pytest.skip("FastMCP not installed")

    mcp = FastMCP("oem")
    from oem_knowledge.server import mount_tools
    mount_tools(mcp)

    tools = asyncio.run(mcp.list_tools())
    start_tool = next((t for t in tools if t.name == "knowledge_session_start"), None)
    assert start_tool is not None, "knowledge_session_start MCP tool not registered"

    # Test execution of knowledge_session_start via engine
    from oem_knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine(str(tmp_path))
    engine.init_project(str(tmp_path))

    res = engine.session_start(str(tmp_path))
    assert res["status"] == "success"
    assert res["operation"] == "knowledge_session_start"
    assert "session_id" in res
    assert res["project"] == str(tmp_path.resolve())
    assert "OEM session started" in res["message"]
    assert "warnings" in res
    assert "suggestion" in res
    # Should NOT have baseline/sections keys
    assert "sections" not in res
    assert "important_concepts" not in res

def test_mcp_lifecycle_tools_contract():
    """Verify that the MCP lifecycle surface exactly includes the required lifecycle tools."""
    try:
        from fastmcp import FastMCP
    except ImportError:
        pytest.skip("FastMCP not installed")

    mcp = FastMCP("oem")
    from oem_knowledge.server import mount_tools
    mount_tools(mcp)

    tool_names = [t.name for t in asyncio.run(mcp.list_tools())]
    
    required_tools = [
        "knowledge_session_start",
        "knowledge_read",
        "knowledge_search",
        "knowledge_reflect",
        "knowledge_session_end"
    ]
    
    for tool in required_tools:
        assert tool in tool_names, f"Required tool '{tool}' is missing from the MCP surface"
