from __future__ import annotations
import sys
import json
from oem_knowledge.ui import render_panel

def run_working_set_command(args) -> None:
    action = getattr(args, "working_set_action", "show")
    if action == "status":
        run_status(args)
    elif action == "compact":
        run_compact(args)
    else:
        run_show(args)


def run_show(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.runtime.working_set import load_working_set
    ws = load_working_set(project)
    
    if ws is None:
        print("No working set found.")
        sys.exit(1)

    if getattr(args, "json", False):
        data = ws.model_dump() if hasattr(ws, "model_dump") else ws.dict()
        print(json.dumps(data, indent=2))
        return

    lines = [
        f"schema_version:   {ws.schema_version}",
        f"updated_at:       {ws.updated_at}",
        f"workspace_root:   {ws.workspace_root}",
        f"goal:             {ws.goal}",
        f"current_problem:  {ws.current_problem}",
        f"current_hypothesis: {ws.current_hypothesis}",
        f"next_action:      {ws.next_action}",
        f"active_work_item: {ws.active_work_item}",
        f"active_topic:     {ws.active_topic}",
        f"active_task:      {ws.active_task}",
        f"active_files:     {', '.join(ws.active_files) if ws.active_files else 'None'}",
        f"active_concepts:  {', '.join(ws.active_concepts) if ws.active_concepts else 'None'}",
        f"active_memory_ids:{', '.join(ws.active_memory_ids) if ws.active_memory_ids else 'None'}",
        f"blocked_by:       {', '.join(ws.blocked_by) if ws.blocked_by else 'None'}",
        f"open_questions:   {', '.join(ws.open_questions) if ws.open_questions else 'None'}",
        f"confidence:       {ws.confidence}",
    ]
    print(render_panel("OEM Working Set", lines, status="ok"))


def run_status(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.runtime.working_set import get_resume_status
    status_info = get_resume_status(project)
    
    if getattr(args, "json", False):
        data = {
            "resume_source": status_info["resume_source"],
            "freshness": status_info["freshness"],
            "age": status_info["working_set_age"],
        }
        print(json.dumps(data, indent=2))
        return
        
    age_str = "None"
    if status_info["working_set_age"] is not None:
        age_str = f"{status_info['working_set_age']:.1f}s"
        
    lines = [
        f"Resume source: {status_info['resume_source']}",
        f"Freshness:     {status_info['freshness']}",
        f"Age:           {age_str}",
    ]
    print(render_panel("Working Set Status", lines, status="ok"))


def run_compact(args) -> None:
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    from oem_knowledge.runtime.working_set import compact_working_set
    success = compact_working_set(project)

    if getattr(args, "json", False):
        print(json.dumps({"success": success}, indent=2))
        return

    if success:
        print("Successfully compacted working set (pruned active lists to defined caps).")
    else:
        print("Error: Failed to compact working set.", file=sys.stderr)
        sys.exit(1)
