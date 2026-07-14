from __future__ import annotations

import json
from pathlib import Path

from oem_knowledge.runtime.working_set import (
    create_checkpoint as _create_checkpoint,
    list_checkpoints as _list_checkpoints,
    restore_checkpoint as _restore_checkpoint,
)


def oem_checkpoint_list(project: str = "") -> str:
    """List all working set checkpoints.

    Args:
        project: Project directory path. Defaults to current directory.
    """
    try:
        project_path = _resolve_project(project)
        checkpoints = _list_checkpoints(project_path)
        return json.dumps({"status": "success", "checkpoints": checkpoints, "count": len(checkpoints)}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)


def oem_checkpoint_create(project: str = "") -> str:
    """Create a manual working set checkpoint.

    Args:
        project: Project directory path. Defaults to current directory.
    """
    try:
        project_path = _resolve_project(project)
        cp_path = _create_checkpoint(reason="manual", project=project_path)
        if cp_path:
            return json.dumps({"status": "success", "checkpoint_path": str(cp_path), "name": cp_path.name}, indent=2)
        else:
            return json.dumps({"status": "error", "message": "Failed to create checkpoint (no working set or engine error)."}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)}, indent=2)


def oem_checkpoint_restore(target: str, project: str = "") -> str:
    """Restore working set to a checkpoint.

    Args:
        target: Checkpoint name_id (timestamp string like 20260710_092135_792958) or filename.
        project: Project directory path. Defaults to current directory.
    """
    if not target:
        return json.dumps({"status": "error", "message": "target argument is required"}, indent=2)
    try:
        project_path = _resolve_project(project)
        success = _restore_checkpoint(target, project_path)
        if success:
            return json.dumps({"status": "success", "target": target, "message": f"Restored working set to checkpoint '{target}'."}, indent=2)
        else:
            return json.dumps({"status": "error", "target": target, "message": f"Checkpoint '{target}' not found or validation failed."}, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "target": target, "message": str(e)}, indent=2)


def _resolve_project(project: str) -> str | None:
    raw = project.strip() if project else ""
    if raw and raw != ".":
        return raw
    return None


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    mcp.tool()(oem_checkpoint_list)
    mcp.tool()(oem_checkpoint_create)
    mcp.tool()(oem_checkpoint_restore)
