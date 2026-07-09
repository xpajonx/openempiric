import argparse
import sys
import json
from pathlib import Path
from oem_knowledge.ui import render_panel

def run_checkpoint_command(args) -> None:
    action = getattr(args, "checkpoint_action", "list")
    if action == "restore":
        run_restore(args)
    elif action == "create":
        run_create(args)
    else:
        run_list(args)

def run_list(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.runtime.working_set import list_checkpoints
    checkpoints = list_checkpoints(project)

    if getattr(args, "json", False):
        print(json.dumps(checkpoints, indent=2))
        return

    if not checkpoints:
        print("No checkpoints found.")
        return

    lines = []
    for cp in checkpoints:
        reason = cp["checkpoint_reason"] or "N/A"
        goal = cp["goal"] or "N/A"
        lines.append(
            f"Index/Name: {cp['name_id']}\n"
            f"  Created:  {cp['updated_at']}\n"
            f"  Reason:   {reason}\n"
            f"  Goal:     {goal}"
        )
    
    print(render_panel("OEM Working Set Checkpoints", lines, status="ok"))

def run_restore(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None
    target = getattr(args, "target", None)

    from oem_knowledge.runtime.working_set import restore_checkpoint
    success = restore_checkpoint(target, project)

    if getattr(args, "json", False):
        print(json.dumps({"success": success, "target": target}, indent=2))
        return

    if success:
        print(f"Successfully restored working set to checkpoint '{target}'.")
    else:
        print(f"Error: Failed to restore checkpoint '{target}' (checkpoint not found or failed validation).", file=sys.stderr)
        sys.exit(1)

def run_create(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.runtime.working_set import create_checkpoint
    cp_path = create_checkpoint(reason="manual", project=project)
    success = cp_path is not None

    if getattr(args, "json", False):
        print(json.dumps({"success": success, "checkpoint_path": str(cp_path) if cp_path else None}, indent=2))
        return

    if success:
        print(f"Successfully created checkpoint: {cp_path.name}")
    else:
        print("Error: Failed to create checkpoint.", file=sys.stderr)
        sys.exit(1)
