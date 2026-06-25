from __future__ import annotations

import sys


def main():
    from oem_knowledge.engine import apply_oem_process_env_defaults
    apply_oem_process_env_defaults()

    # 1. Parse arguments using the fast stdlib-only parser
    from .parser import _setup_parser
    parser = _setup_parser()
    args = parser.parse_args()

    # 2. Route commands dynamically to target modules.
    # Because these imports are inside the conditional branches, the fast path
    # (commands like `--help` or `--version` which exit inside parse_args)
    # will never import anything beyond the basic parser.
    if args.command in (
        "doctor",
        "warmup",
        "setup",
        "migrate",
        "config",
        "mcp",
        "metrics",
        "todo",
        "outcome",
        "runtime-summary",
    ):
        from .commands.system import run_system_command
        run_system_command(args)
    elif args.command == "clean":
        from .commands.clean import run_clean_command
        run_clean_command(args)
    elif args.command in (
        "run",
        "session-start",
        "session-end",
        "session-status",
        "recover",
        "reflect",
    ):
        from .commands.session import run_session_command
        run_session_command(args)
    elif args.command in (
        "search",
        "read",
        "preflight",
        "concept",
        "merge",
        "status",
        "stats",
        "init",
        "rebuild",
        "events",
        "event",
        "explain",
        "identity",
        "contradictions",
        "health",
        "source",
    ):
        from .commands.knowledge import run_knowledge_command
        run_knowledge_command(args)
    elif args.command == "skills":
        from .commands.skills import run_skills_command
        run_skills_command(args)
    elif args.command == "instructions":
        from .commands.instructions import run_instructions_command
        run_instructions_command(args)
