from __future__ import annotations

import enum
import os
import re
import shutil
import sys
from pathlib import Path

from oem_knowledge.platform.wsl import is_wsl, distro_from_unc_path, wsl_path_from_unc
from oem_knowledge.platform.wsl import list_wsl_distros


class HostOS(enum.Enum):
    WINDOWS = "windows"
    WSL = "wsl"
    LINUX = "linux"
    MACOS = "macos"
    UNKNOWN = "unknown"


class ProjectEnv(enum.Enum):
    WINDOWS_NATIVE = "windows_native"
    WSL_NATIVE = "wsl_native"
    MOUNTED_WINDOWS = "mounted_windows"
    UNC_WSL = "unc_wsl"
    UNKNOWN = "unknown"


def detect_host() -> HostOS:
    if sys.platform == "win32":
        return HostOS.WINDOWS
    if is_wsl():
        return HostOS.WSL
    if sys.platform == "linux":
        return HostOS.LINUX
    if sys.platform == "darwin":
        return HostOS.MACOS
    return HostOS.UNKNOWN


def classify_project_environment(project_root: str | Path) -> ProjectEnv:
    root = Path(project_root)
    raw = str(root)

    if re.match(r"^[A-Za-z]:[\\/]", raw):
        from oem_knowledge.platform.wsl import is_wsl as _is_wsl
        if _is_wsl():
            return ProjectEnv.MOUNTED_WINDOWS
        return ProjectEnv.WINDOWS_NATIVE

    if raw.startswith("\\\\wsl") or raw.startswith("//wsl"):
        return ProjectEnv.UNC_WSL

    if raw.startswith("/mnt/") and len(raw) > 5 and raw[5].isalpha():
        return ProjectEnv.MOUNTED_WINDOWS

    if not raw.startswith("/"):
        return ProjectEnv.UNKNOWN

    if is_wsl():
        return ProjectEnv.WSL_NATIVE

    return ProjectEnv.UNKNOWN


def find_nearest_oem_root_on_platform(
    project_root: str | Path,
) -> Path | None:
    root = Path(project_root).resolve()
    for parent in [root] + list(root.parents):
        if (parent / ".oem").is_dir():
            return parent
    return None


def detect_project_environment_summary(project_root: str | Path | None = None) -> dict:
    host = detect_host()
    oem_in_windows_path = shutil.which("oem")
    oem_in_wsl = False
    wsl_distros: list[str] = []
    default_wsl_distro: str | None = None

    from oem_knowledge.platform.wsl import command_exists_in_wsl as _cmd_in_wsl
    from oem_knowledge.platform.wsl import list_wsl_distros as _list_distros
    from oem_knowledge.platform.wsl import detect_default_wsl_distro as _default_distro

    if host in (HostOS.WINDOWS, HostOS.WSL):
        wsl_distros = _list_distros()
        default_wsl_distro = _default_distro()
        oem_in_wsl = _cmd_in_wsl("oem", default_wsl_distro)

    project_env = None
    memory_root = None
    dual_memory_warning = False
    if project_root:
        project_env = classify_project_environment(project_root)
        memory_root = find_nearest_oem_root_on_platform(project_root)

        if host in (HostOS.WINDOWS, HostOS.WSL):
            wsl_project = str(project_root).replace("\\", "/")
            if not wsl_project.startswith("/mnt/") and not wsl_project.startswith("\\\\wsl"):
                alt_path = Path(f"/mnt/{wsl_project[0].lower()}/{wsl_project[3:]}" if re.match(r"^[A-Z]:", str(project_root)) else str(project_root))
            else:
                alt_path = None
            if alt_path and alt_path != Path(project_root):
                alt_oem = find_nearest_oem_root_on_platform(alt_path)
                if alt_oem and alt_oem != memory_root:
                    dual_memory_warning = True

        if memory_root and (memory_root / ".oem").is_dir():
            memory_root = memory_root / ".oem"

    return {
        "host": host.value,
        "oem_in_windows_path": oem_in_windows_path is not None,
        "oem_in_wsl": oem_in_wsl,
        "wsl_distros": wsl_distros,
        "default_wsl_distro": default_wsl_distro,
        "project_env": project_env.value if project_env else None,
        "memory_root": str(memory_root) if memory_root else None,
        "dual_memory_warning": dual_memory_warning,
    }
