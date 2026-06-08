from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys

import pytest

from oem_knowledge.adapters import get_adapter
from oem_knowledge.adapters.codex_app.adapter import CODEX_SKILL_CONTENT, CodexAppAdapter
from oem_knowledge.cli import main
from oem_knowledge.engine import KnowledgeEngine


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    monkeypatch.setenv("OEM_CODEX_HOME", str(home))
    monkeypatch.setenv("OEM_CODEX_WSL_PROJECT_DIR", "/home/xpajonx/.config/openempiric-dev")
    monkeypatch.setenv("OEM_CODEX_WSL_EXE", "C:\\Windows\\System32\\wsl.exe")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    return home


def test_codex_app_adapter_aliases(tmp_path):
    eng = KnowledgeEngine(str(tmp_path))

    assert isinstance(get_adapter("codex", eng, str(tmp_path)), CodexAppAdapter)
    assert isinstance(get_adapter("codex-app", eng, str(tmp_path)), CodexAppAdapter)


def test_setup_writes_wsl_mcp_and_preserves_config(tmp_path, codex_home):
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[desktop]\nconversationDetailMode = "STEPS_COMMANDS"\n\n[mcp_servers.node_repl]\ncommand = "node"\n',
        encoding="utf-8",
    )

    eng = KnowledgeEngine(str(tmp_path))
    adapter = CodexAppAdapter(eng, str(tmp_path))
    res = adapter.setup()

    assert Path(res["skill_path"]).exists()
    assert "OpenEmpiric" in Path(res["skill_path"]).read_text(encoding="utf-8")

    text = config.read_text(encoding="utf-8")
    assert "[desktop]" in text
    assert "[mcp_servers.node_repl]" in text
    assert "[mcp_servers.openempiric]" in text

    data = tomllib.loads(text)
    bridge = data["mcp_servers"]["openempiric"]
    assert bridge["command"] == "C:\\Windows\\System32\\wsl.exe"
    assert bridge["args"] == [
        "-d",
        "Ubuntu",
        "--cd",
        "/home/xpajonx/.config/openempiric-dev",
        "bash",
        "-lc",
        "exec oem mcp",
    ]
    assert bridge["startup_timeout_sec"] == 120


def test_setup_replaces_existing_mcp_block_with_parseable_windows_path(tmp_path, codex_home):
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.openempiric]\ncommand = "wsl.exe"\nargs = []\n',
        encoding="utf-8",
    )

    adapter = CodexAppAdapter(KnowledgeEngine(str(tmp_path)), str(tmp_path))
    adapter.setup(repair=True)

    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["openempiric"]["command"] == "C:\\Windows\\System32\\wsl.exe"


def test_codex_home_prefers_windows_profile_when_running_in_wsl(tmp_path, monkeypatch):
    monkeypatch.delenv("OEM_CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr(sys, "platform", "linux")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["powershell.exe", "-NoProfile", "-Command"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="C:\\Users\\upper\n", stderr="")
        if cmd[:2] == ["wslpath", "-u"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/mnt/c/Users/upper/.codex\n", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    with patch("oem_knowledge.adapters.codex_app.adapter.subprocess.run", side_effect=fake_run):
        adapter = CodexAppAdapter(KnowledgeEngine(str(tmp_path)), str(tmp_path))
        assert adapter.get_codex_home() == Path("/mnt/c/Users/upper/.codex")


def test_setup_repair_replaces_oem_owned_skill(tmp_path, codex_home):
    skill = codex_home / "skills" / "openempiric" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("old skill", encoding="utf-8")

    adapter = CodexAppAdapter(KnowledgeEngine(str(tmp_path)), str(tmp_path))
    adapter.setup(repair=True)

    assert skill.read_text(encoding="utf-8") == CODEX_SKILL_CONTENT


def test_verify_health_reports_missing_skill(tmp_path, codex_home):
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[mcp_servers.openempiric]\ncommand = \"wsl.exe\"\nargs = []\n", encoding="utf-8")

    adapter = CodexAppAdapter(KnowledgeEngine(str(tmp_path)), str(tmp_path))
    healthy, message = adapter.verify_health(probe_bridge=False)

    assert healthy is False
    assert "skill not found" in message.lower()


def test_verify_health_reports_unreachable_bridge(tmp_path, codex_home):
    adapter = CodexAppAdapter(KnowledgeEngine(str(tmp_path)), str(tmp_path))
    adapter.setup()

    with patch.object(adapter, "verify_bridge", return_value=(False, "WSL bridge check failed")):
        healthy, message = adapter.verify_health()

    assert healthy is False
    assert message == "WSL bridge check failed"


def test_run_codex_app_does_not_spawn_process(tmp_path, codex_home):
    with patch.object(CodexAppAdapter, "verify_bridge", return_value=(True, "OK")):
        with patch("oem_knowledge.runtime.runner.subprocess.run") as mock_run:
            with patch("sys.argv", ["oem", "run", "codex-app", "--project", str(tmp_path)]):
                main()

    mock_run.assert_not_called()
    assert (tmp_path / ".oem").is_dir()
