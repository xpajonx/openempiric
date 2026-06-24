from __future__ import annotations

import re
from pathlib import Path

from oem_knowledge.platform.wsl import windows_to_wsl_path as _w2w
from oem_knowledge.platform.wsl import wsl_to_windows_path as _w2win
from oem_knowledge.platform.wsl import wsl_path_from_unc
from oem_knowledge.platform.wsl import unc_path_from_wsl


def is_unc_path(path: str | Path) -> bool:
    raw = str(path)
    return raw.startswith("\\\\") or raw.startswith("//")


def is_windows_path(path: str | Path) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(path)))


def is_mounted_windows_path(path: str | Path) -> bool:
    raw = str(path)
    return bool(re.match(r"^/mnt/[a-z]/", raw, re.IGNORECASE))


def normalize_to_wsl_path(
    path: str | Path,
    distro: str | None = None,
) -> str | None:
    raw = str(path)

    if is_unc_path(raw):
        wsl = wsl_path_from_unc(Path(raw))
        if wsl:
            return wsl

    if is_windows_path(raw):
        return _w2w(raw, distro)

    clean = raw.replace("\\", "/")
    return clean


def normalize_to_windows_path(
    path: str | Path,
    distro: str | None = None,
) -> str | None:
    raw = str(path)

    if is_windows_path(raw):
        return raw

    if is_unc_path(raw):
        return raw.replace("/", "\\")

    from oem_knowledge.platform.wsl import wsl_to_windows_path as _wsl_to_win

    win = _wsl_to_win(raw, distro)
    if win:
        return win

    if is_mounted_windows_path(raw):
        match = re.match(r"^/mnt/([a-z])/(.*)$", raw, re.IGNORECASE)
        if match:
            drive = match.group(1).upper()
            rest = match.group(2).replace("/", "\\")
            return f"{drive}:\\{rest}"

    return raw.replace("/", "\\")
