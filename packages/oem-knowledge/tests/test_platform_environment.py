import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_detect_host_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    from oem_knowledge.platform.environment import detect_host
    host = detect_host()
    assert host.value == "windows"


def test_detect_host_wsl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    from oem_knowledge.platform.environment import detect_host
    host = detect_host()
    assert host.value == "wsl"


def test_detect_host_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value="5.15.0-generic"):
        from oem_knowledge.platform.environment import detect_host
        host = detect_host()
    assert host.value == "linux"


def test_classify_project_environment_wsl(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value="5.15.0-generic microsoft"):
        from oem_knowledge.platform.environment import classify_project_environment
        env = classify_project_environment(Path("/home/user/project"))
    assert env.value == "wsl_native"


def test_classify_project_environment_windows_native(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=False):
        from oem_knowledge.platform.environment import classify_project_environment
        env = classify_project_environment(Path(r"C:\Users\test\project"))
    assert env.value == "windows_native"


def test_detect_project_environment_summary_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=False):
        from oem_knowledge.platform.environment import detect_project_environment_summary
        summary = detect_project_environment_summary()
    assert summary["host"] == "linux"
    assert summary["project_env"] is None


def test_classify_project_environment_unc(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value="5.15.0-generic microsoft"):
        from oem_knowledge.platform.environment import classify_project_environment
        env = classify_project_environment(Path("//wsl$/Ubuntu/home/project"))
    assert env.value == "unc_wsl"
