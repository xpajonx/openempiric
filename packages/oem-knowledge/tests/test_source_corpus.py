import pytest
import shutil
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from oem_knowledge.cli.parser import _setup_parser
from oem_knowledge.cli.commands.knowledge import run_knowledge_command
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.server import resolve_active_project, handle_resolution_error

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    
    # Create a couple of sample files to index inside valid directories
    src_dir = project_dir / "src"
    src_dir.mkdir()
    docs_dir = project_dir / "docs"
    docs_dir.mkdir()
    
    file1 = src_dir / "src_test_file.py"
    file1.write_text("def test_function():\n    return 'hello world'\n", encoding="utf-8")
    
    file2 = docs_dir / "docs_test_file.md"
    file2.write_text("# Documentation\nThis is a sample document for testing.\n", encoding="utf-8")
    
    yield project_dir
    shutil.rmtree(project_dir)

def test_cli_source_index_dry_run(temp_project, capsys):
    parser = _setup_parser()
    args = parser.parse_args(["source", "index", "--project", str(temp_project), "--dry-run"])
    run_knowledge_command(args)
    captured = capsys.readouterr()
    assert "Source Indexing (Dry Run)" in captured.out
    assert "Operation:         DRY_RUN" in captured.out
    assert "Scanned Files:     2" in captured.out

def test_cli_source_index_and_stats_and_search_and_read(temp_project, capsys):
    parser = _setup_parser()
    
    # 1. Run index
    args_index = parser.parse_args(["source", "index", "--project", str(temp_project)])
    run_knowledge_command(args_index)
    captured = capsys.readouterr()
    assert "Source Indexing Complete" in captured.out
    assert "Operation:         WRITE" in captured.out
    assert "Scanned Files:     2" in captured.out

    # 2. Run stats
    args_stats = parser.parse_args(["source", "stats", "--project", str(temp_project)])
    run_knowledge_command(args_stats)
    captured = capsys.readouterr()
    assert "Source Stats" in captured.out
    assert "Total Chunks:" in captured.out
    assert "Database Size:" in captured.out

    # 3. Run search
    args_search = parser.parse_args(["source", "search", "test_function", "--project", str(temp_project)])
    run_knowledge_command(args_search)
    captured = capsys.readouterr()
    assert "Source Search Results" in captured.out
    assert "src/src_test_file.py" in captured.out

    # 4. Run read
    args_read = parser.parse_args(["source", "read", "src/src_test_file.py", "--start-line", "1", "--end-line", "2", "--project", str(temp_project)])
    run_knowledge_command(args_read)
    captured = capsys.readouterr()
    assert "Source Content" in captured.out
    assert "def test_function():" in captured.out

def test_mcp_source_tools(temp_project):
    import asyncio
    from fastmcp import FastMCP
    from oem_knowledge.server import mount_tools
    
    mcp = FastMCP("test_openempiric")
    mount_tools(mcp)
    
    # Make sure tools are registered
    tools = asyncio.run(mcp.list_tools())
    search_tool = next((t for t in tools if t.name == "knowledge_source_search"), None)
    read_tool = next((t for t in tools if t.name == "knowledge_source_read"), None)
    
    assert search_tool is not None
    assert read_tool is not None
    
    # 1. Index the project first
    with KnowledgeEngine(str(temp_project)) as eng:
        eng.source.index()
    
    # 2. Test knowledge_source_search via the registered tool function
    search_fn = search_tool.fn
    search_res_json = search_fn(query="test_function", project=str(temp_project))
    search_res = json.loads(search_res_json)
    
    assert search_res["status"] == "success"
    assert search_res["operation"] == "knowledge_source_search"
    assert len(search_res["results"]) > 0
    assert search_res["results"][0]["metadata"]["rel_path"] == "src/src_test_file.py"
    
    # 3. Test knowledge_source_read via the registered tool function
    read_fn = read_tool.fn
    read_res_json = read_fn(path="src/src_test_file.py", start_line=1, end_line=2, project=str(temp_project))
    read_res = json.loads(read_res_json)
    
    assert read_res["status"] == "success"
    assert read_res["operation"] == "knowledge_source_read"
    assert "def test_function():" in read_res["content"]
