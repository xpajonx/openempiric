from __future__ import annotations

import json
from pathlib import Path

_BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "dd if=",
    "format",
    ":(){ :|:& };:",
    "> /dev/sda",
    "chmod -R 000",
    "chown -R",
    "passwd",
    "usermod",
    "visudo",
]


def _permissions_path(workdir: str = "") -> Path:
    base = Path(workdir) if workdir else Path.cwd()
    return base / ".harness" / "state" / "permissions.json"


def load_permissions(workdir: str = "") -> dict:
    p = _permissions_path(workdir)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"allowed_dirs": [], "blocked_commands": _BLOCKED_COMMANDS, "allow_subagent_permissions_skip": True}


def save_permissions(perms: dict, workdir: str = ""):
    p = _permissions_path(workdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(perms, indent=2))


def resolve_workdir(workdir: str, project_root: str = "") -> str:
    """Resolve a subagent workdir relative to the project root.
    
    Returns None if the workdir escapes the project root (blocked).
    """
    base = Path(project_root).resolve() if project_root else Path.cwd().resolve()
    target = Path(workdir).resolve() if workdir else base

    try:
        target.relative_to(base)
        return str(target)
    except ValueError:
        return str(base)


def is_command_blocked(command: str, workdir: str = "") -> bool:
    """Check if a command is in the blocked list."""
    perms = load_permissions(workdir)
    blocked = perms.get("blocked_commands", _BLOCKED_COMMANDS)
    cmd_lower = command.lower()
    for bc in blocked:
        if bc in cmd_lower:
            return True
    return False
