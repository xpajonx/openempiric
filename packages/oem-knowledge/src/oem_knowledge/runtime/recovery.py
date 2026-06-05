from __future__ import annotations
import json
import sys
import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from oem_tui.panels import render_panel
from .config import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def cmd_recover(eng: KnowledgeEngine, project: str | None = None, abort: bool = False, status: bool = False):
    harness = eng._resolve_harness(project)
    active_session_file = harness / "state" / "active_session.json"
    if not active_session_file.exists():
        print(render_panel("OEM Recovery", ["No unfinished sessions detected."], status="info"))
        return

    try:
        session_data = json.loads(active_session_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(render_panel("Recovery Error", [f"Failed to read active session file: {e}"], status="error"))
        sys.exit(1)

    session_id = session_data.get("session_id")
    agent_name = session_data.get("agent", "opencode")
    started_at = session_data.get("started_at", 0.0)
    current_status = session_data.get("status", "unknown")
    transcript_path = session_data.get("transcript_path", "")

    if status:
        started_str = datetime.datetime.fromtimestamp(started_at).isoformat() if started_at else "unknown"
        lines = [
            f"Session ID:      {session_id}",
            f"Agent:           {agent_name}",
            f"Lifecycle State: {current_status}",
            f"Started At:      {started_str}",
            f"Project:         {session_data.get('project')}",
            f"Transcript Path: {transcript_path}",
            f"Context Path:    {session_data.get('context_path')}",
            f"Temp Inst Path:  {session_data.get('temp_instructions')}"
        ]
        print(render_panel("Active Session Status", lines, status="stats"))
        return

    if abort:
        context_path = session_data.get("context_path")
        temp_inst = session_data.get("temp_instructions")
        for path_str in (context_path, temp_inst, str(_OEM_RUNTIME_CONTEXT_PATH), str(_OEM_TEMP_INSTRUCTIONS)):
            if path_str:
                p = Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
        try:
            session_data["status"] = "failed"
            active_session_file.unlink()
        except Exception:
            pass
        print(render_panel("Session Aborted", [f"Session {session_id} has been discarded and cleaned up."], status="ok"))
        return

    print(render_panel("Recovering Session", [f"Attempting to recover session {session_id} (State: {current_status})"], status="info"))
    
    from oem_knowledge.adapters import get_adapter
    adapter = get_adapter(agent_name, eng, project)

    chat_text = ""
    if transcript_path:
        t_file = Path(transcript_path)
        if t_file.exists():
            if hasattr(adapter, "parse_transcript"):
                chat_text = adapter.parse_transcript(t_file)
            else:
                chat_text = t_file.read_text(encoding="utf-8")

    if not chat_text:
        if hasattr(adapter, "discover_latest_transcript") and hasattr(adapter, "parse_transcript"):
            latest_t = adapter.discover_latest_transcript()
            if latest_t:
                chat_text = adapter.parse_transcript(latest_t)
        if not chat_text:
            chat_path = harness / "state" / f"chat_{session_id}.md"
            if chat_path.exists():
                chat_text = chat_path.read_text(encoding="utf-8")
                try:
                    chat_path.unlink()
                except Exception:
                    pass

    if not chat_text:
        print(render_panel("Recovery Failed", ["Could not find any conversation transcript or log for the session."], status="error"))
        sys.exit(1)

    try:
        commit_res = eng.session_commit(project, conversation_text=chat_text, session_id=session_id)
        eng.record_outcome("success", session_id=session_id, project=project)
        
        try:
            session_data["status"] = "completed"
            active_session_file.unlink()
        except Exception:
            pass

        context_path = session_data.get("context_path")
        temp_inst = session_data.get("temp_instructions")
        for path_str in (context_path, temp_inst, str(_OEM_RUNTIME_CONTEXT_PATH), str(_OEM_TEMP_INSTRUCTIONS)):
            if path_str:
                p = Path(path_str)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

        print(
            render_panel(
                "Recovery Complete",
                [
                    f"Successfully recovered and committed session {session_id}.",
                    f"Report: {Path(commit_res['report_path']).name}",
                    f"Materialized: {len(commit_res.get('materialized_log', []))}",
                    f"Links: {commit_res.get('links_updated', 0)}",
                ],
                status="ok",
            )
        )
    except Exception as e:
        print(render_panel("Recovery Commit Failed", [f"Error committing recovered session: {e}"], status="error"))
        sys.exit(1)
