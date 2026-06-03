from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ..engine import OEM_DIR


def _todos_path(workdir: str = "") -> Path:
    base = Path(workdir) if workdir else Path.cwd()
    return base / OEM_DIR / "state" / "todos.json"


def _load_todos(workdir: str = "") -> list[dict]:
    p = _todos_path(workdir)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_todos(todos: list[dict], workdir: str = ""):
    p = _todos_path(workdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(todos, indent=2))


def oem_todo_write(items: str, workdir: str = "") -> str:
    """Replace the current todo list with new items. Persists to .oem/state/todos.json.

    Args:
        items: JSON array of item objects. Each item: {"content": "description", "status": "pending"}.
        workdir: Project directory. Defaults to current directory.
    """
    try:
        parsed = json.loads(items)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON: {e}"
    if not isinstance(parsed, list):
        return "Error: items must be a JSON array"

    todos = []
    for item in parsed:
        content = item.get("content", "")
        if not content:
            continue
        todos.append(
            {
                 "id": item.get("id", str(uuid.uuid4())),
                 "content": content,
                 "status": item.get("status", "pending"),
                 "created_at": time.strftime("%Y-%m-%d %H:%M"),
            }
        )

    _save_todos(todos, workdir)

    summary = []
    for t in todos:
        summary.append(f"  [{t['status'][0].upper()}] {t['content']}")
    return f"Todo list updated ({len(todos)} items):\n" + "\n".join(summary)


def oem_todo_read(workdir: str = "") -> str:
    """Read the current todo list from .oem/state/todos.json."""
    todos = _load_todos(workdir)
    if not todos:
        return "Todo list is empty."

    summary = [f"Todo list ({len(todos)} items):"]
    for t in todos:
        status_icon = {"pending": " ", "in_progress": "→", "completed": "✓"}.get(
            t.get("status", "pending"), " "
        )
        summary.append(f"  [{status_icon}] {t['content']}  (id: {t['id']})")
    return "\n".join(summary)


def oem_todo_advance(item_id: str, status: str = "", workdir: str = "") -> str:
    """Update one todo item's status. If set to 'completed', auto-advance the next pending item to 'in_progress'.

    Args:
        item_id: The item's UUID.
        status: New status (pending, in_progress, completed). If empty, cycles to the next state.
        workdir: Project directory. Defaults to current directory.
    """
    todos = _load_todos(workdir)
    if not todos:
        return "Error: No todo items found."

    target = None
    for t in todos:
        if t["id"] == item_id:
            target = t
            break

    if not target:
        return f"Error: Item {item_id} not found."

    if status:
        target["status"] = status
    else:
        cycle = {
            "pending": "in_progress",
            "in_progress": "completed",
            "completed": "pending",
        }
        target["status"] = cycle.get(target["status"], "in_progress")

    if target["status"] == "completed":
        for t in todos:
            if t["status"] == "pending":
                t["status"] = "in_progress"
                break

    _save_todos(todos, workdir)
    return f"Updated item {item_id}: {target['content']} → {target['status']}"


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    mcp.tool()(oem_todo_write)
    mcp.tool()(oem_todo_read)
    mcp.tool()(oem_todo_advance)
