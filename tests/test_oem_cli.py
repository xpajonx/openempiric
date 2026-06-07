from __future__ import annotations

import sys
from unittest.mock import patch
import pytest
from pathlib import Path
import tempfile
import shutil

from oem_knowledge.cli import main


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
    """Verify that the deprecated 'oem session-start' command remains available internally for backward compatibility."""
    # First initialize project
    with patch.object(sys, "argv", ["oem", "init", tmp_proj]):
        main()

    # Now test session-start
    with patch.object(sys, "argv", ["oem", "session-start", "--project", tmp_proj]):
        main()


def test_oem_session_end(tmp_proj):
    """Verify that the deprecated 'oem session-end' command remains available internally for backward compatibility."""
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
    from oem_knowledge.cli import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS

    with patch("oem_knowledge.cli.subprocess.run") as mock_run:
        with patch.object(sys, "argv", ["oem", "run", "mock-agent", "--project", tmp_proj]):
            # Context file should not exist before run
            assert not _OEM_RUNTIME_CONTEXT_PATH.exists()
            assert not _OEM_TEMP_INSTRUCTIONS.exists()

            main()

            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            assert args[0] == ["mock-agent"]
            assert kwargs.get("check") is True
            assert kwargs.get("env") is not None
            assert kwargs["env"].get("OEM_MANAGED") == "1"
            assert "OEM_SESSION_ID" in kwargs["env"]
            assert kwargs["env"].get("OEM_PROJECT") == tmp_proj

    # Both transient files should be cleaned up after run finishes
    assert not _OEM_RUNTIME_CONTEXT_PATH.exists()
    assert not _OEM_TEMP_INSTRUCTIONS.exists()


def test_oem_run_opencode_safe_bootstrap(tmp_proj):
    """Verify that `oem run opencode` bootstraps project-local state without editing global config."""
    import oem_knowledge.runtime.runner as runner

    temp_home = tempfile.mkdtemp()
    try:
        home_path = Path(temp_home)
        plugins_dir = home_path / ".config" / "opencode" / "plugins"
        context_path = plugins_dir / ".oem_runtime_context.json"
        temp_instructions = plugins_dir / ".openempiric_temp_instructions.md"

        with patch("pathlib.Path.home", return_value=home_path):
            with patch.object(runner, "_OPENCODE_PLUGINS_DIR", plugins_dir):
                with patch.object(runner, "_OEM_RUNTIME_CONTEXT_PATH", context_path):
                    with patch.object(runner, "_OEM_TEMP_INSTRUCTIONS", temp_instructions):
                        with patch("oem_knowledge.cli.subprocess.run") as mock_run:
                            with patch.object(sys, "argv", ["oem", "run", "opencode", "--project", tmp_proj]):
                                main()

        mock_run.assert_called_once()
        assert (Path(tmp_proj) / ".oem").is_dir()
        assert (Path(tmp_proj) / ".oem" / "skills" / "openempiric.yaml").exists()
        assert (plugins_dir / "openempiric.ts").exists()
        assert not (home_path / ".config" / "opencode" / "opencode.jsonc").exists()
    finally:
        shutil.rmtree(temp_home)


def test_oem_doctor_user_project(tmp_proj):
    """Verify that 'oem doctor' succeeds (exits 0) when run in a user project directory."""
    temp_home = tempfile.mkdtemp()
    try:
        with patch("pathlib.Path.home", return_value=Path(temp_home)):
            with patch("oem_knowledge.cli.check_mcp_server", return_value=(True, True, 19, "")):
                # Run setup first
                with patch.object(sys, "argv", ["oem", "setup", "opencode"]):
                    main()
                # Run doctor
                with patch.object(sys, "argv", ["oem", "doctor", "--project", tmp_proj]):
                    try:
                        main()
                    except SystemExit as e:
                        assert e.code == 0 or e.code is None
    finally:
        shutil.rmtree(temp_home)


def test_oem_warmup_failure(tmp_proj):
    """Verify that 'oem warmup' fails with exit code 1 when fastembed is not installed."""
    from unittest.mock import PropertyMock
    with patch("oem_knowledge.engine.KnowledgeEngine.model", new_callable=PropertyMock) as mock_model:
        mock_model.return_value = None
        with patch.object(sys, "argv", ["oem", "warmup", "--project", tmp_proj]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


def test_oem_warmup_success(tmp_proj):
    """Verify that 'oem warmup' succeeds (exits 0 or completes) when fastembed is installed."""
    from unittest.mock import PropertyMock, MagicMock
    with patch("oem_knowledge.engine.KnowledgeEngine.model", new_callable=PropertyMock) as mock_model:
        mock_model.return_value = MagicMock()
        with patch.object(sys, "argv", ["oem", "warmup", "--project", tmp_proj]):
            try:
                main()
            except SystemExit as e:
                assert e.code == 0 or e.code is None
