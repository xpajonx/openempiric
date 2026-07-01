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


class TestProvenanceRefinements:
    def test_detect_runtime_sets_kind_editable_from_direct_url_json(self, monkeypatch, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        fake_python.write_text("")
        
        sp_dir = venv_dir / "lib" / "python3.12" / "site-packages"
        dist_info = sp_dir / "oem_knowledge-1.0.4+local.dist-info"
        dist_info.mkdir(parents=True)
        
        direct_url_file = dist_info / "direct_url.json"
        direct_url_file.write_text(json.dumps({
            "url": "file:///home/xpajonx/.config/openempiric-dev/openempiric",
            "dir_info": {
                "editable": True
            }
        }))
        
        monkeypatch.setattr(sys, "executable", str(fake_python))
        monkeypatch.setattr(sys, "path", [])
        
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_EDITABLE

    def test_is_dev_runtime_true_for_editable_runtime(self):
        assert is_dev_runtime(RUNTIME_KIND_EDITABLE)
        assert is_dev_runtime({"runtime_kind": RUNTIME_KIND_EDITABLE})
        assert is_dev_runtime({"kind": RUNTIME_KIND_EDITABLE})

    def test_find_site_packages_detects_normal_venv_lib_python_site_packages(self, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        sp_dir = venv_dir / "lib" / "python3.12" / "site-packages"
        sp_dir.mkdir(parents=True)
        assert _find_site_packages(fake_python) == sp_dir.resolve()

    def test_find_site_packages_detects_windows_venv_lib_site_packages(self, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "Scripts" / "python.exe"
        fake_python.parent.mkdir(parents=True)
        sp_dir = venv_dir / "Lib" / "site-packages"
        sp_dir.mkdir(parents=True)
        assert _find_site_packages(fake_python) == sp_dir.resolve()

    def test_find_site_packages_prefers_sys_path_site_packages(self, monkeypatch, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        
        sp_venv = venv_dir / "lib" / "python3.12" / "site-packages"
        sp_venv.mkdir(parents=True)
        
        sp_other = tmp_path / "other" / "site-packages"
        sp_other.mkdir(parents=True)
        
        monkeypatch.setattr(sys, "path", [str(sp_other)])
        assert _find_site_packages(fake_python) == sp_other.resolve()

    def test_find_site_packages_existing_artificial_layout_no_longer_required(self, tmp_path):
        sp = tmp_path / "site-packages"
        sp.mkdir()
        parent = tmp_path / "bin" / "python"
        parent.parent.mkdir(parents=True)
        parent.write_text("")
        assert _find_site_packages(parent.resolve()) == sp.resolve()

    def test_direct_url_json_evidence_preserved_for_editable_runtime(self, monkeypatch, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        
        sp_dir = venv_dir / "lib" / "python3.12" / "site-packages"
        dist_info = sp_dir / "oem_knowledge-0.0.0.dist-info"
        dist_info.mkdir(parents=True)
        
        direct_url_file = dist_info / "direct_url.json"
        direct_url_file.write_text(json.dumps({
            "url": "file:///home/xpajonx/.config/openempiric-dev/openempiric",
            "dir_info": {
                "editable": True
            }
        }))
        
        monkeypatch.setattr(sys, "executable", str(fake_python))
        monkeypatch.setattr(sys, "path", [])
        
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_EDITABLE
        any_url_evidence = any("file:///home/xpajonx/.config/openempiric-dev/openempiric" in ev for ev in info["evidence"])
        assert any_url_evidence

    def test_detect_runtime_malformed_direct_url_does_not_crash(self, monkeypatch, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        
        sp_dir = venv_dir / "lib" / "python3.12" / "site-packages"
        dist_info = sp_dir / "oem_knowledge-1.0.4.dist-info"
        dist_info.mkdir(parents=True)
        
        direct_url_file = dist_info / "direct_url.json"
        direct_url_file.write_text("malformed json content }")
        
        monkeypatch.setattr(sys, "executable", str(fake_python))
        monkeypatch.setattr(sys, "path", [])
        
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_UNKNOWN
        any_warning = any("failed to parse" in ev for ev in info["evidence"])
        assert any_warning

    def test_detect_runtime_direct_url_non_editable_not_marked_editable(self, monkeypatch, tmp_path):
        venv_dir = tmp_path / "venv"
        fake_python = venv_dir / "bin" / "python"
        fake_python.parent.mkdir(parents=True)
        
        sp_dir = venv_dir / "lib" / "python3.12" / "site-packages"
        dist_info = sp_dir / "oem_knowledge-1.0.4.dist-info"
        dist_info.mkdir(parents=True)
        
        direct_url_file = dist_info / "direct_url.json"
        direct_url_file.write_text(json.dumps({
            "url": "https://github.com/some/repo",
            "vcs_info": {
                "vcs": "git",
                "commit_id": "1234567890abcdef"
            }
        }))
        
        monkeypatch.setattr(sys, "executable", str(fake_python))
        monkeypatch.setattr(sys, "path", [])
        
        info = detect_runtime()
        assert info["runtime_kind"] == RUNTIME_KIND_UNKNOWN

    def test_find_site_packages_dedupes_preserving_order(self, monkeypatch, tmp_path):
        sp_dir = tmp_path / "unique-site-packages"
        sp_dir.mkdir()
        
        monkeypatch.setattr(sys, "path", [str(sp_dir)])
        
        import site
        if hasattr(site, "getsitepackages"):
            monkeypatch.setattr(site, "getsitepackages", lambda: [str(sp_dir)])
            
        assert _find_site_packages(tmp_path / "bin" / "python") == sp_dir.resolve()

