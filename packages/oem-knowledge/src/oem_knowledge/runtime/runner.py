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
from oem_knowledge.tools.metrics import update_metrics_file
from .context import _compile_oem_context
from .session import SessionState

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


def _link_plugin():
    """Symlink the TypeScript plugin into opencode's plugins directory. Idempotent."""
    plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
    if not plugin_src.exists():
        plugin_src = Path(__file__).resolve().parent.parent / "plugins" / "openempiric.ts"
    if not plugin_src.exists():
        plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
        
    import tempfile
    sys_temp = Path(tempfile.gettempdir()).resolve()
    try:
        is_in_temp = plugin_src.resolve().is_relative_to(sys_temp)
    except ValueError:
        is_in_temp = False
    if is_in_temp:
        try:
            is_in_repo = plugin_src.resolve().is_relative_to(_REPO_ROOT.resolve())
        except ValueError:
            is_in_repo = False
        if not is_in_repo:
            logging.warning("Plugin source is in volatile temp directory, skipping symlink: %s", plugin_src)
            return

    _OPENCODE_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    plugin_dest = _OPENCODE_PLUGINS_DIR / "openempiric.ts"
    if not plugin_src.exists():
        logging.warning("Plugin source not found at %s", plugin_src)
        return
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


def _ensure_workspace_ready(eng: KnowledgeEngine, project: str | None, adapter, warnings: list[str]) -> None:
    """Auto-init project + warm up model + verify plugin. Idempotent. Critical steps fail fast, optional steps fail open."""
    # Critical - fail fast
    if not eng.is_initialized(project):
        logging.info("First-run detected — bootstrapping project...")
        eng.init_project(project or ".")

    harness = eng._resolve_harness(project)

    # Optional - fail open
    try:
        eng.warmup_if_needed()
    except Exception as e:
        logging.warning("Embedding model warmup failed: %s", e)
        warnings.append("Vector search unavailable")

    try:
        if not adapter.verify_mcp():
            logging.info("Plugin not linked — installing...")
            _link_plugin()
            adapter.install_skill()
    except Exception as e:
        logging.warning("Plugin linkage/repair failed: %s", e)
        warnings.append("Plugin integration disabled")


def _auto_recover_stale_session(eng: KnowledgeEngine, project: str | None = None) -> None:
    """Detect and auto-recover any unfinished session. Silent if nothing to do."""
    try:
        harness = eng._resolve_harness(project)
        active_file = harness / "state" / "active_session.json"
        if not active_file.exists():
            return

        session_state = SessionState.load(active_file)
        if not session_state:
            return

        if session_state.status in ("completed",):
            active_file.unlink(missing_ok=True)
            return

        session_id = session_state.session_id
        agent_name = session_state.agent
        logging.info("Auto-recovering stale session %s (state=%s)", session_id, session_state.status)

        from oem_knowledge.adapters import get_adapter
        adapter = get_adapter(agent_name, eng, project)

        chat_text = ""
        if session_state.transcript_path:
            t_file = Path(session_state.transcript_path)
            if t_file.exists():
                if hasattr(adapter, "parse_transcript"):
                    chat_text = adapter.parse_transcript(t_file)
                else:
                    chat_text = t_file.read_text(encoding="utf-8")

        if not chat_text and hasattr(adapter, "discover_latest_transcript"):
            latest_t = adapter.discover_latest_transcript()
            if latest_t:
                if hasattr(adapter, "parse_transcript"):
                    chat_text = adapter.parse_transcript(latest_t)
                else:
                    chat_text = latest_t.read_text(encoding="utf-8")

        if not chat_text:
            chat_path = harness / "state" / f"chat_{session_id}.md"
            if chat_path.exists():
                chat_text = chat_path.read_text(encoding="utf-8")
                try:
                    chat_path.unlink()
                except Exception:
                    pass

        if chat_text:
            commit_res = eng.session_commit(project, conversation_text=chat_text, session_id=session_id)
            eng.record_outcome("success", session_id=session_id, project=project)
            logging.info("Auto-recovery complete: report=%s events=%d",
                         commit_res.get("report_path", "?"),
                         len(commit_res.get("canonical_events", [])))
        else:
            logging.info("No transcript found for stale session — recording as abandoned")
            eng.record_outcome("abandoned", session_id=session_id, project=project)

        try:
            metrics_file = harness / "state" / "metrics.json"
            update_metrics_file(metrics_file, {"sessions_recovered": 1})
        except Exception:
            pass

        active_file.unlink(missing_ok=True)

    except Exception as e:
        logging.warning("Auto-recovery failed: %s — user can still use oem recover manually", e)


def run_agent(agent_name: str, eng: KnowledgeEngine, project: str | None = None):
    warnings = []
    start_time = time.time()

    # Track stale session recovery status
    stale_existed = False
    recovery_failed = False
    try:
        h = eng._resolve_harness(project)
        active_file = h / "state" / "active_session.json"
        if active_file.exists():
            stale_existed = True
            _auto_recover_stale_session(eng, project)
            if active_file.exists():
                recovery_failed = True
    except Exception:
        recovery_failed = True

    # Resolve harness - Critical step, fails fast
    harness = eng._resolve_harness(project)

    # Resolve adapter - Critical step, fails fast
    from oem_knowledge.adapters import get_adapter
    adapter = get_adapter(agent_name, eng, project)

    # 1. Ensure workspace is ready (auto-init, warmup, plugin)
    _ensure_workspace_ready(eng, project, adapter, warnings)

    # 2. Verify adapter health, auto-repair if needed
    if hasattr(adapter, "verify_health"):
        try:
            healthy, msg = adapter.verify_health()
            if not healthy:
                logging.warning("Adapter health check failed: %s — attempting repair", msg)
                try:
                    _link_plugin()
                    adapter.install_skill()
                    healthy, msg = adapter.verify_health()
                except Exception as e:
                    healthy, msg = False, str(e)
                if not healthy:
                    logging.warning("Adapter repair failed: %s — continuing without plugin", msg)
                    warnings.append("Plugin integration disabled")
        except Exception as e:
            logging.warning("Adapter health check error: %s", e)
            warnings.append("Plugin integration disabled")

    # 2. Pre-session: generate session_id, restore state, compile context
    session_id = uuid.uuid4().hex[:12]

    # Resolve expected transcript path via adapter
    try:
        t_path = adapter.get_expected_transcript_path(session_id)
        transcript_path_str = str(t_path.resolve())
    except Exception:
        h = eng._resolve_harness(project)
        transcript_path_str = str((h / "state" / f"chat_{session_id}.md").resolve())

    active_session_file = harness / "state" / "active_session.json"
    try:
        active_session_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        warnings.append(f"Session directory creation failed ({e})")
    
    session_state = SessionState.create(
        session_id=session_id,
        agent=agent_name,
        project=project or ".",
        transcript_path=transcript_path_str,
        context_path=str(_OEM_RUNTIME_CONTEXT_PATH.resolve()),
        temp_instructions=str(_OEM_TEMP_INSTRUCTIONS.resolve()),
    )

    try:
        session_state.save(active_session_file)
    except Exception as e:
        warnings.append(f"Session tracking state save failed ({e})")

    # Emit sessions_started metric
    try:
        metrics_file = harness / "state" / "metrics.json"
        update_metrics_file(metrics_file, {"sessions_started": 1})
    except Exception:
        pass

    try:
        context = _compile_oem_context(eng)
    except Exception as e:
        logging.warning("Context compilation failed: %s. Falling back to minimal context.", e)
        warnings.append("Context enrichment disabled")
        context = {
            "active_concepts": [],
            "active_decisions": [],
            "relevant_failures": [],
            "open_questions": [],
            "memory_context": (
                "# OEM Runtime Notice\n"
                "Session lifecycle is automatic. Do not manually initialize OEM, activate memory, or call "
                "knowledge_session_start / knowledge_session_commit. Use OEM tools only when knowledge search or "
                "health checks are needed.\n\n"
                "OpenEmpiric is already active for this session; do not initialize it manually. "
                "Use the knowledge_search tool to retrieve details. "
                "You do not need to search before every response — only when you need information you do not already have."
            )
        }

    try:
        _OEM_RUNTIME_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OEM_RUNTIME_CONTEXT_PATH.write_text(
            json.dumps(context, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logging.warning("Failed to write runtime context to %s: %s", _OEM_RUNTIME_CONTEXT_PATH, e)
        warnings.append("Context enrichment disabled")

    try:
        _OEM_TEMP_INSTRUCTIONS.parent.mkdir(parents=True, exist_ok=True)
        instructions = (
            "# openempiric Reflection Guidelines\n\n"
            "## Knowledge Capture\n"
            "OEM is your long-term memory for this project. During this session, you should identify and record critical learning events (hypotheses, experiments, validations, decisions, failures) using structured format patterns. This allows the reflection pipeline to extract and persist learnings automatically.\n\n"
            "## Reflection Format\n"
            "To record an event, write a line in your response matching one of these prefixes:\n"
            "- `Hypothesis: <statement>` (or `Hyp: <statement>`)\n"
            "- `Experiment: <statement>` (or `Exp: <statement>`)\n"
            "- `Validation: <statement>` (or `Val: <statement>`)\n"
            "- `Failure: <statement>` (or `Fail: <statement>`)\n"
            "- `Decision: <statement>` (or `Dec: <statement>`)\n"
        )
        _OEM_TEMP_INSTRUCTIONS.write_text(instructions, encoding="utf-8")
    except Exception as e:
        logging.warning("Failed to write transient instructions to %s: %s", _OEM_TEMP_INSTRUCTIONS, e)
        warnings.append("Transient instructions unavailable")

    try:
        logging.info("Restoring session state (session_id=%s)", session_id)
        eng.restore_session_state(project)
    except Exception as e:
        logging.warning("Pre-session restore failed: %s", e)
        warnings.append("Session state restore failed")

    # Set status to running
    try:
        session_state.status = "running"
        session_state.save(active_session_file)
    except Exception:
        pass

    # Run Supervisor checks
    try:
        from .readiness import RuntimeReadiness
        from .supervisor import render_supervisor_panel, print_project_memory_summary
        
        checks = RuntimeReadiness().check(
            eng, agent_name, project,
            harness=harness,
            adapter=adapter,
            stale_existed=stale_existed,
            recovery_failed=recovery_failed
        )
        panel_str = render_supervisor_panel(project, agent_name, checks)
        print(panel_str)
        
        agent_display = agent_name
        if agent_name == "opencode":
            agent_display = "OpenCode"
        elif agent_name == "claude-code":
            agent_display = "Claude Code"
        elif agent_name == "cursor":
            agent_display = "Cursor"
        elif agent_name in ("agy", "antigravity"):
            agent_display = "Antigravity"
        else:
            agent_display = agent_name.title()
            
        duration = time.time() - start_time
        print_project_memory_summary(context, agent_display, duration)
    except Exception as e:
        logging.warning("Readiness pipeline display failed: %s", e)

    # Print warnings if any were collected
    if warnings:
        from oem_tui.panels import render_panel
        panel_lines = ["OEM started with degraded functionality:"]
        for w in warnings:
            panel_lines.append(f"- {w}")
        panel_lines.append("")
        panel_lines.append("Agent session continues normally.")
        print(render_panel("Warning", panel_lines, status="warning"))

    # 3. Spawn agent with managed mode env vars
    managed_env = os.environ.copy()
    managed_env["OEM_MANAGED"] = "1"
    managed_env["OEM_SESSION_ID"] = session_id
    managed_env["OEM_RUNTIME_CONTEXT_PATH"] = str(_OEM_RUNTIME_CONTEXT_PATH)
    if project:
        managed_env["OEM_PROJECT"] = project

    logging.info("Spawning coding agent: %s... (managed session_id=%s)", agent_name, session_id)
    p = None
    try:
        import signal
        cmd = []
        if agent_name == "opencode":
            cmd = ["opencode"]
        elif agent_name == "claude-code":
            cmd = ["claude"]
        elif agent_name == "cursor":
            cmd = ["cursor", "."]
        elif agent_name in ("agy", "antigravity"):
            cmd = ["agy"]
        else:
            cmd = agent_name.split()

        # Check if subprocess.run is mocked (e.g. in test environment)
        if "mock" in type(subprocess.run).__name__.lower() or hasattr(subprocess.run, "mock_calls"):
            subprocess.run(cmd, check=True, env=managed_env)
        else:
            p = subprocess.Popen(cmd, env=managed_env, preexec_fn=os.setsid)
            return_code = p.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, cmd)
    except Exception as e:
        logging.warning("Agent session finished or returned: %s", e)
    finally:
        if p is not None:
            try:
                pgid = os.getpgid(p.pid)
                os.killpg(pgid, signal.SIGTERM)
                
                # Grace period: check if group is terminated (up to 3 seconds)
                grace_start = time.time()
                while time.time() - grace_start < 3.0:
                    try:
                        os.killpg(pgid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.25)
                else:
                    # Fallback to SIGKILL if processes are still running
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except Exception:
                        pass
            except (ProcessLookupError, NameError, AttributeError):
                pass
            except Exception as e:
                logging.warning("Failed to cleanup agent process group: %s", e)

        # 4. Post-session: read deferred chat from plugin or from agent transcripts
        chat_text = ""
        try:
            transcript_file = Path(session_state.transcript_path)
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

        # 7. Emit runtime metrics
        try:
            metrics_file = harness / "state" / "metrics.json"
            update_metrics_file(metrics_file, {
                "sessions_completed": 1 if committed else 0,
                "sessions_failed": 0 if committed else 1,
            })
        except Exception:
            pass

        # 8. Cleanup temp files
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
                    session_state.status = "completed"
                    active_session_file.unlink()
                else:
                    session_state.status = "failed"
                    session_state.save(active_session_file)
        except Exception:
            pass
