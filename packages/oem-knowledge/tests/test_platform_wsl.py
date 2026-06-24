import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def test_is_wsl_false(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value="5.15.0-25-generic"):
        from oem_knowledge.platform.wsl import is_wsl
        assert not is_wsl()


def test_is_wsl_from_env(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    from oem_knowledge.platform.wsl import is_wsl
    assert is_wsl()


def test_is_wsl_from_osrelease(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "read_text", return_value="5.15.0-25-generic microsoft"):
        from oem_knowledge.platform.wsl import is_wsl
        assert is_wsl()


def test_is_wsl_osrelease_not_found(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    with patch.object(Path, "exists", return_value=False):
        from oem_knowledge.platform.wsl import is_wsl
        assert not is_wsl()


def test_windows_to_wsl_path_drive_letter(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/wslpath")
    import subprocess
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
        r = Result()
        r.stdout = "/mnt/c/Users/test"
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import windows_to_wsl_path
    result = windows_to_wsl_path(r"C:\Users\test")
    assert result == "/mnt/c/Users/test"


def test_windows_to_wsl_path_wslpath_not_found(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    from oem_knowledge.platform.wsl import windows_to_wsl_path
    result = windows_to_wsl_path(r"C:\Users\test")
    assert "/mnt/c/Users/test" in result


def test_windows_to_wsl_path_already_unix(monkeypatch):
    from oem_knowledge.platform.wsl import windows_to_wsl_path
    result = windows_to_wsl_path("/home/user/project")
    assert result == "/home/user/project"


def test_list_wsl_distros(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
        r = Result()
        r.stdout = "  Ubuntu (Default)\n  Debian\n"
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import list_wsl_distros
    distros = list_wsl_distros()
    assert distros == ["Ubuntu", "Debian"]


def test_list_wsl_distros_failure(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 1
        r = Result()
        r.stdout = ""
        r.stderr = "error"
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import list_wsl_distros
    assert list_wsl_distros() == []


def test_detect_default_wsl_distro_default(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        if "-l" in cmd:
            stdout = "  Ubuntu (Default)\n  Debian\n"
        elif "--set-default-version" in cmd:
            stdout = "Default Distribution: Ubuntu\n"
        else:
            stdout = ""
        class Result:
            returncode = 0
        r = Result()
        r.stdout = stdout
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import detect_default_wsl_distro
    result = detect_default_wsl_distro()
    assert result == "Ubuntu"


def test_detect_default_wsl_distro_no_default(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        if "-l" in cmd:
            stdout = "  Ubuntu\n  Debian\n"
        elif "--set-default-version" in cmd:
            stdout = ""
        else:
            stdout = ""
        class Result:
            returncode = 0
        r = Result()
        r.stdout = stdout
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import detect_default_wsl_distro
    assert detect_default_wsl_distro() is None


def test_detect_default_wsl_distro_single(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        if "-l" in cmd:
            stdout = "  Ubuntu\n"
        elif "--set-default-version" in cmd:
            stdout = ""
        else:
            stdout = ""
        class Result:
            returncode = 0
        r = Result()
        r.stdout = stdout
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import detect_default_wsl_distro
    assert detect_default_wsl_distro() == "Ubuntu"


def test_distro_from_unc_path():
    from oem_knowledge.platform.wsl import distro_from_unc_path
    assert distro_from_unc_path(Path("//wsl$/Ubuntu/home/user")) == "ubuntu"
    assert distro_from_unc_path(Path("//wsl.localhost/Debian/home/user")) == "debian"
    assert distro_from_unc_path(Path("/home/user")) is None


def test_wsl_path_from_unc():
    from oem_knowledge.platform.wsl import wsl_path_from_unc
    result = wsl_path_from_unc(Path("//wsl$/Ubuntu/home/user/project"))
    assert result == "/home/user/project"
    result2 = wsl_path_from_unc(Path("//wsl.localhost/Debian/mnt/c/tmp"))
    assert result2 == "/mnt/c/tmp"
    assert wsl_path_from_unc(Path("/linux/path")) is None


def test_shell_quote():
    from oem_knowledge.platform.wsl import shell_quote
    assert shell_quote("simple") == "'simple'"
    assert shell_quote("has'quote") == "'has'\"'\"'quote'"
    assert shell_quote("") == "''"


def test_get_wsl_exe_path(monkeypatch):
    monkeypatch.setenv("WINDIR", "C:\\Windows")
    from oem_knowledge.platform.wsl import get_wsl_exe_path
    path = get_wsl_exe_path()
    assert "System32" in path
    assert "wsl.exe" in path


def test_get_wsl_exe_path_from_wsl(monkeypatch):
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.delenv("SystemRoot", raising=False)
    import subprocess
    def fake_run(cmd, **kwargs):
        class Result:
            returncode = 0
        r = Result()
        r.stdout = "C:\\Windows"
        r.stderr = ""
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)
    from oem_knowledge.platform.wsl import get_wsl_exe_path
    path = get_wsl_exe_path()
    assert "System32" in path
    assert "wsl.exe" in path
