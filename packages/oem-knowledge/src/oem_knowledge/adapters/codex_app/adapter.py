from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter
from oem_knowledge.platform.wsl import (
    is_wsl,
    detect_default_wsl_distro,
    wsl_path_from_unc,
    distro_from_unc_path,
    windows_to_wsl_path,
    get_wsl_exe_path,
    _detect_windows_env,
    shell_quote,
)

logger = logging.getLogger(__name__)


CODEX_SKILL_CONTENT = """---
name: openempiric
description: Use OpenEmpiric project memory through the OEM MCP tools when working in a repository with OEM enabled.
---

# OpenEmpiric

OpenEmpiric is the persistent project memory layer for this workspace.

- Before planning non-trivial tasks, call `knowledge_preflight` with the user task.
- If preflight returns `required`, follow the returned OEM context before planning.
- If preflight returns `suggest`, consider the returned context and optionally use `knowledge_search` or `knowledge_source_search`.
- Prefer OEM MCP tools such as `knowledge_search` when project history, decisions, prior failures, or active concepts may matter.
- Do not use `knowledge_index` as a fallback for failed reflection.
- Do not treat the source corpus as learned memory.
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
        env_distro = os.environ.get("WSL_DISTRO_NAME")
        if env_distro:
            return env_distro
        unc_distro = distro_from_unc_path(Path.cwd())
        if unc_distro:
            return unc_distro.capitalize()
        detected = detect_default_wsl_distro()
        return detected or "Ubuntu"

    def get_codex_home(self) -> Path:
        raw = os.environ.get("OEM_CODEX_HOME") or os.environ.get("CODEX_HOME")
        if not raw and sys.platform != "win32":
            raw = self._detect_windows_codex_home_from_wsl()
            if not raw and is_wsl():
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
        unc_path = wsl_path_from_unc(raw_path)
        if unc_path:
            return unc_path

        if sys.platform == "win32":
            converted = windows_to_wsl_path(str(raw_path.resolve()))
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
                except Exception as e:
                    logger.debug(f"Failed to read pyproject.toml at {pyproject_path}: {e}")
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
            f"test -d {shell_quote(project_dir)} "
            "&& command -v oem >/dev/null "
            "&& oem --version >/dev/null"
        )
        return [self.get_probe_wsl_exe(), "-d", distro, "--cd", project_dir, "bash", "-lc", check]

    def get_windows_wsl_exe(self) -> str:
        return get_wsl_exe_path()

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

        self.install_project_skill()

        healthy, message = self.verify_health(probe_bridge=False)
        return {
            "codex_home": str(codex_home),
            "skill_path": str(skill_path),
            "config_path": str(config_path),
            "mcp_config": self.build_mcp_config(),
            "healthy": healthy,
            "message": message,
        }

    def install_project_skill(self) -> bool:
        try:
            harness = self.engine._resolve_harness(self.project_path)
            skills_dir = harness / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            skills_file = skills_dir / "openempiric.yaml"

            import yaml
            existing_data = {}
            if skills_file.exists():
                try:
                    with open(skills_file, "r", encoding="utf-8") as f:
                        existing_data = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.warning(f"Failed to parse existing codex skill file: {e}")

            adapters = existing_data.get("adapters", [])
            if not isinstance(adapters, list):
                adapters = [adapters] if adapters else []
            
            if "adapter" in existing_data:
                legacy = existing_data["adapter"]
                if legacy and legacy not in adapters:
                    adapters.append(legacy)

            if "codex-app" not in adapters:
                adapters.append("codex-app")

            updated_data = dict(existing_data)
            updated_data["name"] = existing_data.get("name", "openempiric")
            updated_data["version"] = existing_data.get("version", "1.0.0")
            updated_data["schema_version"] = existing_data.get("schema_version", 1)
            updated_data["adapters"] = adapters
            
            if "adapter" in updated_data:
                del updated_data["adapter"]

            updated_data["description"] = existing_data.get("description", "Agent knowledge runtime")
            updated_data["required"] = existing_data.get("required", ["knowledge_preflight", "knowledge_search", "knowledge_capture_after_work"])
            updated_data["tools"] = existing_data.get("tools", ["oem", "knowledge_preflight", "knowledge_search"])
            updated_data["best_practices"] = existing_data.get("best_practices", [
                "OpenEmpiric is already active for this session; do not initialize it manually.",
                "Relevant project memory has been restored automatically into your context.",
                "Before planning non-trivial tasks, call knowledge_preflight with the user task.",
                "If knowledge_preflight returns required, follow the returned OEM context before planning.",
                "If knowledge_preflight returns suggest, consider the returned context and optionally use knowledge_search or knowledge_source_search.",
                "When OEM knowledge is relevant, prefer calling OEM tools directly instead of executing shell commands.",
                "Do not use shell execution when a corresponding OEM tool is available.",
                "Refer to active concepts and past failures during planning to align with existing decisions.",
                "Do not use knowledge_index as a fallback for failed reflection.",
                "Do not treat the source corpus as learned memory.",
                "Report referenced memory concepts at session end using the knowledge_usage_report tool.",
                "Use knowledge_search when additional project context is needed.",
                "Fallback Strategy: If the MCP server is unreachable or a tool call fails, fall back to the OEM CLI (oem search)."
            ])

            with open(skills_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(updated_data, f, default_flow_style=False, sort_keys=False)
            return True
        except Exception as e:
            logger.warning(f"Failed to install project skill: {e}", exc_info=True)
            return False

    def install_skill(self) -> bool:
        try:
            skill_path = self.get_skill_path()
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(CODEX_SKILL_CONTENT, encoding="utf-8")
            return True
        except Exception as e:
            logger.warning(f"Failed to install skill: {e}", exc_info=True)
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
        except Exception as e:
            logger.warning(f"Failed to verify MCP: {e}", exc_info=True)
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
            converted = windows_to_wsl_path(raw)
            if converted:
                return Path(converted)
        return Path(raw).expanduser()

    def _detect_windows_codex_home_from_wsl(self) -> str | None:
        if not is_wsl():
            return None
        windows_profile = _detect_windows_env("USERPROFILE")
        if not windows_profile:
            return None
        return windows_profile.rstrip("\\/") + "\\.codex"
