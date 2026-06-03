from __future__ import annotations

import sys
from unittest.mock import patch
import pytest
from pathlib import Path
import tempfile
import shutil

from harness_knowledge.cli import main


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def test_oem_init(tmp_proj):
    """Verify that 'oem init' creates the .oem structure."""
    with patch.object(sys, "argv", ["oem", "init", tmp_proj]):
        main()
    
    assert (Path(tmp_proj) / ".oem").is_dir()
    assert (Path(tmp_proj) / ".oem" / "wiki").is_dir()
    assert (Path(tmp_proj) / ".oem" / "state").is_dir()


def test_oem_session_start(tmp_proj):
    """Verify that 'oem session-start' invokes restoration correctly."""
    # First initialize project
    with patch.object(sys, "argv", ["oem", "init", tmp_proj]):
        main()

    # Now test session-start
    with patch.object(sys, "argv", ["oem", "session-start", "--project", tmp_proj]):
        main()


def test_oem_session_end(tmp_proj):
    """Verify that 'oem session-end' runs session_commit pipeline successfully."""
    # First initialize project
    with patch.object(sys, "argv", ["oem", "init", tmp_proj]):
        main()

    # Now test session-end
    with patch.object(
        sys,
        "argv",
        [
            "oem",
            "session-end",
            "--project",
            tmp_proj,
            "--chat",
            "decision: Use python 3.12",
            "--session-id",
            "test-session-123",
        ],
    ):
        main()

    # Check that a session report was written
    sessions_dir = Path(tmp_proj) / ".oem" / "sessions"
    assert sessions_dir.is_dir()
    assert len(list(sessions_dir.glob("*.md"))) >= 1


def test_oem_run(tmp_proj):
    """Verify that 'oem run' spawns the specified command writes context to file (not config)."""
    from harness_knowledge.cli import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS

    with patch("harness_knowledge.cli.subprocess.run") as mock_run:
        with patch.object(sys, "argv", ["oem", "run", "mock-agent", "--project", tmp_proj]):
            # Context file should not exist before run
            assert not _OEM_RUNTIME_CONTEXT_PATH.exists()
            assert not _OEM_TEMP_INSTRUCTIONS.exists()

            main()

            mock_run.assert_called_once_with(["mock-agent"], check=True)

    # Both transient files should be cleaned up after run finishes
    assert not _OEM_RUNTIME_CONTEXT_PATH.exists()
    assert not _OEM_TEMP_INSTRUCTIONS.exists()
