from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        release = Path("/proc/sys/kernel/osrelease")
        return release.exists() and "microsoft" in release.read_text(encoding="utf-8").lower()
    except Exception:
        return False


def list_wsl_distros() -> list[str]:
    distros: list[str] = []
    try:
        result = subprocess.run(
            ["wsl.exe", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and not line.startswith("Windows") and not line.startswith("Legacy"):
                    distro = re.sub(r"\s*\(Default\)\s*$", "", line).strip()
                    distros.append(distro)
        return distros
    except FileNotFoundError:
        return distros
    except Exception as e:
        logger.debug("Failed to list WSL distros: %s", e)
        return distros


def detect_default_wsl_distro() -> str | None:
    env_distro = os.environ.get("WSL_DISTRO_NAME")
    if env_distro:
        return env_distro

    distros = list_wsl_distros()
    if not distros:
        return None
    if len(distros) == 1:
        return distros[0]

    try:
        result = subprocess.run(
            ["wsl.exe", "--set-default-version", "--status"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                match = re.search(r"Default Distribution:\s*(\S+)", line)
                if match:
                    distro = match.group(1).strip()
                    if distro in distros:
                        return distro
    except Exception:
        pass

    return None


def _normalize_unc(raw: str) -> str:
    if raw.startswith("//"):
        raw = "\\\\" + raw[2:]
    raw = raw.replace("/", "\\")
    return raw


def _wsl_unc_server() -> str:
    return r"wsl\$|wsl\.localhost"


def distro_from_unc_path(path: Path) -> str | None:
    raw = _normalize_unc(str(path))
    match = re.match(r"^\\\\(?:" + _wsl_unc_server() + r")\\([^\\]+)\\", raw, flags=re.IGNORECASE)
    return match.group(1).lower() if match else None


def wsl_path_from_unc(path: Path) -> str | None:
    raw = _normalize_unc(str(path))
    match = re.match(r"^\\\\(?:" + _wsl_unc_server() + r")\\([^\\]+)\\(.+)$", raw, flags=re.IGNORECASE)
    if not match:
        return None
    return "/" + match.group(2).replace("\\", "/")


def unc_path_from_wsl(wsl_path: str, distro: str = "Ubuntu") -> str:
    clean_path = wsl_path.replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}\\{clean_path.lstrip('\\')}"


def windows_to_wsl_path(windows_path: str, distro: str | None = None) -> str | None:
    if not re.match(r"^[A-Za-z]:[\\/]", windows_path):
        return windows_path.replace("\\", "/")
    try:
        proc = subprocess.run(
            ["wslpath", "-u", windows_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except FileNotFoundError:
        detected = _wslpath_fallback(windows_path, distro)
        if detected:
            return detected
    except Exception as e:
        logger.debug("Failed to run wslpath: %s", e)
    return None


def wsl_to_windows_path(wsl_path: str, distro: str | None = None) -> str | None:
    try:
        proc = subprocess.run(
            ["wslpath", "-w", wsl_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug("Failed to run wslpath -w: %s", e)
    return None


def _wslpath_fallback(windows_path: str, distro: str | None = None) -> str | None:
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", windows_path)
    if not match:
        return None
    drive = match.group(1).lower()
    rest = match.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{rest}"


def command_exists_in_wsl(command: str, distro: str | None = None) -> bool:
    if not is_wsl() and sys.platform == "win32":
        if not shutil.which("wsl.exe"):
            return False
        distro = distro or detect_default_wsl_distro()
        if not distro:
            return False
        try:
            result = subprocess.run(
                ["wsl.exe", "-d", distro, "bash", "-lc", f"command -v {command}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    if is_wsl():
        return shutil.which(command) is not None

    return shutil.which(command) is not None


def get_wsl_exe_path() -> str:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        return str(Path(windir) / "System32" / "wsl.exe")

    if is_wsl():
        detected = _detect_windows_env("WINDIR") or _detect_windows_env("SystemRoot")
        if detected:
            return detected.rstrip("\\/") + "\\System32\\wsl.exe"

    return "C:\\Windows\\System32\\wsl.exe"


def _detect_windows_env(name: str) -> str | None:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return None
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", f"$env:{name}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        value = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else ""
        return value or None
    except Exception as e:
        logger.debug("Failed to detect Windows environment variable %s: %s", name, e)
        return None


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
