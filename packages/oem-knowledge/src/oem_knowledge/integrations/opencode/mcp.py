from __future__ import annotations

import enum
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from oem_knowledge.platform.environment import (
    HostOS,
    ProjectEnv,
    classify_project_environment,
    detect_host,
    find_nearest_oem_root_on_platform,
)
from oem_knowledge.platform.wsl import (
    command_exists_in_wsl,
    detect_default_wsl_distro,
    get_wsl_exe_path,
    list_wsl_distros,
    shell_quote,
    wsl_path_from_unc,
)

logger = logging.getLogger(__name__)


class OpenCodeMCPMode(enum.Enum):
    WINDOWS_NATIVE = "windows_native"
    WSL_BRIDGE = "wsl_bridge"
    LINUX_DIRECT = "linux_direct"
    BLOCKED = "blocked"


def build_opencode_mcp_command(
    project_root: str | Path,
    mode: OpenCodeMCPMode | None = None,
    wsl_distro: str | None = None,
    is_dev_workspace: bool = False,
) -> dict[str, Any] | None:
    project_root = Path(project_root).resolve()

    if mode is None:
        recommendation = recommend_opencode_mcp_mode(project_root, wsl_distro)
        mode = recommendation["mode"]
        if mode == OpenCodeMCPMode.BLOCKED:
            return None
        wsl_distro = recommendation.get("wsl_distro")

    if mode == OpenCodeMCPMode.WINDOWS_NATIVE:
        return _build_native_command(project_root, is_dev_workspace)

    if mode == OpenCodeMCPMode.LINUX_DIRECT:
        return _build_native_command(project_root, is_dev_workspace)

    if mode == OpenCodeMCPMode.WSL_BRIDGE:
        return _build_wsl_bridge_command(project_root, wsl_distro, is_dev_workspace)

    return None


def recommend_opencode_mcp_mode(
    project_root: str | Path | None = None,
    wsl_distro: str | None = None,
) -> dict:
    host = detect_host()
    result: dict = {
        "mode": OpenCodeMCPMode.BLOCKED,
        "reason": "",
        "wsl_distro": None,
        "details": {},
    }

    oem_paths: dict[str, str | None] = {}

    if host == HostOS.WINDOWS:
        oem_paths["windows"] = shutil.which("oem")
        distros = list_wsl_distros()
        if not distros:
            result["reason"] = "no_wsl_distros"
            result["details"] = {"host": "windows", "wsl_available": False}
            return result

        if wsl_distro:
            if wsl_distro not in distros:
                result["reason"] = "unknown_distro"
                result["details"] = {"host": "windows", "requested_distro": wsl_distro, "available_distros": distros}
                return result
        else:
            if len(distros) > 1:
                result["reason"] = "multiple_wsl_distros"
                result["details"] = {"host": "windows", "available_distros": distros}
                return result
            wsl_distro = distros[0]

        oem_in_wsl = command_exists_in_wsl("oem", wsl_distro)
        oem_paths["wsl"] = f"Ubuntu {wsl_distro}" if oem_in_wsl else None

        windows_oem = oem_paths["windows"] is not None
        wsl_oem = oem_in_wsl

        if windows_oem and not wsl_oem:
            result["mode"] = OpenCodeMCPMode.WINDOWS_NATIVE
            result["reason"] = "windows_oem_available"
        elif wsl_oem and not windows_oem:
            result["mode"] = OpenCodeMCPMode.WSL_BRIDGE
            result["reason"] = "oem_only_in_wsl"
            result["wsl_distro"] = wsl_distro
        elif windows_oem and wsl_oem:
            if project_root:
                env = classify_project_environment(project_root)
                if env in (ProjectEnv.WSL_NATIVE, ProjectEnv.UNC_WSL):
                    result["mode"] = OpenCodeMCPMode.WSL_BRIDGE
                    result["reason"] = "project_in_wsl"
                else:
                    result["mode"] = OpenCodeMCPMode.WINDOWS_NATIVE
                    result["reason"] = "windows_oem_available"
                result["wsl_distro"] = wsl_distro
            else:
                result["mode"] = OpenCodeMCPMode.WSL_BRIDGE
                result["reason"] = "windows_oem_and_wsl_oem_both_available"
                result["wsl_distro"] = wsl_distro
        else:
            result["reason"] = "no_oem_cli"
            result["details"] = {"host": "windows", "oem_in_wsl": False, "oem_in_windows": False}

        result["details"]["oem_paths"] = oem_paths
        return result

    if host == HostOS.WSL:
        oem_paths["wsl_local"] = shutil.which("oem")
        if oem_paths["wsl_local"] is not None:
            result["mode"] = OpenCodeMCPMode.LINUX_DIRECT
            result["reason"] = "wsl_native_oem"
            result["details"] = {"host": "wsl", "oem_paths": oem_paths}
            return result

        distro = wsl_distro or detect_default_wsl_distro()
        if distro and command_exists_in_wsl("oem", distro):
            result["mode"] = OpenCodeMCPMode.WSL_BRIDGE
            result["reason"] = "oem_only_in_windows_wsl"
            result["wsl_distro"] = distro
            result["details"] = {"host": "wsl", "oem_paths": oem_paths, "oem_in_windows_wsl": True}
            return result

        result["reason"] = "no_oem_cli"
        result["details"] = {"host": "wsl", "oem_paths": oem_paths}
        return result

    oem_paths["local"] = shutil.which("oem")
    if host == HostOS.LINUX:
        if oem_paths["local"] is not None:
            result["mode"] = OpenCodeMCPMode.LINUX_DIRECT
            result["reason"] = "linux_oem_available"
        else:
            result["reason"] = "no_oem_cli"
    elif host == HostOS.MACOS:
        if oem_paths["local"] is not None:
            result["mode"] = OpenCodeMCPMode.LINUX_DIRECT
            result["reason"] = "macos_oem_available"
        else:
            result["reason"] = "no_oem_cli"
    else:
        result["reason"] = "unknown_host"

    result["details"] = {"host": host.value, "oem_paths": oem_paths}
    return result


def detect_possible_split_memory(project_root: str | Path) -> dict:
    project_root = Path(project_root).resolve()
    host = detect_host()
    warnings: list[str] = []
    windows_oem: str | None = None
    wsl_oem: str | None = None

    raw = str(project_root)

    if host in (HostOS.WINDOWS, HostOS.WSL):
        env = classify_project_environment(project_root)

        if env == ProjectEnv.WSL_NATIVE:
            windows_path_str = str(project_root).replace("/", "\\")
            if re.match(r"^[A-Za-z]:", windows_path_str):
                win_path = windows_path_str
            else:
                from oem_knowledge.platform.wsl import unc_path_from_wsl
                distro = detect_default_wsl_distro() or "Ubuntu"
                win_path = unc_path_from_wsl(str(project_root), distro)
            windows_oem_root = find_nearest_oem_root_on_platform(Path(win_path))
            if windows_oem_root:
                wsl_oem = str(project_root)
                windows_oem = str(windows_oem_root)
                warnings.append("Project has .oem in both WSL and Windows paths")

        elif env == ProjectEnv.WINDOWS_NATIVE:
            if is_wsl():
                wsl_path = str(project_root).replace("\\", "/")
                match = re.match(r"^([A-Za-z]):/(.*)$", wsl_path)
                if match:
                    alt = Path(f"/mnt/{match.group(1).lower()}/{match.group(2)}")
                    wsl_oem_root = find_nearest_oem_root_on_platform(alt)
                    if wsl_oem_root:
                        windows_oem = str(project_root)
                        wsl_oem = str(wsl_oem_root)
                        warnings.append("Project has .oem in both Windows and WSL paths")

        elif env == ProjectEnv.MOUNTED_WINDOWS:
            match = re.match(r"^/mnt/([a-z])/(.*)$", str(project_root), re.IGNORECASE)
            if match:
                win_path = f"{match.group(1).upper()}:\\{match.group(2).replace('/', '\\')}"
                windows_oem_root = find_nearest_oem_root_on_platform(Path(win_path))
                if windows_oem_root:
                    wsl_oem = str(project_root)
                    windows_oem = str(windows_oem_root)
                    warnings.append("Project has .oem in both mounted Windows and WSL-native paths")

    return {
        "split_detected": len(warnings) > 0,
        "warnings": warnings,
        "windows_oem_path": windows_oem,
        "wsl_oem_path": wsl_oem,
    }


def _build_native_command(
    project_root: Path,
    is_dev_workspace: bool,
) -> dict[str, Any]:
    if is_dev_workspace:
        return {
            "command": "uv",
            "args": [
                "run",
                "--directory",
                str(project_root),
                "python",
                "-m",
                "oem_knowledge.server",
            ],
            "enabled": True,
            "timeout": 60000,
        }

    resolved_oem = shutil.which("oem")
    return {
        "command": str(Path(resolved_oem).resolve()) if resolved_oem else "oem",
        "args": ["mcp"],
        "enabled": True,
        "timeout": 60000,
    }


def _build_wsl_bridge_command(
    project_root: Path,
    wsl_distro: str | None,
    is_dev_workspace: bool,
) -> dict[str, Any] | None:
    distro = wsl_distro or detect_default_wsl_distro()
    if not distro:
        return None

    wsl_project_root = _resolve_wsl_project_path(project_root, distro)

    if is_dev_workspace:
        bash_cmd = f"exec uv run --directory {shell_quote(wsl_project_root)} python -m oem_knowledge.server"
    else:
        bash_cmd = "exec oem mcp"

    return {
        "command": "wsl.exe",
        "args": [
            "-d",
            distro,
            "--cd",
            wsl_project_root,
            "bash",
            "-lc",
            bash_cmd,
        ],
        "enabled": True,
        "timeout": 120000,
    }


def _resolve_wsl_project_path(project_root: Path, distro: str) -> str:
    raw = str(project_root.resolve())

    unc = wsl_path_from_unc(project_root)
    if unc:
        return unc

    from oem_knowledge.platform.paths import (
        is_mounted_windows_path,
        is_windows_path,
        normalize_to_wsl_path,
    )

    wsl = normalize_to_wsl_path(raw, distro)
    if wsl:
        return wsl

    match = re.match(r"^([A-Za-z]):/(.*)$", raw.replace("\\", "/"))
    if match:
        return f"/mnt/{match.group(1).lower()}/{match.group(2)}"

    return raw.replace("\\", "/")



