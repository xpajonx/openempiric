from __future__ import annotations
import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .config import _REPO_ROOT, _OPENCODE_PLUGINS_DIR, _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS
from .context import _compile_oem_context

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

def run_agent(agent_name: str, eng: KnowledgeEngine, project: str | None = None):
    # 0. Resolve project harness
    harness = eng._resolve_harness(project)

    # Resolve adapter
    from oem_knowledge.adapters import get_adapter
    adapter = get_adapter(agent_name, eng, project)

    # 1. Ensure plugin is available in opencode plugins dir
    plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
    _OPENCODE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    plugin_dest = _OPENCODE_PLUGINS_DIR / "openempiric.ts"

    if plugin_src.exists():
        if plugin_dest.exists() or plugin_dest.is_symlink():
            try:
                plugin_dest.unlink()
            except Exception:
                pass
        try:
            plugin_dest.symlink_to(plugin_src)
        except Exception:
            try:
                shutil.copy(plugin_src, plugin_dest)
            except Exception as e:
                logging.warning("Failed to copy plugin file to %s: %s", plugin_dest, e)

    # 2. Pre-session: generate session_id, restore state, compile context
    session_id = uuid.uuid4().hex[:12]

    # Resolve expected transcript path via adapter
    try:
        t_path = adapter.get_expected_transcript_path(session_id)
        transcript_path_str = str(t_path.resolve())
    except Exception:
        h = eng._resolve_harness(project)
        transcript_path_str = str((h / "state" / f"chat_{session_id}.md").resolve())

    harness = eng._resolve_harness(project)
    active_session_file = harness / "state" / "active_session.json"
    active_session_file.parent.mkdir(parents=True, exist_ok=True)
    
    session_state = {
        "session_id": session_id,
        "agent": agent_name,
        "status": "started",
        "started_at": time.time(),
        "project": str(Path(project or ".").resolve()),
        "transcript_path": transcript_path_str,
        "context_path": str(_OEM_RUNTIME_CONTEXT_PATH.resolve()),
        "temp_instructions": str(_OEM_TEMP_INSTRUCTIONS.resolve())
    }

    try:
        active_session_file.write_text(json.dumps(session_state, indent=2), encoding="utf-8")
    except Exception as e:
        logging.warning("Failed to write active session file: %s", e)

    context = _compile_oem_context(eng)
    try:
        _OEM_RUNTIME_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OEM_RUNTIME_CONTEXT_PATH.write_text(
            json.dumps(context, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.warning("Failed to write runtime context to %s: %s", _OEM_RUNTIME_CONTEXT_PATH, e)

    try:
        logging.info("Restoring session state (session_id=%s)", session_id)
        eng.restore_session_state(project)
    except Exception as e:
        logging.warning("Pre-session restore failed: %s", e)

    # Set status to running
    try:
        session_state["status"] = "running"
        active_session_file.write_text(json.dumps(session_state, indent=2), encoding="utf-8")
    except Exception:
        pass

    # 3. Spawn agent with managed mode env vars
    managed_env = os.environ.copy()
    managed_env["OEM_MANAGED"] = "1"
    managed_env["OEM_SESSION_ID"] = session_id
    managed_env["OEM_RUNTIME_CONTEXT_PATH"] = str(_OEM_RUNTIME_CONTEXT_PATH)
    if project:
        managed_env["OEM_PROJECT"] = project

    logging.info("Spawning coding agent: %s... (managed session_id=%s)", agent_name, session_id)
    try:
        if agent_name == "opencode":
            subprocess.run(["opencode"], check=True, env=managed_env)
        elif agent_name == "claude-code":
            subprocess.run(["claude"], check=True, env=managed_env)
        elif agent_name == "cursor":
            subprocess.run(["cursor", "."], check=True, env=managed_env)
        elif agent_name in ("agy", "antigravity"):
            # Run the antigravity (agy) agent
            subprocess.run(["agy"], check=True, env=managed_env)
        else:
            subprocess.run(agent_name.split(), check=True, env=managed_env)
    except Exception as e:
        logging.warning("Agent session finished or returned: %s", e)
    finally:
        # 4. Post-session: read deferred chat from plugin or from agent transcripts
        chat_text = ""
        try:
            transcript_file = Path(session_state["transcript_path"])
            if transcript_file.exists():
                if hasattr(adapter, "parse_transcript"):
                    chat_text = adapter.parse_transcript(transcript_file)
                else:
                    chat_text = transcript_file.read_text(encoding="utf-8")
        except Exception:
            pass

        if not chat_text:
            if hasattr(adapter, "discover_latest_transcript") and hasattr(adapter, "parse_transcript"):
                latest_t = adapter.discover_latest_transcript()
                if latest_t:
                    logging.info(f"Discovered transcript: {latest_t}")
                    chat_text = adapter.parse_transcript(latest_t)
            else:
                chat_path = harness / "state" / f"chat_{session_id}.md"
                if chat_path.exists():
                    chat_text = chat_path.read_text(encoding="utf-8")
                    try:
                        chat_path.unlink()
                    except Exception:
                        pass

        # 5. Session commit (reflect → materialize → graph → index)
        committed = False
        try:
            commit_res = eng.session_commit(project, conversation_text=chat_text, session_id=session_id)
            logging.info("Session commit: report=%s events=%d materialized=%d",
                         commit_res.get("report_path", "?"),
                         len(commit_res.get("canonical_events", [])),
                         len(commit_res.get("materialized_log", [])))
            committed = True
        except Exception as e:
            logging.warning("Post-session commit failed: %s", e)

        # 6. Record outcome
        try:
            eng.record_outcome("success" if committed else "failure", session_id=session_id, project=project)
        except Exception as e:
            logging.warning("Outcome recording failed: %s", e)

        # 7. Cleanup temp files
        for p in [_OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        # Delete active session file on successful completion
        try:
            if active_session_file.exists():
                if committed:
                    session_state["status"] = "completed"
                    active_session_file.unlink()
                else:
                    session_state["status"] = "failed"
                    active_session_file.write_text(json.dumps(session_state, indent=2), encoding="utf-8")
        except Exception:
            pass
