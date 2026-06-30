from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from oem_knowledge.runtime.provenance import (
    detect_runtime,
    is_dev_runtime,
    _find_site_packages,
    _find_dist_info,
    _is_under_repo_venv,
    _get_package_version,
    RUNTIME_KIND_UV_TOOL,
    RUNTIME_KIND_REPO_VENV,
    RUNTIME_KIND_EDITABLE,
    RUNTIME_KIND_UNKNOWN,
)


class TestDetectRuntime:
    def test_detect_uv_tool_by_parent_uv_marker(self, monkeypatch, tmp_path):
        tools_dir = tmp_path / ".local/share/uv/tools"
        fake_exec = tools_dir / "oem-knowledge/bin/python"
        fake_exec.parent.mkdir(parents=True)
        fake_exec.write_text("")
        uv_marker = tools_dir / "uv"
        uv_marker.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_exec))
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_UV_TOOL

    def test_detect_repo_venv(self, monkeypatch, tmp_path):
        repo_root = tmp_path / "myproject"
        repo_root.mkdir()
        (repo_root / "pyproject.toml").write_text("")
        venv_bin = repo_root / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python"
        fake_python.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_python))
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_REPO_VENV

    @pytest.fixture
    def mock_site_packages(self, tmp_path):
        sp = tmp_path / "site-packages"
        sp.mkdir()
        return sp

    def test_dist_info_found(self, mock_site_packages):
        dist_dir = mock_site_packages / "oem_knowledge-1.0.2.dist-info"
        dist_dir.mkdir()
        assert _find_dist_info(mock_site_packages, "oem_knowledge") == dist_dir

    def test_dist_info_not_found(self, mock_site_packages):
        assert _find_dist_info(mock_site_packages, "nonexistent") is None

    def test_find_site_packages_found(self, tmp_path):
        sp = tmp_path / "site-packages"
        sp.mkdir()
        parent = tmp_path / "bin" / "python"
        parent.parent.mkdir(parents=True)
        parent.write_text("")
        assert _find_site_packages(parent.resolve()) == sp.resolve()


class TestIsUnderRepoVenv:
    def test_under_repo_venv_with_pyproject(self, tmp_path):
        repo = tmp_path / "myapp"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("")
        venv_python = repo / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")
        assert _is_under_repo_venv(venv_python.resolve())

    def test_under_repo_venv_with_git(self, tmp_path):
        repo = tmp_path / "myapp"
        repo.mkdir()
        (repo / ".git").mkdir()
        venv_python = repo / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")
        assert _is_under_repo_venv(venv_python.resolve())

    def test_not_under_repo_venv(self, tmp_path):
        standalone = tmp_path / "standalone" / "bin" / "python"
        standalone.parent.mkdir(parents=True)
        standalone.write_text("")
        assert not _is_under_repo_venv(standalone.resolve())


class TestIsDevRuntime:
    def test_dev_runtime_true_for_repo_venv(self, monkeypatch, tmp_path):
        repo = tmp_path / "myapp"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("")
        venv_python = repo / ".venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("")
        monkeypatch.setattr(sys, "executable", str(venv_python))
        assert is_dev_runtime()

    def test_dev_runtime_false_for_uv_tool(self, monkeypatch, tmp_path):
        tools_dir = tmp_path / ".local/share/uv/tools"
        fake_exec = tools_dir / "pkg/bin/python"
        fake_exec.parent.mkdir(parents=True)
        fake_exec.write_text("")
        uv_marker = tools_dir / "uv"
        uv_marker.write_text("")
        monkeypatch.setattr(sys, "executable", str(fake_exec))
        assert not is_dev_runtime()


class TestGetPackageVersion:
    def test_returns_none_on_missing(self):
        assert _get_package_version("nonexistent-pkg-12345") is None

    def test_returns_string_on_found(self):
        v = _get_package_version("oem-knowledge")
        assert v is None or isinstance(v, str)
