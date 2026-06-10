import pytest
import shutil
import asyncio
from pathlib import Path
from unittest.mock import patch
from oem_knowledge.engine import KnowledgeEngine
from fastmcp import FastMCP
from oem_knowledge.server import mount_tools

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)

def test_mcp_session_end_includes_phase_timings_on_partial(temp_project):
    mcp = FastMCP("test_mcp")
    mount_tools(mcp)

    # Mock index_all to return partial indexing
    def mock_index_all(*args, **kwargs):
        return {
            "status": "partial",
            "error": "Indexing budget exceeded"
        }
        
    with patch("oem_knowledge.services.search.SearchService._index_all_impl", new=mock_index_all):
        res = asyncio.run(mcp.call_tool(
            "knowledge_session_end",
            {
                "project": str(temp_project),
                "conversation_text": "- Fix test partial timing mcp",
                "session_id": "test_mcp_ses_1"
            }
        ))
        res_str = res.content[0].text
        assert "Session commit completed partially" in res_str
        assert "Warnings:" in res_str
        assert "Search indexing skipped after timeout budget" in res_str
        assert "### Timing:" in res_str
        assert "reflection:" in res_str
        assert "materialization:" in res_str
        assert "search_index: skipped" in res_str
        assert "\033" not in res_str  # No ANSI escape codes
