import pytest
import shutil
import sys
from pathlib import Path
from unittest.mock import patch
from oem_knowledge.cli.parser import _setup_parser
from oem_knowledge.cli.commands.session import run_session_command
from oem_knowledge.engine import KnowledgeEngine

@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    yield project_dir
    shutil.rmtree(project_dir)

def test_cli_session_end_verbose_prints_phase_progress(temp_project, capsys):
    parser = _setup_parser()
    args = parser.parse_args(["session-end", "--project", str(temp_project), "--chat", "decision: test verbose output", "--verbose"])
    run_session_command(args)
    captured = capsys.readouterr()
    assert "[session] load_state" in captured.out
    assert "[session] reflection" in captured.out
    assert "[session] append_events" in captured.out
    assert "[session] materialization" in captured.out
    assert "[session] search_index" in captured.out
    assert "[session] write_report" in captured.out
    assert "[session] done in" in captured.out

def test_cli_session_end_no_index_skips_search_index(temp_project):
    parser = _setup_parser()
    args = parser.parse_args(["session-end", "--project", str(temp_project), "--chat", "decision: test no index option", "--no-index"])
    with patch.object(KnowledgeEngine, "session_commit", wraps=KnowledgeEngine(temp_project).session_commit) as mock_commit:
        try:
            run_session_command(args)
        except SystemExit:
            pass
        mock_commit.assert_called_once()
        assert mock_commit.call_args[1]["update_index"] is False

def test_cli_session_end_negative_index_budget_rejected(temp_project, capsys):
    parser = _setup_parser()
    args = parser.parse_args(["session-end", "--project", str(temp_project), "--chat", "test negative budget", "--index-budget-seconds", "-5"])
    with pytest.raises(SystemExit) as excinfo:
        run_session_command(args)
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "must be non-negative" in captured.out

def test_cli_session_end_no_index_and_index_budget_conflict_rejected(temp_project, capsys):
    parser = _setup_parser()
    args = parser.parse_args(["session-end", "--project", str(temp_project), "--chat", "test conflict", "--no-index", "--index-budget-seconds", "5"])
    with pytest.raises(SystemExit) as excinfo:
        run_session_command(args)
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "Cannot specify both --no-index and --index-budget-seconds" in captured.out
