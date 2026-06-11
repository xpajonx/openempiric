from __future__ import annotations
import io
import contextlib
import tempfile
import shutil
from pathlib import Path
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.runtime.supervisor import CommitProgressSupervisor, render_commit_complete_panel


def test_commit_progress_is_emitted():
    # Capture stdout during CommitProgressSupervisor execution
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        progress = CommitProgressSupervisor(force_tty=False)
        progress.start()
        progress.update_step("transcript", "success")
        progress.update_step("reflection", "success")
        progress.update_step("materialization", "success")
        progress.update_step("index", "success")

    output = f.getvalue()
    # Should print borders and all status changes sequentially
    assert "OEM Session Commit" in output
    assert "Transcript Loaded" in output
    assert "Reflection Complete" in output
    assert "Materialization Complete" in output
    assert "Updating Search Index" in output


def test_render_commit_complete_panel():
    panel = render_commit_complete_panel(
        report_name="2026-06-06.md",
        concepts_count=5,
        observations_count=3,
        duration=1.5,
        structured_events=2,
        fallback_concepts=1,
        file_observations=0
    )
    assert "Session End Complete" in panel
    assert "Report: 2026-06-06.md" in panel
    assert "Concepts Materialized: 5" in panel
    assert "Commit Time: 1.5s" in panel
    assert "Structured Events: 2" in panel
    assert "Fallback Concepts: 1" in panel
    assert "File Observations: 0" in panel


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_session_commit_orchestrates_progress(tmp_proj):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        # Trigger session commit
        eng.session_commit(tmp_proj, conversation_text="Hypothesis: test")

    output = f.getvalue()
    assert "OEM Session Commit" in output
