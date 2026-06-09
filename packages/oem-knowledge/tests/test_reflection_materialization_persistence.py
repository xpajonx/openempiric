import pytest
import shutil
import json
import logging
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock
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

def test_git_diff_failure_logs_warning_and_continues(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Use a custom function to mock subprocess.run so that type(subprocess.run).__name__
    # does not contain "mock" (avoiding the test skip check)
    def mock_run(*args, **kwargs):
        raise OSError("Git command failed")

    with patch("subprocess.run", new=mock_run):
        with caplog.at_level(logging.WARNING):
            res = engine.reflection.reflect_session(
                str(temp_project),
                conversation_text="Hypothesis: AI safety is important",
                session_id="session_1"
            )
            # Should complete successfully
            assert res["status"] == "success"
            assert any("Git diff extraction failed" in w for w in res["warnings"])
            # Warning should be logged
            assert any("Failed to extract codebase modifications" in r.message for r in caplog.records)

def test_metrics_emission_failure_does_not_fail_session(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Mock update_metrics_file to fail
    with patch("oem_knowledge.tools.metrics.update_metrics_file", side_effect=RuntimeError("Metrics DB locked")):
        with caplog.at_level(logging.WARNING):
            res = engine.reflection.reflect_session(
                str(temp_project),
                conversation_text="Hypothesis: AI safety is important",
                session_id="session_1"
            )
            assert res["status"] == "success"
            # Verify warning log is present
            assert any("Failed to emit reflection metrics" in r.message for r in caplog.records)

def test_materialization_write_failure_surfaces_error(temp_project, caplog):
    engine = KnowledgeEngine(temp_project)
    
    # Pre-generate a reflection session markdown report so materialize has something to process
    sessions_dir = engine._sessions_dir(str(temp_project))
    sessions_dir.mkdir(parents=True, exist_ok=True)
    report_file = sessions_dir / "session_1.md"
    
    # Session reports must wrap JSON events in a markdown json block
    report_content = "```json\n" + json.dumps({
        "knowledge_events": [
            {"type": "observation", "concept": "ai-safety", "evidence": "Some evidence"}
        ]
    }) + "\n```"
    report_file.write_text(report_content)
    
    # Pre-populate registry with a validated concept so it is materialized (candidate is not materialized)
    registry = {"concept_001": {
        "concept_id": "concept_001",
        "canonical_name": "ai-safety",
        "status": "validated",
        "confidence": 3,
        "evidence_count": 2,
        "sessions": []
    }}
    engine.state._save_registry(registry, str(temp_project))
    
    # Mock _safe_write_concept_file to raise exception
    with patch.object(engine.materialization, "_safe_write_concept_file", side_effect=OSError("Read-only filesystem")):
        with caplog.at_level(logging.ERROR):
            res = engine.materialization.materialize_concepts(str(temp_project))
            assert res["status"] == "error"
            assert "Read-only filesystem" in res["message"]
            assert res["failed_step"] == "materialization"
            assert any("Failed to materialize/write concept file" in r.message for r in caplog.records)

def test_session_commit_stable_shape_on_materialization_failure(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # Mock materialize_concepts to fail
    with patch.object(engine.materialization, "materialize_concepts", return_value={
        "status": "error",
        "failed_step": "materialization",
        "message": "Mock filesystem error"
    }):
        res = engine.session_commit(
            str(temp_project),
            conversation_text="Hypothesis: AI safety is important",
            session_id="session_1"
        )
        assert res["status"] == "error"
        assert res["failed_step"] == "materialization"
        assert res["message"] == "Mock filesystem error"
        # Verify stable shape keys are present
        for key in ["report_path", "knowledge_events", "materialized_log", "links_updated", "index_stats", "explainability", "warnings"]:
            assert key in res

def test_mcp_session_end_failure_output_contains_next_action(temp_project):
    mcp = FastMCP("test_mcp")
    mount_tools(mcp)
    
    # Mock session_commit to return error
    mock_res = {
        "status": "error",
        "failed_step": "materialization",
        "message": "Mock filesystem error"
    }
    with patch.object(KnowledgeEngine, "session_commit", return_value=mock_res):
        result = asyncio.run(mcp.call_tool(
            "knowledge_session_end",
            {
                "project": str(temp_project),
                "conversation_text": "Hypothesis: test",
                "session_id": "sess_1"
            }
        ))
        out = result.content[0].text
        assert "# Session End Failure" in out
        assert "Failed step: materialization" in out
        assert "Reason: Mock filesystem error" in out
        assert "Please retry session end after fixing the issue." in out

def test_opencode_exit_commit_truthful_result(temp_project):
    engine = KnowledgeEngine(temp_project)
    
    # 1. Normal success path
    res_success = engine.session_commit(
        str(temp_project),
        conversation_text="Hypothesis: AI safety is important",
        session_id="session_1"
    )
    assert res_success["status"] in ("success", "partial")
    
    # 2. Failure path
    with patch.object(engine.materialization, "materialize_concepts", return_value={
        "status": "error",
        "failed_step": "materialization",
        "message": "Disk full"
    }):
        res_fail = engine.session_commit(
            str(temp_project),
            conversation_text="Hypothesis: AI safety is important",
            session_id="session_2"
        )
        assert res_fail["status"] == "error"
        assert res_fail["failed_step"] == "materialization"
