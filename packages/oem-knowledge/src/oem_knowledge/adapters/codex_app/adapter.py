from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter


CODEX_SKILL_CONTENT = """---
name: openempiric
description: Use OpenEmpiric project memory through the OEM MCP tools when working in a repository with OEM enabled.
---

# OpenEmpiric

OpenEmpiric is the persistent project memory layer for this workspace.

- Prefer OEM MCP tools such as `knowledge_search` when project history, decisions, prior failures, or active concepts may matter.
- Do not run shell-based OEM commands when an equivalent MCP tool is available.
- Treat OEM memory as supporting context; project files and explicit user instructions remain authoritative.
- Report referenced memory concepts at session end using the OEM usage-reporting tool when available.
- Do not manually call internal lifecycle commands unless the user explicitly asks.
"""


@register_adapter("codex-app")
@register_adapter("codex")
class CodexAppAdapter(BaseAdapter):
    """Codex App adapter that bridges Windows Codex to an OEM install inside WSL."""

    def get_wsl_distro(self) -> str:
        return os.environ.get("WSL_DISTRO_NAME") or self._distro_from_unc_path(Path.cwd()) or "Ubuntu"

    def get_codex_home(self) -> Path:
        raw = os.environ.get("OEM_CODEX_HOME") or os.environ.get("CODEX_HOME")
        if not raw and sys.platform != "win32":
            raw = self._detect_windows_codex_home_from_wsl()
            if not raw and self._is_wsl():
                raise RuntimeError(
                    "Could not automatically detect your Windows Codex home directory from WSL.\n"
                    "Please configure it manually by setting the OEM_CODEX_HOME environment variable.\n"
                    "Example: export OEM_CODEX_HOME=\"/mnt/c/Users/YourUsername/.codex\""
                )
        if not raw:
            raw = (str(Path(os.environ["USERPROFILE"]) / ".codex") if os.environ.get("USERPROFILE") else "")
        if not raw:
            raw = str(Path.home() / ".codex")
        return self._windows_path_for_current_runtime(raw)

    def get_skill_path(self) -> Path:
        return self.get_codex_home() / "skills" / "openempiric" / "SKILL.md"

    def get_config_path(self) -> Path:
        return self.get_codex_home() / "config.toml"

    def get_wsl_project_dir(self) -> str:
        override = os.environ.get("OEM_CODEX_WSL_PROJECT_DIR")
        if override:
            return override

        raw_path = Path(self.project_path or Path.cwd())
        unc_path = self._wsl_path_from_unc(raw_path)
        if unc_path:
            return unc_path

        if sys.platform == "win32":
            converted = self._run_wslpath(str(raw_path.resolve()))
            if converted:
                return converted

        return str(raw_path.resolve()).replace("\\", "/")

    def build_mcp_config(self) -> dict[str, Any]:
        project_dir = self.get_wsl_project_dir()
        distro = self.get_wsl_distro()

        # Check if we are running in a dev workspace
        is_dev = False
        workspace_root = Path(self.project_path or Path.cwd()).resolve()
        while workspace_root.parent != workspace_root:
            pyproject_path = workspace_root / "pyproject.toml"
            if pyproject_path.exists():
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if 'name = "oem-mcp"' in content:
                        is_dev = True
                        break
                except Exception:
                    pass
            workspace_root = workspace_root.parent

        if is_dev:
            # Dev workspace: run local package server
            bash_cmd = f"exec uv run --directory {project_dir} python -m oem_knowledge.server"
        else:
            # Global install: run oem mcp
            bash_cmd = "exec oem mcp"

        return {
            "command": self.get_windows_wsl_exe(),
            "args": [
                "-d",
                distro,
                "--cd",
                project_dir,
                "bash",
                "-lc",
                bash_cmd,
            ],
            "startup_timeout_sec": 120,
        }

    def build_bridge_check_command(self) -> list[str]:
        project_dir = self.get_wsl_project_dir()
        distro = self.get_wsl_distro()
        check = (
            f"test -d {self._shell_quote(project_dir)} "
            "&& command -v oem >/dev/null "
            "&& oem --version >/dev/null"
        )
        return [self.get_probe_wsl_exe(), "-d", distro, "--cd", project_dir, "bash", "-lc", check]

    def get_windows_wsl_exe(self) -> str:
        override = os.environ.get("OEM_CODEX_WSL_EXE")
        if override:
            return override

        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        if windir:
            return str(Path(windir) / "System32" / "wsl.exe")

        if self._is_wsl():
            detected = self._detect_windows_env("WINDIR") or self._detect_windows_env("SystemRoot")
            if detected:
                return detected.rstrip("\\/") + "\\System32\\wsl.exe"

        return "C:\\Windows\\System32\\wsl.exe"

    def get_probe_wsl_exe(self) -> str:
        if sys.platform == "win32":
            return self.get_windows_wsl_exe()
        return "wsl.exe"

    def setup(self, repair: bool = False) -> dict[str, Any]:
        codex_home = self.get_codex_home()
        skill_path = self.get_skill_path()
        config_path = self.get_config_path()

        codex_home.mkdir(parents=True, exist_ok=True)
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        if repair or not skill_path.exists() or skill_path.read_text(encoding="utf-8") != CODEX_SKILL_CONTENT:
            skill_path.write_text(CODEX_SKILL_CONTENT, encoding="utf-8")

        config_path.parent.mkdir(parents=True, exist_ok=True)
        original = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        updated = self._upsert_mcp_block(original, self.build_mcp_config())
        if repair or updated != original:
            if config_path.exists():
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_path = config_path.with_name(f"config.toml.backup-{timestamp}")
                backup_path.write_text(original, encoding="utf-8")
            config_path.write_text(updated, encoding="utf-8")

        healthy, message = self.verify_health(probe_bridge=False)
        return {
            "codex_home": str(codex_home),
            "skill_path": str(skill_path),
            "config_path": str(config_path),
            "mcp_config": self.build_mcp_config(),
            "healthy": healthy,
            "message": message,
        }

    def install_skill(self) -> bool:
        try:
            skill_path = self.get_skill_path()
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(CODEX_SKILL_CONTENT, encoding="utf-8")
            return True
        except Exception:
            return False

    def verify_mcp(self) -> bool:
        try:
            config = self._load_config()
            server = config.get("mcp_servers", {}).get("openempiric", {})
            expected = self.build_mcp_config()
            return (
                server.get("command") == expected["command"]
                and server.get("args") == expected["args"]
            )
        except Exception:
            return False

    def verify_health(self, probe_bridge: bool = True) -> tuple[bool, str]:
        codex_home = self.get_codex_home()
        if not codex_home.exists():
            return False, f"Codex home not found: {codex_home}"

        skill_path = self.get_skill_path()
        if not skill_path.exists():
            return False, f"Codex OpenEmpiric skill not found: {skill_path}"

        config_path = self.get_config_path()
        if not config_path.exists():
            return False, f"Codex config.toml not found: {config_path}"

        try:
            self._load_config()
        except Exception as e:
            return False, f"Codex config.toml is not parseable: {e}"

        if not self.verify_mcp():
            return False, "Codex MCP server 'openempiric' is not registered with the expected WSL bridge"

        if probe_bridge:
            ok, msg = self.verify_bridge()
            if not ok:
                return False, msg

        return True, "Codex App WSL bridge healthy"

    def verify_bridge(self) -> tuple[bool, str]:
        if shutil.which("wsl.exe") is None:
            return False, "wsl.exe not found on PATH"

        try:
            proc = subprocess.run(
                self.build_bridge_check_command(),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except Exception as e:
            return False, f"WSL bridge check failed: {e}"

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            detail = f": {err}" if err else ""
            return False, f"WSL bridge check failed{detail}"

        return True, "WSL bridge reachable"

    def discover_latest_transcript(self) -> Optional[Path]:
        return None

    def _load_config(self) -> dict[str, Any]:
        import tomllib

        return tomllib.loads(self.get_config_path().read_text(encoding="utf-8"))

    def _upsert_mcp_block(self, text: str, mcp_config: dict[str, Any]) -> str:
        block = self._render_mcp_block(mcp_config)
        pattern = re.compile(r"(?ms)^\[mcp_servers\.openempiric\]\s*\n.*?(?=^\[|\Z)")
        stripped = text.rstrip()
        if pattern.search(text):
            return pattern.sub(lambda _match: block, text).rstrip() + "\n"
        if not stripped:
            return block
        return stripped + "\n\n" + block

    def _render_mcp_block(self, mcp_config: dict[str, Any]) -> str:
        return (
            "[mcp_servers.openempiric]\n"
            f"command = {json.dumps(mcp_config['command'])}\n"
            f"args = {json.dumps(mcp_config['args'])}\n"
            f"startup_timeout_sec = {int(mcp_config['startup_timeout_sec'])}\n"
        )

    def _windows_path_for_current_runtime(self, raw: str) -> Path:
        if sys.platform != "win32" and re.match(r"^[A-Za-z]:[\\/]", raw):
            converted = self._run_wslpath(raw)
            if converted:
                return Path(converted)
        return Path(raw).expanduser()

    def _run_wslpath(self, raw: str) -> str | None:
        try:
            proc = subprocess.run(
                ["wslpath", "-u", raw],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except Exception:
            return None
        return None

    def _detect_windows_codex_home_from_wsl(self) -> str | None:
        if not self._is_wsl():
            return None
        windows_profile = self._detect_windows_env("USERPROFILE")
        if not windows_profile:
            return None
        return windows_profile.rstrip("\\/") + "\\.codex"

    def _detect_windows_env(self, name: str) -> str | None:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            return None
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", f"$env:{name}"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            if proc.returncode != 0:
                return None
            value = proc.stdout.strip().splitlines()[0].strip() if proc.stdout.strip() else ""
            return value or None
        except Exception:
            return None

    def _is_wsl(self) -> bool:
        if os.environ.get("WSL_DISTRO_NAME"):
            return True
        try:
            release = Path("/proc/sys/kernel/osrelease")
            return release.exists() and "microsoft" in release.read_text(encoding="utf-8").lower()
        except Exception:
            return False

    def _distro_from_unc_path(self, path: Path) -> str | None:
        raw = str(path)
        match = re.match(r"^\\\\wsl(?:\.localhost)?\\([^\\]+)\\", raw, flags=re.IGNORECASE)
        return match.group(1) if match else None

    def _wsl_path_from_unc(self, path: Path) -> str | None:
        raw = str(path)
        match = re.match(r"^\\\\wsl(?:\.localhost)?\\([^\\]+)\\(.+)$", raw, flags=re.IGNORECASE)
        if not match:
            return None
        return "/" + match.group(2).replace("\\", "/")

    def _shell_quote(self, value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"
