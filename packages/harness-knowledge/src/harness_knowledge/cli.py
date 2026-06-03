from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from harness_tui.panels import render_panel
from .engine import KnowledgeEngine, migrate_harness_to_oem
from .linter import run_lint


def run_agent(agent_name: str, project_dir: str):
    # 1. Resolve repository root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    plugin_src = repo_root / "plugins" / "openempiric.ts"
    
    # 2. Ensure plugin is symlinked/copied to plugins folder
    plugin_dest_dir = Path.home() / ".config" / "opencode" / "plugins"
    plugin_dest_dir.mkdir(parents=True, exist_ok=True)
    plugin_dest = plugin_dest_dir / "openempiric.ts"
    
    if plugin_src.exists() and not plugin_dest.exists():
        try:
            plugin_dest.symlink_to(plugin_src)
        except Exception:
            try:
                shutil.copy(plugin_src, plugin_dest)
            except Exception as e:
                print(f"Warning: Failed to copy plugin file to {plugin_dest}: {e}")

    # 3. Handle opencode.jsonc modification (Option A)
    config_path = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    config_backup_path = Path.home() / ".config" / "opencode" / "opencode.jsonc.bak"
    config_modified = False

    if config_path.exists():
        try:
            # Backup
            shutil.copy(config_path, config_backup_path)
            
            # Read and parse
            text = config_path.read_text(encoding="utf-8")
            # Simple comment stripping (ignoring URL schemes like https://)
            cleaned = re.sub(r"(?<!:)\/\/.*", "", text)
            cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
            config_data = json.loads(cleaned)
            
            # Update plugin array
            plugins = config_data.setdefault("plugin", [])
            if "openempiric" not in plugins and ["openempiric", {}] not in plugins:
                plugins.append("openempiric")
                config_modified = True
                
            if config_modified:
                # Write updated json
                config_path.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Warning: Failed to temporarily modify config at {config_path}: {e}")

    # 4. Spawn the coding agent
    print(f"Spawning coding agent: {agent_name}...")
    try:
        # Execute the agent command
        if agent_name == "opencode":
            subprocess.run(["opencode"], check=True)
        elif agent_name == "claude-code":
            subprocess.run(["claude"], check=True)
        elif agent_name == "cursor":
            subprocess.run(["cursor", "."], check=True)
        else:
            subprocess.run(agent_name.split(), check=True)
    except Exception as e:
        print(f"Agent session finished or returned: {e}")
    finally:
        # 5. Restore config on exit
        if config_modified and config_backup_path.exists():
            try:
                shutil.move(config_backup_path, config_path)
            except Exception as e:
                print(f"Warning: Failed to restore config at {config_path}: {e}")
        elif config_backup_path.exists():
            try:
                config_backup_path.unlink()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="OpenEmpiric (oem) CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # status / stats
    sub.add_parser("status")
    sub.add_parser("stats")

    # init
    init_parser = sub.add_parser("init")
    init_parser.add_argument("project", type=str, nargs="?", default=".")

    # search
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query", type=str)
    search_parser.add_argument("--k", type=int, default=3)
    search_parser.add_argument("--project", type=str, default="")

    # rebuild
    rebuild_parser = sub.add_parser("rebuild")
    rebuild_parser.add_argument("--project", type=str, default="")

    # events
    events_parser = sub.add_parser("events")
    events_parser.add_argument("--project", type=str, default="")
    events_parser.add_argument("--concept", type=str, default="")
    events_parser.add_argument("--type", type=str, default="")
    events_parser.add_argument("--session-id", type=str, default="")

    # event
    event_parser = sub.add_parser("event")
    event_parser.add_argument("event_id", type=str)
    event_parser.add_argument("--project", type=str, default="")

    # explain
    explain_parser = sub.add_parser("explain")
    explain_parser.add_argument("type", choices=["concept", "event"])
    explain_parser.add_argument("id", type=str)
    explain_parser.add_argument("--project", type=str, default="")

    # lint
    lint_parser = sub.add_parser("lint")
    lint_parser.add_argument("--project", type=str, default="")
    lint_parser.add_argument("--workers", type=int, default=4)
    lint_parser.add_argument(
        "--fix", action="store_true", help="Automatically heal links"
    )

    # session-start
    session_start_parser = sub.add_parser("session-start")
    session_start_parser.add_argument("--project", type=str, default="")

    # session-end
    session_end_parser = sub.add_parser("session-end")
    session_end_parser.add_argument("--project", type=str, default="")
    session_end_parser.add_argument("--chat", type=str, default="")
    session_end_parser.add_argument("--session-id", type=str, default="")

    # run
    run_parser = sub.add_parser("run")
    run_parser.add_argument("agent", type=str, help="opencode, claude-code, cursor, or custom command")
    run_parser.add_argument("--project", type=str, default="")

    args = parser.parse_args()
    
    # We instantiate KnowledgeEngine
    project_dir = args.project if hasattr(args, "project") and args.project else "."
    eng = KnowledgeEngine(project_dir if project_dir != "." else None)

    if args.command in ("status", "stats"):
        s = eng.stats()
        lines = [
            f"Chunks: {s['total_chunks']}",
            f"DB size: {s['db_size_mb']:.2f} MB",
            f"Path: {s['harness_path']}",
        ]
        print(render_panel("Stats", lines, status="stats"))

    elif args.command == "init":
        res = eng.init_project(args.project)
        lines = (
            [res["message"]]
            + [f"  📁 {d}" for d in res.get("created_directories", [])]
            + [f"  📄 {f}" for f in res.get("created_files", [])]
        )
        print(render_panel("Init Complete", lines, status="bootstrap"))

    elif args.command == "search":
        results = eng.search(args.query, k=args.k)
        lines = [f'Query: "{args.query}"', f"Results: {len(results)}", ""]
        for idx, r in enumerate(results):
            lines.append(
                f"{idx + 1}. [{r['metadata'].get('rel_path', 'unknown')}] (score: {r['score']:.4f})"
            )
            lines.append(f"   {r['document'][:150]}...")
            lines.append("")
        if not results:
            lines = [f"No matches for: '{args.query}'"]
        print(render_panel("Search Results", lines, status="search"))

    elif args.command == "session-start":
        res = eng.restore_session_state(args.project or None)
        lines = [
            f"Goals: {len(res.get('active_goals', []))}",
            f"Blockers: {len(res.get('blockers', []))}",
            f"Files: {len(res.get('recommended_files', []))}",
        ]
        print(render_panel("Session Start", lines, status="restore"))

    elif args.command == "session-end":
        res = eng.session_commit(args.project or None, args.chat, args.session_id)
        print(
            render_panel(
                "Session End Complete",
                [
                    f"Report: {Path(res['report_path']).name}",
                    f"Materialized: {len(res.get('materialized_log', []))}",
                    f"Links: {res.get('links_updated', 0)}",
                ],
                status="ok",
            )
        )

    elif args.command == "rebuild":
        res = eng.rebuild_registry(args.project or None)
        print(
            render_panel(
                "Registry Rebuilt",
                [
                    res.get("message", ""),
                    f"Materialized concepts: {res.get('materialized', 0)}",
                ],
                status="ok",
            )
        )

    elif args.command == "events":
        events = eng.get_events(
            args.project or None,
            concept=args.concept,
            event_type=args.type,
            session_id=args.session_id,
        )
        lines = [f"Total: {len(events)}"] + [
            f"  [{ev['event_type'].upper()}] {ev.get('summary', '')[:80]}"
            for ev in events[:20]
        ]
        print(render_panel("Events", lines, status="ok"))

    elif args.command == "event":
        try:
            ev = eng.get_event(args.project or None, args.event_id)
            print(
                render_panel(
                    "Event",
                    [
                        f"Type: {ev['event_type']}",
                        f"Summary: {ev.get('summary', '')}",
                        f"Evidence: {ev.get('evidence', '')}",
                    ],
                    status="ok",
                )
            )
        except KeyError:
            print(
                render_panel(
                    "Not Found", [f"No event: {args.event_id}"], status="error"
                )
            )

    elif args.command == "explain":
        if args.type == "concept":
            res = eng.explain_concept(args.project or None, args.id)
            if res.get("status") == "error":
                print(render_panel("Concept Not Found", [res.get("message", "")], status="error"))
            else:
                cdata = res["explanation"]["concept"]
                lines = [
                    f"Concept: {cdata.get('canonical_name', '').title()} ({cdata.get('concept_id', '')})",
                    f"Status: {cdata.get('status', '').upper()}",
                    f"Confidence: {cdata.get('confidence', '')}/5",
                    f"Total Events: {res['explanation'].get('total_events', 0)}",
                    f"Aliases: {', '.join(cdata.get('aliases', []))}",
                    "",
                    "Recent Evidence:",
                ]
                for ev in res["explanation"].get("recent_evidence", []):
                    lines.append(f"  - {ev}")
                print(render_panel("Concept Explanation", lines, status="ok"))
        else:
            try:
                ev = eng.get_event(args.project or None, args.id)
                lines = [
                    f"Event ID: {ev.get('event_id')}",
                    f"Type:     {ev.get('event_type')}",
                    f"Summary:  {ev.get('summary')}",
                    f"Evidence: {ev.get('evidence')}",
                ]
                print(render_panel("Event Explanation", lines, status="ok"))
            except KeyError:
                print(render_panel("Event Not Found", [f"No event: {args.id}"], status="error"))

    elif args.command == "lint":
        target = Path(args.project) if args.project else Path.cwd()
        res = asyncio.run(
            run_lint(target, max_parallel=args.workers, fix=args.fix)
        )
        if res["status"] == "error":
            print(render_panel("Lint Error", [res["message"]], status="error"))
        else:
            lines = [
                f"Files scanned: {res.get('files_scanned', 0)}",
                f"Broken links:  {len(res.get('broken_links', []))}",
                f"Healed links:  {len(res.get('healed_links', []))}",
                f"Orphan nodes:  {len(res.get('orphans', []))}",
            ]
            if args.fix:
                lines.append(f"Files fixed:   {res.get('fixed_files_count', 0)}")
            lines.append("")

            for bl in res.get("broken_links", []):
                lines.append(
                    f"  ❌ Broken link: {bl['source']}:{bl['line']} -> {bl['target']}"
                )
            if res.get("healed_links"):
                action = "Fixed" if args.fix else "Can Heal"
                lines.append(f"Healed links ({action}):")
                for hl in res["healed_links"]:
                    lines.append(
                        f"  ✅ {hl['source']}:{hl['line']} -> resolved to {hl['target_concept']} (originally: {hl['original']})"
                    )
            for op in res.get("orphans", []):
                lines.append(f"  ⚠️ Orphan concept: {op}")
            print(
                render_panel(
                    "Lint Results",
                    lines,
                    status="error" if res.get("broken_links") else "ok",
                )
            )

    elif args.command == "run":
        run_agent(args.agent, project_dir)


if __name__ == "__main__":
    main()
