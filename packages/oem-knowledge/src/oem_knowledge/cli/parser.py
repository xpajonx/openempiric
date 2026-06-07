from __future__ import annotations

import argparse

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("oem-knowledge")
except Exception:
    _VERSION = "0.97"


def _resolve_project(args) -> str | None:
    """Normalise --project: ``""`` or ``"."`` → ``None`` (cwd)."""
    raw = getattr(args, "project", None)
    if raw and raw != ".":
        return raw
    return None


def _setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenEmpiric (oem) CLI")
    parser.add_argument("--version", action="version", version=f"oem {_VERSION}", help="Show version and exit")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help=argparse.SUPPRESS)
    sub.add_parser("stats", help=argparse.SUPPRESS)

    init_p = sub.add_parser("init", help=argparse.SUPPRESS)
    init_p.add_argument("project", type=str, nargs="?", default=".")

    search_p = sub.add_parser("search", help="[User] Search the project knowledge base")
    search_p.add_argument("query", type=str)
    search_p.add_argument("--k", type=int, default=3)
    search_p.add_argument("--project", type=str, default="")

    rebuild_p = sub.add_parser("rebuild", help="[Advanced] Replay the event store to rebuild the concept registry")
    rebuild_p.add_argument("--project", type=str, default="")

    events_p = sub.add_parser("events", help=argparse.SUPPRESS)
    events_p.add_argument("--project", type=str, default="")
    events_p.add_argument("--concept", type=str, default="")
    events_p.add_argument("--type", type=str, default="")
    events_p.add_argument("--session-id", type=str, default="")

    event_p = sub.add_parser("event", help=argparse.SUPPRESS)
    event_p.add_argument("event_id", type=str)
    event_p.add_argument("--project", type=str, default="")

    explain_p = sub.add_parser("explain", help=argparse.SUPPRESS)
    explain_p.add_argument("type", choices=["concept", "event"])
    explain_p.add_argument("id", type=str)
    explain_p.add_argument("--history", action="store_true", help="Show revision history")
    explain_p.add_argument("--project", type=str, default="")

    vault_p = sub.add_parser("vault", help=argparse.SUPPRESS)
    vault_p.add_argument("action", choices=["sync", "candidates", "promote", "demote"])
    vault_p.add_argument("concept_id", type=str, nargs="?", default="")
    vault_p.add_argument("--project", type=str, default="")

    identity_p = sub.add_parser("identity", help=argparse.SUPPRESS)
    identity_p.add_argument("action", choices=["scan", "review"])
    identity_p.add_argument("concept_a", type=str, nargs="?", default="")
    identity_p.add_argument("concept_b", type=str, nargs="?", default="")
    identity_p.add_argument("--project", type=str, default="")

    concept_p = sub.add_parser("concept", help=argparse.SUPPRESS)
    concept_p.add_argument("action", choices=["evolve", "health", "fitness"])
    concept_p.add_argument("concept_id", type=str, nargs="?", default="")
    concept_p.add_argument("--format", choices=["text", "yaml", "json"], default="text")
    concept_p.add_argument("--project", type=str, default="")

    contradictions_p = sub.add_parser("contradictions", help=argparse.SUPPRESS)
    contradictions_p.add_argument("--project", type=str, default="")

    merge_p = sub.add_parser("merge", help="[Advanced] Merge two duplicate/overlapping registry concepts together")
    merge_p.add_argument("primary_id", type=str)
    merge_p.add_argument("secondary_id", type=str)
    merge_p.add_argument("--auto", action="store_true", help="Automatically merge")
    merge_p.add_argument("--project", type=str, default="")

    lint_p = sub.add_parser("lint", help=argparse.SUPPRESS)
    lint_p.add_argument("--project", type=str, default="")
    lint_p.add_argument("--workers", type=int, default=4)
    lint_p.add_argument("--fix", action="store_true", help="Automatically heal links")

    session_start_p = sub.add_parser("session-start", help="[Internal] Restore pre-injection context and prepare workspace before agent run")
    session_start_p.add_argument("--project", type=str, default="")

    reflect_p = sub.add_parser("reflect", help="[Advanced] Dry-run session transcript reflection and concept extraction")
    reflect_p.add_argument("--chat", type=str, default="")
    reflect_p.add_argument("--debug", action="store_true", help="Show detailed extraction breakdown")
    reflect_p.add_argument("--project", type=str, default="")

    session_end_p = sub.add_parser("session-end", help="[Internal] Finalize context, run extraction, and commit learnings after agent exits")
    session_end_p.add_argument("--project", type=str, default="")
    session_end_p.add_argument("--chat", type=str, default="")
    session_end_p.add_argument("--session-id", type=str, default="")
    session_end_p.add_argument("--verbose", action="store_true", help="Show detailed reflection analysis")

    session_status_p = sub.add_parser("session-status", help=argparse.SUPPRESS)
    session_status_p.add_argument("--project", type=str, default="")

    run_p = sub.add_parser("run", help="[User] Run a managed coding agent session with dynamic config injection")
    run_p.add_argument("agent", type=str, help="opencode, claude-code, cursor, or custom command")
    run_p.add_argument("--project", type=str, default="")

    recover_p = sub.add_parser("recover", help="[Internal] Recover, commit, or abort crashed or unfinished agent sessions")
    recover_p.add_argument("--project", type=str, default="")
    recover_p.add_argument("--abort", action="store_true", help="Abort/discard the unfinished session")
    recover_p.add_argument("--status", action="store_true", help="Print current active session status")

    runtime_summary_p = sub.add_parser("runtime-summary", help=argparse.SUPPRESS)
    runtime_summary_p.add_argument("--days", type=int, default=7)
    runtime_summary_p.add_argument("--project", type=str, default="")

    metrics_p = sub.add_parser("metrics", help=argparse.SUPPRESS)
    metrics_p.add_argument("--project", type=str, default="")
    metrics_p.add_argument("--reset", action="store_true", help="Reset all metrics to default")
    metrics_p.add_argument("--export", type=str, help="Export raw metrics JSON to file path")
    metrics_p.add_argument("--usage-log", type=int, nargs="?", const=10, help="Print recent entries from usage_log.jsonl (default 10)")
    metrics_p.add_argument("--report", action="store_true", help="Report concept usage and decisions")
    metrics_p.add_argument("--used", type=str, default="[]", help="JSON array of referenced concept IDs")
    metrics_p.add_argument("--ignored", type=str, default="[]", help="JSON array of ignored concept IDs")
    metrics_p.add_argument("--decisions", type=str, default="[]", help="JSON array of decisions aligned")

    todo_p = sub.add_parser("todo", help=argparse.SUPPRESS)
    
    outcome_p = sub.add_parser("outcome", help="[Internal] Record manual session outcome status, concepts, and goal satisfaction")
    outcome_p.add_argument("status", choices=["success", "failure", "abandoned"])
    outcome_p.add_argument("referenced_concepts", type=str, nargs="*", default=[])
    outcome_p.add_argument("--reason", type=str, default="")
    outcome_p.add_argument("--session-id", type=str, default="")
    outcome_p.add_argument("--project", type=str, default="")
    outcome_p.add_argument("--goal-satisfaction", type=float, default=None, help="Goal satisfaction rating (0.0 to 1.0)")

    health_p = sub.add_parser("health", help="[User] Scan the workspace for stale concepts, duplicates, and contradicting knowledge")
    health_p.add_argument("--project", type=str, default="")
    health_p.add_argument("--stale-sessions", type=int, default=5, help="Number of sessions to check for staleness")
    health_p.add_argument("--similarity-threshold", type=float, default=0.85, help="Similarity threshold for duplicates")

    todo_sub = todo_p.add_subparsers(dest="todo_action", required=True)

    todo_read_p = todo_sub.add_parser("read")
    todo_read_p.add_argument("--project", type=str, default="")

    todo_write_p = todo_sub.add_parser("write")
    todo_write_p.add_argument("items", type=str)
    todo_write_p.add_argument("--project", type=str, default="")

    todo_advance_p = todo_sub.add_parser("advance")
    todo_advance_p.add_argument("item_id", type=str)
    todo_advance_p.add_argument("--status", type=str, default="")
    todo_advance_p.add_argument("--project", type=str, default="")

    doctor_p = sub.add_parser("doctor", help="[User] Check workspace health, plugin links, and warmer status")
    doctor_p.add_argument("--project", type=str, default="")

    warmup_p = sub.add_parser("warmup", help=argparse.SUPPRESS)
    warmup_p.add_argument("--project", type=str, default="")

    setup_p = sub.add_parser("setup", help="[User] Configure and register OpenCode agent workstation-level integration")
    setup_sub = setup_p.add_subparsers(dest="setup_target", required=True)
    setup_opencode = setup_sub.add_parser("opencode", help="Integrate OpenCode workspace settings and plugins")
    setup_opencode.add_argument("--repair", action="store_true", help="Forcefully overwrite and recreate all integration files")

    migrate_p = sub.add_parser("migrate", help="Migrate legacy .harness directory to .oem format")
    migrate_p.add_argument("--project", type=str, default="")

    config_p = sub.add_parser("config", help="[User] View or set configuration parameters")
    config_sub = config_p.add_subparsers(dest="config_target", required=True)
    config_retrieval = config_sub.add_parser("retrieval", help="View or set the retrieval mode")
    config_retrieval.add_argument("mode", nargs="?", choices=["auto", "bm25", "hybrid"], help="Retrieval mode to set")
    config_retrieval.add_argument("--project", type=str, default="")

    sub.add_parser("mcp", help="Start the MCP tool server")

    sub._choices_actions = [a for a in sub._choices_actions if a.help is not argparse.SUPPRESS]
    return parser
