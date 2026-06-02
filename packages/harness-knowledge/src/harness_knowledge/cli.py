from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness_tui.panels import render_panel

from .engine import KnowledgeEngine


def main():
    parser = argparse.ArgumentParser(description="harness-knowledge CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats")
    sub.add_parser("init").add_argument("project", type=str, help="Project name")
    sub.add_parser("search").add_argument("query", type=str).add_argument("--k", type=int, default=3).add_argument("--project", type=str, default="")
    sub.add_parser("index").add_argument("--force", action="store_true").add_argument("--project", type=str, default="")
    sub.add_parser("commit").add_argument("project", type=str).add_argument("--chat", type=str, default="").add_argument("--session-id", type=str, default="")
    sub.add_parser("materialize").add_argument("project", type=str)
    sub.add_parser("reflect").add_argument("project", type=str).add_argument("--chat", type=str, default="").add_argument("--session-id", type=str, default="")
    sub.add_parser("graph").add_argument("project", type=str)
    sub.add_parser("consolidate").add_argument("project", type=str)
    sub.add_parser("events").add_argument("project", type=str).add_argument("--concept", type=str, default="").add_argument("--type", type=str, default="").add_argument("--session-id", type=str, default="")
    sub.add_parser("event").add_argument("project", type=str).add_argument("event_id", type=str)
    sub.add_parser("session-start").add_argument("project", type=str)

    args = parser.parse_args()
    eng = KnowledgeEngine()

    if args.command == "stats":
        s = eng.stats()
        lines = [f"Chunks: {s['total_chunks']}", f"DB size: {s['db_size_mb']:.2f} MB", f"Path: {s['harness_path']}"]
        print(render_panel("Stats", lines, status="stats"))

    elif args.command == "init":
        res = eng.init_project(args.project)
        lines = [res["message"]] + [f"  📁 {d}" for d in res.get("created_directories", [])] + [f"  📄 {f}" for f in res.get("created_files", [])]
        print(render_panel("Init Complete", lines, status="bootstrap"))

    elif args.command == "search":
        e = KnowledgeEngine(args.project or None)
        results = e.search(args.query, k=args.k)
        lines = [f"Query: \"{args.query}\"", f"Results: {len(results)}", ""]
        for idx, r in enumerate(results):
            lines.append(f"{idx+1}. [{r['metadata'].get('rel_path', 'unknown')}] (score: {r['score']:.4f})")
            lines.append(f"   {r['document'][:150]}...")
            lines.append("")
        if not results:
            lines = [f"No matches for: '{args.query}'"]
        print(render_panel("Search Results", lines, status="search"))

    elif args.command == "index":
        e = KnowledgeEngine(args.project or None)
        s = e.index_all(force=args.force)
        lines = [f"Scanned: {s['scanned']}  New: {s['new']}  Updated: {s['updated']}  Unchanged: {s['unchanged']}  Failed: {s['failed']}"]
        print(render_panel("Index Complete", lines, status="index"))

    elif args.command == "session-start":
        e = KnowledgeEngine(args.project)
        res = e.restore_session_state(args.project)
        lines = [f"Goals: {len(res.get('active_goals', []))}", f"Blockers: {len(res.get('blockers', []))}", f"Files: {len(res.get('recommended_files', []))}"]
        print(render_panel("Session Start", lines, status="restore"))

    elif args.command == "reflect":
        e = KnowledgeEngine(args.project)
        res = e.reflect_session(args.project, args.chat, args.session_id)
        print(render_panel("Reflection Complete", [f"Report: {Path(res['report_path']).name}", f"Events: {len(res.get('knowledge_events', []))}"], status="ok"))

    elif args.command == "commit":
        e = KnowledgeEngine(args.project)
        res = e.session_commit(args.project, args.chat, args.session_id)
        print(render_panel("Commit Complete", [f"Report: {Path(res['report_path']).name}", f"Materialized: {len(res.get('materialized_log', []))}", f"Links: {res.get('links_updated', 0)}"], status="ok"))

    elif args.command == "materialize":
        e = KnowledgeEngine(args.project)
        res = e.materialize_concepts(args.project)
        print(render_panel("Materialize Complete", res.get("materialized", ["No changes"]), status="ok"))

    elif args.command == "graph":
        e = KnowledgeEngine(args.project)
        res = e.update_graph(args.project)
        print(render_panel("Graph Updated", [f"Links: {res.get('links_updated', 0)}"], status="organize"))

    elif args.command == "consolidate":
        e = KnowledgeEngine(args.project)
        res = e.consolidate(args.project)
        print(render_panel("Consolidation", res.get("merged", ["No changes"]), status="organize"))

    elif args.command == "events":
        e = KnowledgeEngine(args.project)
        events = e.get_events(args.project, concept=args.concept, event_type=args.type, session_id=args.session_id)
        lines = [f"Total: {len(events)}"] + [f"  [{ev['event_type'].upper()}] {ev.get('summary', '')[:80]}" for ev in events[:20]]
        print(render_panel("Events", lines, status="ok"))

    elif args.command == "event":
        e = KnowledgeEngine(args.project)
        try:
            ev = e.get_event(args.project, args.event_id)
            print(render_panel("Event", [f"Type: {ev['event_type']}", f"Summary: {ev.get('summary', '')}", f"Evidence: {ev.get('evidence', '')}"], status="ok"))
        except KeyError:
            print(render_panel("Not Found", [f"No event: {args.event_id}"], status="error"))


if __name__ == "__main__":
    main()
