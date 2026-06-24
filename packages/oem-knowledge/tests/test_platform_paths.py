import os
from pathlib import Path

import pytest


def test_is_unc_path():
    from oem_knowledge.platform.paths import is_unc_path
    assert is_unc_path(r"\\server\share")
    assert is_unc_path(r"\\wsl$\Ubuntu\home")
    assert not is_unc_path("C:\\Users")
    assert not is_unc_path("/linux/path")


def test_is_windows_path():
    from oem_knowledge.platform.paths import is_windows_path
    assert is_windows_path(r"C:\Users")
    assert is_windows_path(r"D:\projects")
    assert not is_windows_path("/linux/path")
    assert not is_windows_path(r"\\wsl$\path")


def test_is_mounted_windows_path(monkeypatch):
    monkeypatch.setattr(Path, "exists", lambda self: True)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)
    from oem_knowledge.platform.paths import is_mounted_windows_path
    assert is_mounted_windows_path("/mnt/c/Users")
    assert is_mounted_windows_path("/mnt/d/projects")
    assert not is_mounted_windows_path("/home/user")


def test_normalize_to_wsl_path_windows(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    from oem_knowledge.platform.paths import normalize_to_wsl_path
    result = normalize_to_wsl_path(r"C:\Users\test")
    assert result is not None


def test_normalize_to_wsl_path_unix():
    from oem_knowledge.platform.paths import normalize_to_wsl_path
    result = normalize_to_wsl_path("/home/user/project")
    assert result == "/home/user/project"


def test_normalize_to_windows_path_unix():
    from oem_knowledge.platform.paths import normalize_to_windows_path
    result = normalize_to_windows_path("/mnt/c/Users/test")
    assert result is not None
