from __future__ import annotations

import subprocess
from pathlib import Path

from ..client import find_opencode


def register(mcp: object) -> None:
    from fastmcp import FastMCP
    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_list_agents() -> str:
        """List all configured opencode agents with their mode and tool permissions."""
        cmd = find_opencode()
        try:
            result = subprocess.run([cmd, "agent", "list"], capture_output=True, text=True, timeout=15)
        except Exception as e:
            return f"Error: {e}"
        return result.stdout.strip() or "(no agents)"

    @mcp.tool()
    def harness_list_projects(base_dir: str = str(Path.home() / "projects")) -> str:
        """List project directories available for opencode to work in."""
        base = Path(base_dir)
        if not base.is_dir():
            return f"Error: {base_dir} not found"
        dirs = sorted(d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("."))
        if not dirs:
            return f"No projects found in {base_dir}"
        lines = [f"Projects ({len(dirs)}):"]
        for d in dirs:
            lines.append(f"  {d}/")
        return "\n".join(lines)
