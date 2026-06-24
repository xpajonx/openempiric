import os
from pathlib import Path

import pytest
from oem_knowledge.platform.environment import HostOS


def test_opencode_mcp_mode_values():
    from oem_knowledge.integrations.opencode.mcp import OpenCodeMCPMode
    assert OpenCodeMCPMode.WINDOWS_NATIVE.value == "windows_native"
    assert OpenCodeMCPMode.WSL_BRIDGE.value == "wsl_bridge"
    assert OpenCodeMCPMode.LINUX_DIRECT.value == "linux_direct"
    assert OpenCodeMCPMode.BLOCKED.value == "blocked"


def test_recommend_opencode_mcp_mode_linux_direct(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    from oem_knowledge.integrations.opencode.mcp import recommend_opencode_mcp_mode, OpenCodeMCPMode
    result = recommend_opencode_mcp_mode(Path("/home/user/project"))
    assert result["mode"] == OpenCodeMCPMode.LINUX_DIRECT


def test_recommend_opencode_mcp_mode_wsl_bridge(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    from oem_knowledge.integrations.opencode import mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "command_exists_in_wsl", lambda cmd, distro=None: True)
    result = mcp_mod.recommend_opencode_mcp_mode(Path("/home/user/project"))
    assert result["mode"] == mcp_mod.OpenCodeMCPMode.WSL_BRIDGE


def test_recommend_opencode_mcp_mode_wsl_bridge_with_distro(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    from oem_knowledge.integrations.opencode import mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "command_exists_in_wsl", lambda cmd, distro=None: True)
    result = mcp_mod.recommend_opencode_mcp_mode(Path("/home/user/project"), wsl_distro="Ubuntu")
    assert result["mode"] == mcp_mod.OpenCodeMCPMode.WSL_BRIDGE
    assert result["wsl_distro"] == "Ubuntu"


def test_recommend_opencode_mcp_mode_windows_native(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    from oem_knowledge.integrations.opencode.mcp import recommend_opencode_mcp_mode, OpenCodeMCPMode
    result = recommend_opencode_mcp_mode(Path("C:\\Users\\test\\project"))
    assert result["mode"] == OpenCodeMCPMode.BLOCKED  # no oem in windows, no wsl distros


def test_recommend_opencode_mcp_mode_windows_multiple_distros_blocks(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("shutil.which", lambda cmd: r"C:\Windows\system32\oem.exe")
    import subprocess
    def fake_wsl_list(cmd, **kwargs):
        class Result:
            returncode = 0
        r = Result()
        r.stdout = "  Ubuntu\n  Debian\n"
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_wsl_list)
    from oem_knowledge.integrations.opencode.mcp import recommend_opencode_mcp_mode, OpenCodeMCPMode
    result = recommend_opencode_mcp_mode(Path("C:\\Users\\test\\project"))
    assert result["mode"] == OpenCodeMCPMode.BLOCKED


def test_build_opencode_mcp_command_windows_native(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    from oem_knowledge.integrations.opencode.mcp import build_opencode_mcp_command, OpenCodeMCPMode
    cmd = build_opencode_mcp_command(Path("."), OpenCodeMCPMode.WINDOWS_NATIVE)
    assert cmd is not None
    assert "oem" in cmd.get("command", "")
    assert "timeout" in cmd


def test_build_opencode_mcp_command_wsl_bridge(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WINDIR", "C:\\Windows")
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    from oem_knowledge.integrations.opencode.mcp import build_opencode_mcp_command, OpenCodeMCPMode
    cmd = build_opencode_mcp_command(Path("/home/user/project"), OpenCodeMCPMode.WSL_BRIDGE, "Ubuntu")
    assert cmd is not None, f"Got None for WSL_BRIDGE mode"
    assert "wsl.exe" in cmd.get("command", "") or "wsl.exe" in str(cmd.get("args", ""))


def test_build_opencode_mcp_command_linux_direct(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    from oem_knowledge.integrations.opencode.mcp import build_opencode_mcp_command, OpenCodeMCPMode
    cmd = build_opencode_mcp_command(Path("/home/user/project"), OpenCodeMCPMode.LINUX_DIRECT)
    assert cmd is not None
    assert cmd.get("command") or cmd.get("args")


def test_build_opencode_mcp_command_blocked():
    from oem_knowledge.integrations.opencode.mcp import build_opencode_mcp_command, OpenCodeMCPMode
    cmd = build_opencode_mcp_command(Path("."), OpenCodeMCPMode.BLOCKED)
    assert cmd is None


def test_detect_possible_split_memory_wsl(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    from oem_knowledge.integrations.opencode.mcp import detect_possible_split_memory
    result = detect_possible_split_memory(Path("/home/user/project"))
    assert isinstance(result, dict)
    assert "split_detected" in result
    assert "windows_oem_path" in result
    assert "wsl_oem_path" in result


def test_detect_possible_split_memory_linux(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    from oem_knowledge.integrations.opencode import mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "detect_host", lambda: HostOS.LINUX)
    from oem_knowledge.integrations.opencode.mcp import detect_possible_split_memory
    result = detect_possible_split_memory(Path("/tmp/test-project-no-oem"))
    assert result["split_detected"] is False
