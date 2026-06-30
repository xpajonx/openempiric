from __future__ import annotations

import json
import sys
from pathlib import Path


RUNTIME_KIND_UV_TOOL = "uv_tool"
RUNTIME_KIND_REPO_VENV = "repo_venv"
RUNTIME_KIND_EDITABLE = "editable_checkout"
RUNTIME_KIND_UNKNOWN = "unknown"


def detect_runtime() -> dict:
    exec_path = Path(sys.executable).resolve()
    try:
        import oem_knowledge
        pkg_path = Path(oem_knowledge.__file__).resolve()
    except Exception:
        pkg_path = None

    kind = RUNTIME_KIND_UNKNOWN
    evidence: list[str] = []

    # Check if running from uv tool install
    uv_tool_marker = exec_path.parent.parent / "uv"
    site_packages = _find_site_packages(exec_path)
    dist_info = None
    if site_packages:
        dist_info = _find_dist_info(site_packages, "oem_knowledge")

    if uv_tool_marker.is_file() or (exec_path.parent.parent / ".." / "uv").resolve().is_file():
        kind = RUNTIME_KIND_UV_TOOL
        evidence.append(f"executable under uv tool tree: {exec_path}")

    # Check if running from a repo .venv
    if _is_under_repo_venv(exec_path):
        kind = RUNTIME_KIND_REPO_VENV
        evidence.append(f"executable under repo .venv: {exec_path}")

    # Check if package is an editable install
    if dist_info:
        direct_url = dist_info / "direct_url.json"
        if direct_url.exists():
            try:
                data = json.loads(direct_url.read_text(encoding="utf-8"))
                url_info = data.get("vcs_info", {})
                evidence.append(
                    f"installed from {data.get('url')} "
                    f"@{url_info.get('requested_revision', '?')} "
                    f"(commit {url_info.get('commit_id', '?')[:12]})"
                )
            except Exception:
                pass

    try:
        version = _get_package_version("oem-knowledge")
    except Exception:
        version = None

    return {
        "runtime_kind": kind,
        "executable_path": str(exec_path),
        "package_path": str(pkg_path) if pkg_path else None,
        "version": version,
        "evidence": evidence,
    }


def is_dev_runtime() -> bool:
    info = detect_runtime()
    return info["runtime_kind"] in (RUNTIME_KIND_REPO_VENV, RUNTIME_KIND_EDITABLE)


def _find_site_packages(exec_path: Path) -> Path | None:
    for parent in [exec_path.parent] + list(exec_path.parent.parents):
        candidate = parent / "site-packages"
        if candidate.is_dir():
            return candidate
    return None


def _find_dist_info(site_packages: Path, name: str) -> Path | None:
    for p in site_packages.iterdir():
        if p.name.startswith(name) and p.name.endswith(".dist-info"):
            return p
    return None


def _is_under_repo_venv(exec_path: Path) -> bool:
    for parent in exec_path.parents:
        if parent.name == ".venv":
            sibling_pyproject = parent.parent / "pyproject.toml"
            sibling_git = parent.parent / ".git"
            if sibling_pyproject.is_file() or sibling_git.is_dir():
                return True
    return False


def _get_package_version(name: str) -> str | None:
    try:
        from importlib.metadata import version
        return version(name)
    except Exception:
        return None
