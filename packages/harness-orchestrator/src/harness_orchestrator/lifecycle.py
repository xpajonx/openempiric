from __future__ import annotations

from pathlib import Path

from harness_knowledge.engine import KnowledgeEngine
from harness_tui.panels import render_panel


def on_session_start(workdir: str = "") -> str:
    """Called automatically when a session begins. Reads .harness/ state and returns a pre-injection block."""
    eng = KnowledgeEngine(workdir or None)
    try:
        res = eng.restore_session_state(workdir or None)
    except Exception as e:
        return render_panel("Lifecycle: Session Start", [f"Error: {e}"], status="error")

    if res.get("status") == "error":
        return render_panel("Lifecycle: Session Start", [res.get("message", "Unknown error")], status="error")

    lines = []
    goals = res.get("active_goals", [])
    blockers = res.get("blockers", [])
    files = res.get("recommended_files", [])

    if goals:
        lines.append("Active Goals:")
        for g in goals:
            lines.append(f"  • {g}")
    if blockers:
        lines.append("Blockers:")
        for b in blockers:
            lines.append(f"  • {b}")
    if files:
        lines.append("Relevant Context Files:")
        for f in files:
            lines.append(f"  • {f}")

    if not lines:
        lines.append("No pre-existing state found for this project.")

    return render_panel("Session State Restored", lines, status="restore")


def on_session_end(conversation_text: str = "", session_id: str = "", workdir: str = "", telemetry: dict | None = None) -> str:
    """Called automatically when a session ends. Commits conversation to the knowledge graph.
    
    This is the load-bearing integration: every CLI action is automatically a knowledge event.
    """
    eng = KnowledgeEngine(workdir or None)
    try:
        res = eng.session_commit(workdir or None, conversation_text, session_id, telemetry=telemetry)
    except Exception as e:
        return render_panel("Lifecycle: Session Commit", [f"Error: {e}"], status="error")

    events = res.get("knowledge_events", [])
    event_counts: dict[str, int] = {}
    for ev in events:
        t = ev.get("type", "observation")
        event_counts[t] = event_counts.get(t, 0) + 1

    lines = [f"Committed to .harness/ knowledge graph.", ""]
    if event_counts:
        lines.append("Knowledge Events:")
        for t, c in sorted(event_counts.items()):
            lines.append(f"  • {t}: {c}")
    lines.extend([
        "",
        "Graph & Index:",
        f"  Materialized: {len(res.get('materialized_log', []))} concepts",
        f"  Links: {res.get('links_updated', 0)}",
    ])
    return render_panel("Session Committed", lines, status="ok")
