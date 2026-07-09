from __future__ import annotations

import argparse


class _OEMArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        cmd = getattr(parsed, "command", None)
        aw_action = getattr(parsed, "active_work_action", None)
        if cmd in ("clean", "recover") or (cmd == "active-work" and aw_action == "repair"):
            if getattr(parsed, "dry_run", False) and getattr(parsed, "apply", False):
                self.error("--dry-run and --apply cannot be used together")
            if getattr(parsed, "backup", None) is False and not getattr(
                parsed, "apply", False
            ):
                self.error("--no-backup is only valid with --apply")
        return parsed


try:
    from importlib.metadata import version as _pkg_version

    _VERSION = _pkg_version("oem-knowledge")
except Exception:
    _VERSION = "1.0.1"


def _resolve_project(args) -> str | None:
    """Normalise --project: ``""`` or ``"."`` → ``None`` (cwd)."""
    raw = getattr(args, "project", None)
    if raw and raw != ".":
        return raw
    return None


def _setup_parser() -> argparse.ArgumentParser:
    parser = _OEMArgumentParser(description="OpenEmpiric (oem) CLI")
    parser.add_argument(
        "--version",
        action="version",
        version=f"oem {_VERSION}",
        help="Show version and exit",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help=argparse.SUPPRESS)
    sub.add_parser("stats", help=argparse.SUPPRESS)

    clean_p = sub.add_parser(
        "clean", help="[User] Analyze or apply safe OEM cleanup actions"
    )
    clean_p.add_argument(
        "--dry-run", action="store_true", help="Analyze only; do not mutate"
    )
    clean_p.add_argument(
        "--apply", action="store_true", help="Apply safe cleanup actions"
    )
    clean_p.add_argument(
        "--scope",
        choices=[
            "self-ingestion",
            "duplicates",
            "structure",
            "registry",
            "legacy",
            "all",
        ],
        default="all",
    )
    clean_p.add_argument(
        "--backup",
        dest="backup",
        action="store_true",
        default=None,
        help="Create a backup before applying cleanups",
    )
    clean_p.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Apply cleanups without creating a backup",
    )
    clean_p.add_argument("--project", type=str, default="")

    init_p = sub.add_parser("init", help=argparse.SUPPRESS)
    init_p.add_argument("project", type=str, nargs="?", default=".")

    search_p = sub.add_parser("search", help="[User] Search the project knowledge base")
    search_p.add_argument("query", type=str)
    search_p.add_argument("--k", type=int, default=3)
    search_p.add_argument("--project", type=str, default="")
    search_p.add_argument("--debug-ranking", action="store_true", help="Show raw/reranked candidate breakdown with identity fields and failure classification")

    source_p = sub.add_parser("source", help="[User] Search and read the project source corpus")
    source_sub = source_p.add_subparsers(dest="source_command", required=True, metavar="SOURCE_COMMAND")

    source_index_p = source_sub.add_parser("index", help="Build the project source corpus index")
    source_index_p.add_argument("--force", action="store_true", help="Re-index all eligible source files")
    source_index_p.add_argument("--dry-run", action="store_true", help="Discover and count without writing files")
    source_index_p.add_argument("--project", type=str, default="")

    source_search_p = source_sub.add_parser("search", help="Search indexed project source files")
    source_search_p.add_argument("query", type=str)
    source_search_p.add_argument("--k", type=int, default=5)
    source_search_p.add_argument("--project", type=str, default="")

    source_read_p = source_sub.add_parser("read", help="Read an exact project source file range")
    source_read_p.add_argument("path", type=str)
    source_read_p.add_argument("--start-line", type=int, default=None)
    source_read_p.add_argument("--end-line", type=int, default=None)
    source_read_p.add_argument("--project", type=str, default="")

    source_stats_p = source_sub.add_parser("stats", help="Show source corpus index statistics")
    source_stats_p.add_argument("--project", type=str, default="")

    read_p = sub.add_parser("read", help="[User] Read the project memory baseline")
    read_p.add_argument(
        "--scope",
        choices=["project", "recent", "skills", "health"],
        default="project",
        help="Scope of memory to read (default: project)",
    )
    read_p.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max items per section (default: 10)",
    )
    read_p.add_argument("--project", type=str, default="")

    preflight_p = sub.add_parser(
        "preflight",
        help="[User] Run deterministic OEM preflight before non-trivial planning",
    )
    preflight_p.add_argument("task", type=str)
    preflight_p.add_argument("--project", type=str, default="")
    preflight_p.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Max items per category (default: 8, clamped to 1..20)",
    )
    preflight_p.add_argument(
        "--json",
        action="store_true",
        help="Print the normalized preflight payload as JSON",
    )
    preflight_p.add_argument(
        "--no-audit",
        action="store_true",
        help="Do not append .oem/preflight/preflight_events.jsonl",
    )

    index_p = sub.add_parser("index", help="[Advanced] Rebuild derived search index for the project")
    index_p.add_argument("--project", type=str, default="")

    rebuild_p = sub.add_parser(
        "rebuild",
        help="[Advanced] Replay the event store to rebuild the concept registry",
    )
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
    explain_p.add_argument(
        "--history", action="store_true", help="Show revision history"
    )
    explain_p.add_argument("--project", type=str, default="")

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

    merge_p = sub.add_parser(
        "merge",
        help="[Advanced] Merge two duplicate/overlapping registry concepts together",
    )
    merge_p.add_argument("primary_id", type=str)
    merge_p.add_argument("secondary_id", type=str)
    merge_p.add_argument("--auto", action="store_true", help="Automatically merge")
    merge_p.add_argument("--project", type=str, default="")

    session_start_p = sub.add_parser(
        "session-start",
        help="[Internal] Restore pre-injection context and prepare workspace before agent run",
    )
    session_start_p.add_argument("--project", type=str, default="")

    reflect_p = sub.add_parser(
        "reflect",
        help="[Advanced] Dry-run session transcript reflection and concept extraction",
    )
    reflect_p.add_argument("--chat", type=str, default="")
    reflect_p.add_argument(
        "--debug", action="store_true", help="Show detailed extraction breakdown"
    )
    reflect_p.add_argument("--project", type=str, default="")

    session_end_p = sub.add_parser(
        "session-end",
        help="[Internal] Finalize context, run extraction, and commit learnings after agent exits",
    )
    session_end_p.add_argument("--project", type=str, default="")
    session_end_p.add_argument("--chat", type=str, default="")
    session_end_p.add_argument("--session-id", type=str, default="")
    session_end_p.add_argument(
        "--verbose", action="store_true", help="Show detailed reflection analysis"
    )
    session_end_p.add_argument(
        "--no-index", action="store_true", help="Skip search indexing during session-end"
    )
    session_end_p.add_argument(
        "--index-budget-seconds",
        type=float,
        default=None,
        help="Search indexing time budget in seconds (0 to skip)",
    )

    session_status_p = sub.add_parser("session-status", help=argparse.SUPPRESS)
    session_status_p.add_argument("--project", type=str, default="")

    run_p = sub.add_parser(
        "run",
        help="[User] Run a managed coding agent session with dynamic config injection",
    )
    run_p.add_argument(
        "agent", type=str, help="opencode, grok, claude-code, cursor, or custom command"
    )
    run_p.add_argument("--project", type=str, default="")
    run_p.add_argument(
        "--init-if-missing",
        action="store_true",
        help="Automatically initialize OEM project memory if missing",
    )
    run_p.add_argument(
        "--no-init",
        action="store_true",
        help="Fail if OEM project memory is missing instead of prompting",
    )
    run_p.add_argument(
        "--skip-doctor",
        action="store_true",
        help="Skip executing workspace doctor checks before starting",
    )
    run_p.add_argument(
        "--skip-session-start",
        action="store_true",
        help="Skip starting a session and preparing workspace context",
    )
    run_p.add_argument(
        "--skip-session-end",
        action="store_true",
        help="Skip committing learnings and closing the session on exit",
    )
    run_p.add_argument(
        "--print-instructions",
        action="store_true",
        help="Print persistent guidelines to stdout and exit",
    )

    recover_p = sub.add_parser(
        "recover",
        help="[Internal] Recover, commit, or abort crashed or unfinished agent sessions",
    )
    recover_p.add_argument("--project", type=str, default="")
    recover_p.add_argument(
        "--abort", action="store_true", help="Abort/discard the unfinished session"
    )
    recover_p.add_argument(
        "--status", action="store_true", help="Print current active session status"
    )
    recover_p.add_argument(
        "--scope",
        choices=["reflection", "registry"],
        default=None,
        help="Scope of recovery"
    )
    recover_p.add_argument(
        "--dry-run", action="store_true", help="Analyze only; do not mutate"
    )
    recover_p.add_argument(
        "--apply", action="store_true", help="Apply safe recovery actions"
    )
    recover_p.add_argument(
        "--backup",
        dest="backup",
        action="store_true",
        default=None,
        help="Create a backup before applying repairs",
    )
    recover_p.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Apply repairs without creating a backup",
    )
    recover_p.add_argument(
        "--rebuild-reports",
        action="store_true",
        help="Explicitly rebuild session report files",
    )

    runtime_summary_p = sub.add_parser("runtime-summary", help=argparse.SUPPRESS)
    runtime_summary_p.add_argument("--days", type=int, default=7)
    runtime_summary_p.add_argument("--project", type=str, default="")

    metrics_p = sub.add_parser("metrics", help=argparse.SUPPRESS)
    metrics_p.add_argument("--project", type=str, default="")
    metrics_p.add_argument(
        "--reset", action="store_true", help="Reset all metrics to default"
    )
    metrics_p.add_argument(
        "--export", type=str, help="Export raw metrics JSON to file path"
    )
    metrics_p.add_argument(
        "--usage-log",
        type=int,
        nargs="?",
        const=10,
        help="Print recent entries from usage_log.jsonl (default 10)",
    )
    metrics_p.add_argument(
        "--report", action="store_true", help="Report concept usage and decisions"
    )
    metrics_p.add_argument(
        "--used", type=str, default="[]", help="JSON array of referenced concept IDs"
    )
    metrics_p.add_argument(
        "--ignored", type=str, default="[]", help="JSON array of ignored concept IDs"
    )
    metrics_p.add_argument(
        "--decisions", type=str, default="[]", help="JSON array of decisions aligned"
    )

    todo_p = sub.add_parser("todo", help=argparse.SUPPRESS)

    outcome_p = sub.add_parser(
        "outcome",
        help="[Internal] Record manual session outcome status, concepts, and goal satisfaction",
    )
    outcome_p.add_argument("status", choices=["success", "failure", "abandoned"])
    outcome_p.add_argument("referenced_concepts", type=str, nargs="*", default=[])
    outcome_p.add_argument("--reason", type=str, default="")
    outcome_p.add_argument("--session-id", type=str, default="")
    outcome_p.add_argument("--project", type=str, default="")
    outcome_p.add_argument(
        "--goal-satisfaction",
        type=float,
        default=None,
        help="Goal satisfaction rating (0.0 to 1.0)",
    )

    health_p = sub.add_parser(
        "health",
        help="[User] Scan the workspace for stale concepts, duplicates, and contradicting knowledge",
    )
    health_p.add_argument("--project", type=str, default="")
    health_p.add_argument(
        "--stale-sessions",
        type=int,
        default=5,
        help="Number of sessions to check for staleness",
    )
    health_p.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.85,
        help="Similarity threshold for duplicates",
    )
    health_p.add_argument(
        "--json",
        action="store_true",
        help="Print the health report as JSON",
    )

    # active-work repair (smallest safe repair for stale handoff/runtime conflicts)
    aw_p = sub.add_parser(
        "active-work",
        help="[User] Inspect or repair active-work metadata consistency (handoff vs runtime)",
    )
    aw_sub = aw_p.add_subparsers(dest="active_work_action", required=True, metavar="ACTION")
    aw_repair_p = aw_sub.add_parser(
        "repair",
        help="Repair stale active-work signals (session-handoff.json, session-handoff.md, runtime/context.md)",
    )
    aw_repair_p.add_argument("--project", type=str, default="")
    aw_repair_p.add_argument("--dry-run", action="store_true", help="Analyze only; do not mutate")
    aw_repair_p.add_argument("--apply", action="store_true", help="Apply safe repairs")
    aw_repair_p.add_argument(
        "--backup",
        dest="backup",
        action="store_true",
        default=None,
        help="Create a backup before applying repairs",
    )
    aw_repair_p.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Apply repairs without creating a backup",
    )

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

    doctor_p = sub.add_parser(
        "doctor", help="[User] Check workspace health, plugin links, and warmer status"
    )
    doctor_sub = doctor_p.add_subparsers(dest="doctor_target")
    doctor_sub.add_parser(
        "opencode", help="Diagnose OpenCode Desktop Windows/WSL MCP setup"
    ).add_argument("--wsl-distro", type=str, default=None, help="Explicit WSL distro name")
    doctor_p.add_argument("--project", type=str, default="")
    doctor_p.add_argument(
        "--fix", action="store_true", help="Automatically repair safe doctor findings"
    )

    warmup_p = sub.add_parser("warmup", help=argparse.SUPPRESS)
    warmup_p.add_argument("--project", type=str, default="")

    setup_p = sub.add_parser(
        "setup",
        help="[User] Configure and register agent workstation-level integration (opencode, grok, codex-app)",
    )
    setup_sub = setup_p.add_subparsers(dest="setup_target", required=True)
    setup_opencode = setup_sub.add_parser(
        "opencode", help="Integrate OpenCode workspace settings and plugins"
    )
    setup_opencode.add_argument(
        "--repair",
        action="store_true",
        help="Forcefully overwrite and recreate all integration files",
    )
    setup_opencode.add_argument(
        "--dry-run",
        action="store_true",
        help="Diagnose and recommend without mutating config",
    )
    setup_opencode.add_argument(
        "--wsl-distro", type=str, default=None,
        help="Explicit WSL distro name (required when multiple distros exist)",
    )
    setup_codex = setup_sub.add_parser(
        "codex-app", help="Integrate Codex App with OEM through the WSL bridge"
    )
    setup_codex.add_argument(
        "--repair",
        action="store_true",
        help="Forcefully overwrite and recreate OEM-owned Codex integration files",
    )
    setup_grok = setup_sub.add_parser(
        "grok", help="Integrate Grok (xAI) with OEM via MCP + project rules"
    )
    setup_grok.add_argument(
        "--repair",
        action="store_true",
        help="Forcefully overwrite and recreate OEM Grok integration files",
    )

    migrate_p = sub.add_parser(
        "migrate", help=argparse.SUPPRESS
    )
    migrate_p.add_argument("--project", type=str, default="")

    config_p = sub.add_parser(
        "config", help="[User] View or set configuration parameters"
    )
    config_sub = config_p.add_subparsers(dest="config_target", required=True)
    config_retrieval = config_sub.add_parser(
        "retrieval", help="View or set the retrieval mode"
    )
    config_retrieval.add_argument(
        "mode",
        nargs="?",
        choices=["auto", "bm25", "hybrid"],
        help="Retrieval mode to set",
    )
    config_retrieval.add_argument("--project", type=str, default="")

    sub.add_parser("mcp", help="Start the MCP tool server")

    skills_p = sub.add_parser("skills", help="[User] Review and promote skill candidates")
    skills_sub = skills_p.add_subparsers(dest="skills_action", required=True)

    skills_list = skills_sub.add_parser("list", help="List all skill candidates")
    skills_list.add_argument("--project", type=str, default="")

    skills_show = skills_sub.add_parser("show", help="Show detailed candidate or approved skill")
    skills_show.add_argument("slug", type=str)
    skills_show.add_argument("--project", type=str, default="")

    skills_suggest = skills_sub.add_parser("suggest", help="Suggest new candidates by scanning memory")
    skills_suggest.add_argument("--project", type=str, default="")

    skills_approve = skills_sub.add_parser("approve", help="Approve candidate and promote to project skill")
    skills_approve.add_argument("slug", type=str)
    skills_approve.add_argument("--force", action="store_true", help="Force approval even if rejected before")
    skills_approve.add_argument("--project", type=str, default="")

    skills_reject = skills_sub.add_parser("reject", help="Reject skill candidate")
    skills_reject.add_argument("slug", type=str)
    skills_reject.add_argument("--project", type=str, default="")

    skills_defer = skills_sub.add_parser("defer", help="Defer skill candidate")
    skills_defer.add_argument("slug", type=str)
    skills_defer.add_argument("--project", type=str, default="")

    skills_edit = skills_sub.add_parser("edit", help="Edit a skill candidate's details")
    skills_edit.add_argument("slug", type=str)
    skills_edit.add_argument("--title", type=str, default=None)
    skills_edit.add_argument("--trigger", type=str, default=None)
    skills_edit.add_argument("--behavior", type=str, default=None)
    skills_edit.add_argument("--project", type=str, default="")

    instructions_p = sub.add_parser("instructions", help="[User] Review and manage instruction directives")
    instructions_sub = instructions_p.add_subparsers(dest="instructions_action", required=True)

    inst_index = instructions_sub.add_parser("index", help="Index project instruction files")
    inst_index.add_argument("--project", type=str, default="")

    inst_list = instructions_sub.add_parser("list", help="List active directives")
    inst_list.add_argument("--project", type=str, default="")

    inst_doctor = instructions_sub.add_parser("doctor", help="Check instructions health status")
    inst_doctor.add_argument("--project", type=str, default="")

    inst_candidates = instructions_sub.add_parser("candidates", help="List instruction update candidates")
    inst_candidates.add_argument("--project", type=str, default="")

    working_set_p = sub.add_parser("working-set", help="[User] Inspect runtime working set state")
    working_set_sub = working_set_p.add_subparsers(dest="working_set_action", required=True)
    working_set_show_p = working_set_sub.add_parser("show", help="Show current working set state")
    working_set_show_p.add_argument("--project", type=str, default="")
    working_set_show_p.add_argument("--json", action="store_true", help="Output in raw JSON format")

    sub._choices_actions = [
        a for a in sub._choices_actions if a.help is not argparse.SUPPRESS
    ]
    return parser
