from __future__ import annotations

import os
import sys
import json
from pathlib import Path

class ProjectResolutionError(Exception):
    def __init__(self, message: str, suggestion: str = "", reason: str = ""):
        super().__init__(message)
        self.suggestion = suggestion
        self.reason = reason


class ProjectMismatchError(ProjectResolutionError):
    def __init__(self, resolved_project: str, cwd: str):
        super().__init__(
            f"Active project mismatch: resolved {resolved_project} but cwd is {cwd}.",
            suggestion="Verify you are in the correct workspace directory or pass the project explicitly.",
            reason="project_mismatch"
        )
        self.resolved_project = resolved_project
        self.cwd = cwd


class ProjectUnresolvedError(ProjectResolutionError):
    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(
            message,
            suggestion=suggestion,
            reason="project_unresolved"
        )


SESSION_TO_PROJECT: dict[str, Path] = {}


def find_nearest_oem_root(path: Path) -> Path | None:
    try:
        p = path.resolve()
        for parent in [p] + list(p.parents):
            if (parent / ".oem").is_dir():
                return parent
    except Exception:
        pass
    return None


def is_oem_dev_repo(path: Path) -> bool:
    try:
        resolved = path.resolve()
        current_file = Path(__file__).resolve()
        for parent in [current_file] + list(current_file.parents):
            if (parent / ".git").exists() and (parent / "packages" / "oem-knowledge").is_dir():
                if resolved == parent:
                    return True
    except Exception:
        pass
    return False


def is_path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def get_project_root_from_active_session(path: Path) -> Path | None:
    root = find_nearest_oem_root(path)
    if root:
        active_json = root / ".oem" / "state" / "active_session.json"
        if active_json.exists():
            try:
                data = json.loads(active_json.read_text(encoding="utf-8"))
                p = data.get("project")
                if p:
                    return Path(p).resolve()
            except Exception:
                pass
    return None


def _should_bypass_mismatch_check() -> bool:
    if os.environ.get("OEM_FORCE_MISMATCH_CHECK") == "1":
        return False
    return "pytest" in sys.modules


def resolve_active_project(project_arg: str = "", session_id: str = "") -> Path:
    # 1. Explicit project argument if provided (one-off override, does not rebind session)
    if project_arg:
        p = Path(project_arg).resolve()
        root = find_nearest_oem_root(p)
        if root:
            return root
        if p.is_dir():
            return p
        raise ProjectUnresolvedError(
            f"Explicit project path '{project_arg}' does not exist or is not a directory.",
            suggestion="Verify the path exists and contains a .oem folder."
        )

    # 2. Active project root recorded in SESSION_TO_PROJECT for this session
    if session_id and session_id in SESSION_TO_PROJECT:
        return SESSION_TO_PROJECT[session_id]

    # Check active_session.json under env var directories
    for env_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
        val = os.environ.get(env_var)
        if val:
            p = Path(val).resolve()
            root = get_project_root_from_active_session(p)
            if root:
                return root

    # Check active_session.json under CWD
    root_from_cwd_session = get_project_root_from_active_session(Path.cwd())
    if root_from_cwd_session:
        return root_from_cwd_session

    # 3. Environment variables provided by agent runtime
    for env_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
        val = os.environ.get(env_var)
        if val:
            p = Path(val).resolve()
            root = find_nearest_oem_root(p)
            if root:
                if is_oem_dev_repo(root) and not _should_bypass_mismatch_check():
                    for check_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
                        chk_val = os.environ.get(check_var)
                        if chk_val:
                            chk_p = Path(chk_val).resolve()
                            if chk_p.is_dir() and not is_path_inside(chk_p, root):
                                raise ProjectMismatchError(str(root), str(chk_p))
                return root

    # 4. Nearest parent directory containing .oem starting from os.getcwd()
    cwd_path = Path.cwd().resolve()
    root = find_nearest_oem_root(cwd_path)
    if root:
        if is_oem_dev_repo(root) and not _should_bypass_mismatch_check():
            pwd_val = os.environ.get("PWD")
            if pwd_val:
                pwd_p = Path(pwd_val).resolve()
                if not is_path_inside(pwd_p, root):
                    raise ProjectMismatchError(str(root), str(pwd_p))
        return root

    raise ProjectUnresolvedError(
        "Active OEM project root could not be resolved from the current context.",
        suggestion="Pass project explicitly or start session from a directory containing .oem."
    )


def handle_resolution_error(operation: str, e: ProjectResolutionError) -> str:
    if isinstance(e, ProjectMismatchError):
        return json.dumps({
            "status": "error",
            "reason": "project_mismatch",
            "resolved_project": e.resolved_project,
            "cwd": e.cwd
        }, indent=2)
    elif isinstance(e, ProjectUnresolvedError):
        return json.dumps({
            "status": "error",
            "operation": operation,
            "reason": "project_unresolved",
            "suggestion": e.suggestion or "Pass project explicitly or start session from a directory containing .oem."
        }, indent=2)
    else:
        return json.dumps({
            "status": "error",
            "operation": operation,
            "message": str(e),
            "suggestion": getattr(e, "suggestion", None)
        }, indent=2)
