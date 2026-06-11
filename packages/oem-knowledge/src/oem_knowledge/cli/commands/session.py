from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from oem_knowledge.ui import render_panel


def run_session_command(args):
    import sys
    from oem_knowledge.fs import LockTimeoutError
    try:
        _run_session_command_impl(args)
    except LockTimeoutError as e:
        from oem_knowledge.ui import render_panel
        print(render_panel(
            "Lock Acquisition Failure",
            [
                "OEM could not acquire the project memory lock.",
                f"Reason: {e}",
                "Another OEM process may still be committing memory. Please retry.",
            ],
            status="error"
        ))
        sys.exit(1)

def _run_session_command_impl(args):
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
    import atexit; atexit.register(eng.close)

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
        except Exception as e:
            logging.debug(f"Could not load active session state: {e}")

        no_index = getattr(args, "no_index", False)
        index_budget = getattr(args, "index_budget_seconds", None)

        if no_index and index_budget is not None:
            print(render_panel(
                "Invalid Arguments",
                ["Cannot specify both --no-index and --index-budget-seconds."],
                status="error"
            ))
            sys.exit(1)

        if index_budget is not None and index_budget < 0:
            print(render_panel(
                "Invalid Arguments",
                ["--index-budget-seconds must be non-negative."],
                status="error"
            ))
            sys.exit(1)

        final_budget = 10.0
        if no_index:
            final_budget = 0.0
        elif index_budget is not None:
            final_budget = index_budget

        def progress_callback(phase_name: str):
            if getattr(args, "verbose", False):
                print(f"[session] {phase_name}...")
                sys.stdout.flush()

        commit_start = time.time()
        res = eng.session_commit(
            project,
            args.chat,
            args.session_id,
            session_started_at=session_started_at,
            update_index=not no_index,
            index_budget_seconds=final_budget,
            progress_callback=progress_callback
        )
        commit_duration = time.time() - commit_start

        if getattr(args, "verbose", False):
            if no_index:
                print(f"[session] search_index skipped after 0.00s budget")
            elif res.get("status") == "partial" and res.get("failed_step") == "search_index":
                print(f"[session] search_index skipped after {final_budget:.2f}s budget")
            timing_total = res.get("phase_timings", {}).get("total", commit_duration)
            print(f"[session] done in {timing_total:.2f}s")
            if res.get("status") == "partial" and res.get("failed_step") == "search_index":
                print(f"[session] partial success: canonical memory saved; search index needs rebuild")
            sys.stdout.flush()

        if res.get("status") == "error":
            print(render_panel(
                "Session End Failure",
                [
                    f"Failed step: {res.get('failed_step', 'unknown')}",
                    f"Reason: {res.get('message', 'Unknown error')}",
                ],
                status="error"
            ))
            sys.exit(1)

        if res.get("status") == "empty":
            print(render_panel(
                "Session End Complete",
                [
                    "No extractable knowledge events found.",
                    res.get("suggestion") or "Use explicit markers or pass structured events."
                ],
                status="info"
            ))
            sys.exit(0)

        if res.get("status") == "partial" and res.get("failed_step") == "llm_extraction":
            print(render_panel(
                "Session End Timeout",
                [
                    res.get("message", "LLM extraction timed out. No events were written."),
                    res.get("suggestion") or "Retry with structured events or Observation:/Decision:/Outcome: markers."
                ],
                status="error"
            ))
            sys.exit(1)

        if res.get("status") == "partial" and not getattr(args, "verbose", False):
            print("Session end: partial success")
            print("Canonical memory saved.")
            if res.get("failed_step") == "search_index":
                print("Search indexing skipped after budget.")
                proj_path = project or "."
                print(f"Run `oem index --project {proj_path}`.")
            else:
                for w in res.get("warnings", []):
                    print(f"Warning: {w}")

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
                file_observations=exp.get("file_observations", 0),
                index_stats=res.get("index_stats"),
                retrieval_mode=eng.search.resolve_retrieval_mode()
            )
        )
        if res.get("notification"):
            print()
            print(res["notification"])
        if args.verbose and "explainability" in res:
            exp = res["explainability"]
            debug_lines = [
                f"Chat Lines Processed:   {exp.get('chat_lines_processed', 0)}",
                f"Structured Events:      {exp.get('structured_events_found', 0)}",
                f"Fallback Extraction:    {'Yes' if exp.get('fallback_extraction_used') else 'No'}",
                f"File Observations:      {exp.get('file_observations_count', 0)}",
                f"Excluded OEM-generated files: {exp.get('excluded_oem_generated_files', 0)}",
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
                f"Excluded OEM-generated files: {exp.get('excluded_oem_generated_files', 0)}",
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
                except Exception as e:
                    logging.warning(f"Failed to load session state details from {session_state_file}: {e}")

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
        scope = getattr(args, "scope", None)
        dry_run = getattr(args, "dry_run", False)
        apply = getattr(args, "apply", False)
        backup = getattr(args, "backup", None)
        cmd_recover(
            eng,
            project,
            abort=args.abort,
            status=args.status,
            scope=scope,
            dry_run=dry_run,
            apply=apply,
            backup=backup,
        )
