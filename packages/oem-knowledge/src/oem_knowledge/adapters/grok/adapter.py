from __future__ import annotations

import json
import logging
import os
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter

logger = logging.getLogger(__name__)


def _get_grok_home() -> Path:
    """Respect GROK_HOME if set, otherwise ~/.grok."""
    grok_home = os.environ.get("GROK_HOME")
    if grok_home:
        return Path(grok_home).expanduser().resolve()
    return Path.home() / ".grok"


def _get_grok_bin() -> Optional[str]:
    """Return path to grok executable, preferring GROK_BIN then GROK_HOME/bin/grok then PATH."""
    grok_bin = os.environ.get("GROK_BIN")
    if grok_bin and shutil.which(grok_bin):
        return grok_bin

    grok_home = _get_grok_home()
    candidate = grok_home / "bin" / "grok"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate)

    return shutil.which("grok")


def _encode_cwd_for_grok_session(cwd: Optional[str] = None) -> str:
    """Replicate Grok's session dir encoding for the working directory."""
    path = Path(cwd or ".").resolve()
    # Grok uses URL-encoding of the absolute path
    encoded = urllib.parse.quote(str(path), safe="")
    # For very long names Grok falls back, but for v1 we use direct encoding
    # (matching observed session layout in this workspace)
    return encoded


def _find_latest_grok_session_dir(project_path: Optional[str] = None) -> Optional[Path]:
    """Find the most recent session directory for the current (or given) cwd under GROK_HOME/sessions/."""
    grok_home = _get_grok_home()
    sessions_root = grok_home / "sessions"
    if not sessions_root.exists():
        return None

    cwd = project_path or str(Path.cwd().resolve())
    encoded = _encode_cwd_for_grok_session(cwd)
    group_dir = sessions_root / encoded

    if not group_dir.exists():
        # Try to find a group that has a .cwd file pointing to us (long-path case)
        try:
            for d in sessions_root.iterdir():
                if d.is_dir():
                    cwd_file = d / ".cwd"
                    if cwd_file.exists():
                        try:
                            if cwd_file.read_text(encoding="utf-8").strip() == cwd:
                                group_dir = d
                                break
                        except Exception:
                            pass
        except Exception:
            pass

    if not group_dir.exists() or not group_dir.is_dir():
        return None

    # Find the latest session dir by mtime of summary.json or the dir itself
    candidates = []
    for entry in group_dir.iterdir():
        if entry.is_dir():
            summary = entry / "summary.json"
            mtime = summary.stat().st_mtime if summary.exists() else entry.stat().st_mtime
            candidates.append((mtime, entry))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


@register_adapter("grok")
@register_adapter("grok-build")
class GrokAdapter(BaseAdapter):
    """Adapter for the Grok (xAI Grok Build) TUI/CLI.

    Supports `oem run grok` with full OEM session lifecycle.
    Respects GROK_HOME for all path discovery.
    """

    def __init__(self, engine=None, project_path: Optional[str] = None):
        super().__init__(engine, project_path)

    def _grok_home(self) -> Path:
        return _get_grok_home()

    def verify_mcp(self) -> bool:
        """Grok 'MCP ready' if the binary is discoverable and ~/.grok exists."""
        try:
            bin_path = _get_grok_bin()
            home = self._grok_home()
            return bool(bin_path) and home.exists()
        except Exception:
            return False

    def verify_health(self) -> tuple[bool, str]:
        bin_path = _get_grok_bin()
        home = self._grok_home()

        if not bin_path:
            return False, "grok binary not found in PATH or GROK_HOME/bin"

        if not home.exists():
            return False, f"Grok home directory not found at {home}"

        return True, f"Grok healthy (binary={bin_path}, home={home})"

    def get_expected_transcript_path(self, session_id: str) -> Path:
        """Preferred path for a given session_id under the current cwd group."""
        grok_home = self._grok_home()
        encoded = _encode_cwd_for_grok_session(self.project_path)
        return (
            grok_home
            / "sessions"
            / encoded
            / session_id
            / "chat_history.jsonl"
        )

    def parse_transcript(self, transcript_path: Path) -> str:
        """Parse Grok chat_history.jsonl (or updates.jsonl fallback) into plain User/Agent text."""
        if not transcript_path.exists():
            return ""

        lines: list[str] = []
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue

                    mtype = msg.get("type") or msg.get("role")

                    # chat_history.jsonl style
                    if mtype == "user":
                        content = msg.get("content", "")
                        text = self._extract_text(content)
                        if text:
                            lines.append(f"User: {text}")

                    elif mtype in ("assistant", "model", "agent"):
                        content = msg.get("content", "")
                        text = self._extract_text(content)
                        if text:
                            lines.append(f"Agent: {text}")

                    # Fallback for some updates.jsonl shapes
                    elif "role" in msg:
                        role = msg.get("role")
                        content = msg.get("content", "")
                        text = self._extract_text(content)
                        if text:
                            prefix = "User" if role == "user" else "Agent"
                            lines.append(f"{prefix}: {text}")

            return "\n\n".join(lines)
        except Exception as e:
            logger.warning(f"Failed to parse Grok transcript {transcript_path}: {e}")
            return ""

    def _extract_text(self, content: Any) -> str:
        """Extract plain text from Grok content (string or list of parts)."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") == "text":
                        parts.append(part.get("text", ""))
                    elif "text" in part:
                        parts.append(str(part["text"]))
                elif isinstance(part, str):
                    parts.append(part)
            return " ".join(p for p in parts if p).strip()
        return str(content or "").strip()

    def discover_latest_transcript(self) -> Optional[Path]:
        """Locate the most recent chat transcript for the current project cwd."""
        session_dir = _find_latest_grok_session_dir(self.project_path)
        if not session_dir:
            return None

        # Prefer chat_history.jsonl
        chat = session_dir / "chat_history.jsonl"
        if chat.exists():
            return chat

        # Fallback to updates.jsonl (authoritative stream)
        updates = session_dir / "updates.jsonl"
        if updates.exists():
            return updates

        return None

    def install_skill(self) -> bool:
        """Install a lightweight skill reference in project .grok/skills if present."""
        try:
            project_root = Path(self.project_path or ".").resolve()
            grok_project_dir = project_root / ".grok"
            skills_dir = grok_project_dir / "skills" / "openempiric"
            skills_dir.mkdir(parents=True, exist_ok=True)

            skill_file = skills_dir / "SKILL.md"
            # Keep content short and point to OEM instructions
            content = (
                "# OpenEmpiric\n\n"
                "OpenEmpiric provides long-term project memory via MCP tools.\n"
                "Before planning non-trivial tasks, call `knowledge_preflight` with the user task.\n"
                "If preflight returns `required`, follow the returned OEM context before planning.\n"
                "If preflight returns `suggest`, consider the returned context and optionally use `knowledge_search` or `knowledge_source_search`.\n"
                "Call `knowledge_read`, `knowledge_search`, `knowledge_reflect` etc. when appropriate.\n"
                "Do not use `knowledge_index` as a fallback for failed reflection.\n"
                "Do not treat the source corpus as learned memory.\n"
                "Start sessions with knowledge_session_start when using the full lifecycle.\n"
            )
            skill_file.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.warning(f"Failed to install Grok skill: {e}")
            return False
