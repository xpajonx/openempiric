import pytest
from fastmcp import FastMCP
from oem_knowledge.server import mount_tools

def test_mcp_tool_names_snapshot():
    # Instantiate FastMCP and mount the tools
    mcp = FastMCP("openempiric")
    mount_tools(mcp)

    import asyncio
    tools = asyncio.run(mcp.list_tools())
    registered_tools = {tool.name for tool in tools}

    # Exact expected set of tool names
    expected_tools = {
        # Todos (from oem_knowledge.tools.todos)
        "oem_todo_write",
        "oem_todo_read",
        "oem_todo_advance",

        # Checkpoints (from oem_knowledge.tools.checkpoint)
        "oem_checkpoint_list",
        "oem_checkpoint_create",
        "oem_checkpoint_restore",
        
        # Telemetry (from oem_knowledge.tools.metrics)
        "knowledge_usage_report",

        # Lifecycle (from oem_knowledge.tools.lifecycle)
        "knowledge_init",
        "knowledge_index",
        "knowledge_reflect",
        "knowledge_session_start",
        "knowledge_read",
        "knowledge_preflight",
        "knowledge_materialize",
        "knowledge_session_commit",
        "knowledge_session_end",
        "knowledge_add_memory",
        "knowledge_export",
        "knowledge_import",

        # Concepts (from oem_knowledge.tools.concepts)
        "knowledge_get_events",
        "knowledge_get_event",
        "knowledge_merge_concepts",
        "knowledge_search",
        "knowledge_explain_concept",

        # Skills (from oem_knowledge.tools.skills)
        "knowledge_skill_candidates",
        "knowledge_skill_candidate_show",
        "knowledge_skill_candidate_approve",
        "knowledge_skill_candidate_reject",
        "knowledge_skill_candidate_defer",

        # Source (from oem_knowledge.tools.source)
        "knowledge_source_search",
        "knowledge_source_read",

        # Diagnostics (from oem_knowledge.tools.diagnostics)
        "knowledge_health_check",
        "knowledge_stats",
    }

    assert registered_tools == expected_tools
    assert len(registered_tools) == 33
