from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from oem_knowledge.ui import render_panel


def run_session_command(args):
    # Setup deferred logging Configuration
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    # Lazy-load to avoid eager framework imports on help/version path
    from oem_knowledge.engine import KnowledgeEngine
    from oem_knowledge.runtime import SessionState, cmd_recover, run_agent

    eng = KnowledgeEngine(project)

    if args.command == "session-start":
        res = eng.restore_session_state(project)
        lines = [
            f"Goals: {len(res.get('active_goals', []))}",
            f"Blockers: {len(res.get('blockers', []))}",
            f"Files: {len(res.get('recommended_files', []))}",
            f"Global Concepts: {len(res.get('global_concepts', []))}",
        ]
        print(render_panel("Session Start", lines, status="restore"))

    elif args.command == "session-end":
        session_started_at = None
        try:
            harness = eng._resolve_harness(project)
            active_session_file = harness / "state" / "active_session.json"
            session_state = SessionState.load(active_session_file)
            if session_state:
                session_started_at = session_state.started_at
        except Exception:
            pass

        commit_start = time.time()
        res = eng.session_commit(
            project,
            args.chat,
            args.session_id,
            session_started_at=session_started_at
        )
        commit_duration = time.time() - commit_start

        from oem_knowledge.runtime.supervisor import render_commit_complete_panel
        report_name = Path(res['report_path']).name
        concepts_count = len(res.get('materialized_log', []))
        exp = res.get("explainability", {})
        obs_count = exp.get("file_observations", 0)

        print(
            render_commit_complete_panel(
                report_name=report_name,
                concepts_count=concepts_count,
                observations_count=obs_count,
                duration=commit_duration,
                structured_events=exp.get("structured_events", 0),
                fallback_concepts=exp.get("fallback_extractions", 0),
                file_observations=exp.get("file_observations", 0)
            )
        )
        if args.verbose and "explainability" in res:
            exp = res["explainability"]
            debug_lines = [
                f"Chat Lines Processed:   {exp.get('chat_lines_processed', 0)}",
                f"Structured Events:      {exp.get('structured_events_found', 0)}",
                f"Fallback Extraction:    {'Yes' if exp.get('fallback_extraction_used') else 'No'}",
                f"File Observations:      {exp.get('file_observations_count', 0)}",
            ]
            generated = exp.get("generated_concepts", [])
            if generated:
                debug_lines.append("")
                debug_lines.append("Generated Concepts:")
                for gc in generated:
                    debug_lines.append(f"  \u2022 {gc}")
            print(render_panel("Reflection Analysis", debug_lines, status="info"))

    elif args.command == "reflect":
        from oem_knowledge.services.reflection import ReflectionService
        rs = ReflectionService(eng)
        res = rs.reflect_session(project, args.chat)
        if args.debug and "explainability" in res:
            exp = res["explainability"]
            debug_lines = [
                f"Chat Lines Processed:   {exp.get('chat_lines_processed', 0)}",
                f"Structured Events:      {exp.get('structured_events_found', 0)}",
                f"Fallback Extraction:    {'Yes' if exp.get('fallback_extraction_used') else 'No'}",
                f"File Observations:      {exp.get('file_observations_count', 0)}",
            ]
            generated = exp.get("generated_concepts", [])
            file_obs = [
                f"Modified: {e['concept_candidates'][0]}"
                for e in res.get("canonical_events", [])
                if e.get("source") == "diff"
            ]
            if generated:
                debug_lines.append("")
                debug_lines.append("Generated Concepts:")
                for gc in generated:
                    debug_lines.append(f"  \u2022 {gc}")
            if file_obs:
                debug_lines.append("")
                debug_lines.append("File Observations:")
                for fo in file_obs:
                    debug_lines.append(f"  \u2022 {fo}")
            print(render_panel("Reflection Analysis", debug_lines, status="info"))
        else:
            events = res.get("knowledge_events", [])
            lines = [f"Total events: {len(events)}"]
            for ev in events[:10]:
                lines.append(f"  [{ev['type'].upper()}] {ev['concept'][:60]}")
            print(render_panel("Reflection Result", lines, status="ok"))

    elif args.command == "session-status":
        harness = eng._resolve_harness(project)
        active_session_file = harness / "state" / "active_session.json"
        session_state = SessionState.load(active_session_file)

        if not session_state:
            print(render_panel("Session Status", ["No active session found."], status="info"))
        else:
            import datetime
            started_str = datetime.datetime.fromtimestamp(session_state.started_at).isoformat() if session_state.started_at else "unknown"

            # Check context injection
            context_exists = False
            if session_state.context_path:
                context_exists = Path(session_state.context_path).exists()

            # Read knowledge injection count from session_state.json
            session_state_file = harness / "state" / "session_state.json"
            injected_count = 0
            if session_state_file.exists():
                try:
                    sdata = json.loads(session_state_file.read_text(encoding="utf-8"))
                    injected_count = len(sdata.get("last_injected_concepts", []))
                except Exception:
                    pass

            # Determine reflection/materialization/outcome status
            is_running = session_state.status in ("started", "running")
            reflection_status = "Pending" if is_running else "Complete"
            materialization_status = "Pending" if is_running else "Complete"
            outcome_status = "Not Recorded" if is_running else "Recorded"

            lines = [
                f"Session ID:      {session_state.session_id}",
                f"State:           {session_state.status}",
                f"Agent:           {session_state.agent}",
                f"Started At:      {started_str}",
                f"Project:         {session_state.project}",
                "",
                f"Context Injection: {'✓' if context_exists else '✗'}",
                f"Knowledge Retrieved: {injected_count}",
                f"Reflection:      {reflection_status}",
                f"Materialization: {materialization_status}",
                f"Outcome:         {outcome_status}",
            ]
            print(render_panel("Session Status", lines, status="stats"))

    elif args.command == "run":
        run_agent(args.agent, eng, project)

    elif args.command == "recover":
        cmd_recover(eng, project, abort=args.abort, status=args.status)
