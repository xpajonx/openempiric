from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


from oem_tui.panels import render_panel
from .engine import KnowledgeEngine, migrate_harness_to_oem
from .linter import run_lint

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("oem-knowledge")
except Exception:
    _VERSION = "0.9.5"

from .runtime import (
    _OEM_RUNTIME_CONTEXT_PATH,
    _OEM_TEMP_INSTRUCTIONS,
    _OPENCODE_PLUGINS_DIR,
    _compile_oem_context,
    run_agent,
    cmd_recover,
    SessionState,
)



def _resolve_project(args) -> str | None:
    """Normalise --project: ``""`` or ``"."`` → ``None`` (cwd)."""
    raw = getattr(args, "project", None)
    if raw and raw != ".":
        return raw
    return None

def _setup_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(description="OpenEmpiric (oem) CLI")
    parser.add_argument("--version", action="version", version=f"oem {_VERSION}", help="Show version and exit")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status")
    sub.add_parser("stats")

    init_p = sub.add_parser("init")
    init_p.add_argument("project", type=str, nargs="?", default=".")

    search_p = sub.add_parser("search")
    search_p.add_argument("query", type=str)
    search_p.add_argument("--k", type=int, default=3)
    search_p.add_argument("--project", type=str, default="")

    rebuild_p = sub.add_parser("rebuild")
    rebuild_p.add_argument("--project", type=str, default="")

    events_p = sub.add_parser("events")
    events_p.add_argument("--project", type=str, default="")
    events_p.add_argument("--concept", type=str, default="")
    events_p.add_argument("--type", type=str, default="")
    events_p.add_argument("--session-id", type=str, default="")

    event_p = sub.add_parser("event")
    event_p.add_argument("event_id", type=str)
    event_p.add_argument("--project", type=str, default="")

    explain_p = sub.add_parser("explain")
    explain_p.add_argument("type", choices=["concept", "event"])
    explain_p.add_argument("id", type=str)
    explain_p.add_argument("--history", action="store_true", help="Show revision history")
    explain_p.add_argument("--project", type=str, default="")

    vault_p = sub.add_parser("vault")
    vault_p.add_argument("action", choices=["sync", "candidates", "promote", "demote"])
    vault_p.add_argument("concept_id", type=str, nargs="?", default="")
    vault_p.add_argument("--project", type=str, default="")

    identity_p = sub.add_parser("identity")
    identity_p.add_argument("action", choices=["scan", "review"])
    identity_p.add_argument("concept_a", type=str, nargs="?", default="")
    identity_p.add_argument("concept_b", type=str, nargs="?", default="")
    identity_p.add_argument("--project", type=str, default="")

    concept_p = sub.add_parser("concept")
    concept_p.add_argument("action", choices=["evolve", "health", "fitness"])
    concept_p.add_argument("concept_id", type=str, nargs="?", default="")
    concept_p.add_argument("--format", choices=["text", "yaml", "json"], default="text")
    concept_p.add_argument("--project", type=str, default="")

    contradictions_p = sub.add_parser("contradictions")
    contradictions_p.add_argument("--project", type=str, default="")

    merge_p = sub.add_parser("merge")
    merge_p.add_argument("primary_id", type=str)
    merge_p.add_argument("secondary_id", type=str)
    merge_p.add_argument("--auto", action="store_true", help="Automatically merge")
    merge_p.add_argument("--project", type=str, default="")

    lint_p = sub.add_parser("lint")
    lint_p.add_argument("--project", type=str, default="")
    lint_p.add_argument("--workers", type=int, default=4)
    lint_p.add_argument("--fix", action="store_true", help="Automatically heal links")

    session_start_p = sub.add_parser("session-start")
    session_start_p.add_argument("--project", type=str, default="")

    session_end_p = sub.add_parser("session-end")
    session_end_p.add_argument("--project", type=str, default="")
    session_end_p.add_argument("--chat", type=str, default="")
    session_end_p.add_argument("--session-id", type=str, default="")

    run_p = sub.add_parser("run")
    run_p.add_argument("agent", type=str, help="opencode, claude-code, cursor, or custom command")
    run_p.add_argument("--project", type=str, default="")

    recover_p = sub.add_parser("recover", help="Recover an unfinished/crashed session")
    recover_p.add_argument("--project", type=str, default="")
    recover_p.add_argument("--abort", action="store_true", help="Abort/discard the unfinished session")
    recover_p.add_argument("--status", action="store_true", help="Print current active session status")


    metrics_p = sub.add_parser("metrics")
    metrics_p.add_argument("--project", type=str, default="")
    metrics_p.add_argument("--reset", action="store_true", help="Reset all metrics to default")
    metrics_p.add_argument("--export", type=str, help="Export raw metrics JSON to file path")
    metrics_p.add_argument("--usage-log", type=int, nargs="?", const=10, help="Print recent entries from usage_log.jsonl (default 10)")
    metrics_p.add_argument("--report", action="store_true", help="Report concept usage and decisions")
    metrics_p.add_argument("--used", type=str, default="[]", help="JSON array of referenced concept IDs")
    metrics_p.add_argument("--ignored", type=str, default="[]", help="JSON array of ignored concept IDs")
    metrics_p.add_argument("--decisions", type=str, default="[]", help="JSON array of decisions aligned")

    todo_p = sub.add_parser("todo", help="Manage session todo list")
    
    outcome_p = sub.add_parser("outcome", help="Record session outcome")
    outcome_p.add_argument("status", choices=["success", "failure", "abandoned"])
    outcome_p.add_argument("referenced_concepts", type=str, nargs="*", default=[])
    outcome_p.add_argument("--reason", type=str, default="")
    outcome_p.add_argument("--session-id", type=str, default="")
    outcome_p.add_argument("--project", type=str, default="")

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

    doctor_p = sub.add_parser("doctor", help="Check workspace health and configuration")
    doctor_p.add_argument("--project", type=str, default="")

    warmup_p = sub.add_parser("warmup", help="Pre-download embedding model (one-time per machine)")
    warmup_p.add_argument("--project", type=str, default="")

    return parser


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = _setup_parser()
    args = parser.parse_args()
    project = _resolve_project(args)
    eng = KnowledgeEngine(project)

    # Check for unfinished session for run, status/stats commands
    if args.command in ("status", "stats", "run"):
        try:
            harness = eng._resolve_harness(project)
            active_session_file = harness / "state" / "active_session.json"
            session_state = SessionState.load(active_session_file)
            if session_state:
                sid = session_state.session_id
                print(render_panel(
                    "Warning: Unfinished Session Detected",
                    [
                        f"An unfinished session was found (ID: {sid}).",
                        "The agent may have crashed or exited unexpectedly.",
                        "",
                        "To query status:       oem recover --status",
                        "To commit learnings:   oem recover",
                        "To discard session:    oem recover --abort"
                    ],
                    status="warning"
                ))
        except Exception:
            pass



    try:
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
                + [f"  \U0001f4c1 {d}" for d in res.get("created_directories", [])]
                + [f"  \U0001f4c4 {f}" for f in res.get("created_files", [])]
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
            res = eng.restore_session_state(project)
            lines = [
                f"Goals: {len(res.get('active_goals', []))}",
                f"Blockers: {len(res.get('blockers', []))}",
                f"Files: {len(res.get('recommended_files', []))}",
                f"Global Concepts: {len(res.get('global_concepts', []))}",
            ]
            print(render_panel("Session Start", lines, status="restore"))

        elif args.command == "session-end":
            res = eng.session_commit(project, args.chat, args.session_id)
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
            res = eng.rebuild_registry(project)
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
                project,
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
                ev = eng.get_event(project, args.event_id)
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
                print(render_panel("Not Found", [f"No event: {args.event_id}"], status="error"))

        elif args.command == "explain":
            if args.type == "concept":
                if args.history:
                    history = eng.get_concept_history(args.id, project)
                    lines = [f"Revision History for Concept: {args.id}", ""]
                    for entry in history:
                        lines.append(f"\U0001f4c5 [{entry.get('timestamp')}] - File: {entry.get('file_name')}")
                        if entry.get("diff"):
                            lines.append("Diff:")
                            for diff_line in entry.get("diff").splitlines():
                                lines.append(f"  {diff_line}")
                        lines.append("")
                    if not history:
                        lines.append("No revision history found.")
                    print(render_panel("Concept History", lines, status="ok"))
                else:
                    res = eng.explain_concept(project, args.id)
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
                    ev = eng.get_event(project, args.id)
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
            res = asyncio.run(run_lint(target, max_parallel=args.workers, fix=args.fix))
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
                    lines.append(f"  \u274c Broken link: {bl['source']}:{bl['line']} -> {bl['target']}")
                if res.get("healed_links"):
                    action = "Fixed" if args.fix else "Can Heal"
                    lines.append(f"Healed links ({action}):")
                    for hl in res["healed_links"]:
                        lines.append(
                            f"  \u2705 {hl['source']}:{hl['line']} -> resolved to {hl['target_concept']} (originally: {hl['original']})"
                        )
                for op in res.get("orphans", []):
                    lines.append(f"  \u26a0\ufe0f Orphan concept: {op}")
                print(
                    render_panel(
                        "Lint Results",
                        lines,
                        status="error" if res.get("broken_links") else "ok",
                    )
                )

        elif args.command == "vault":
            from oem_knowledge.vault import GlobalVault
            vault = GlobalVault()
            if args.action == "sync":
                try:
                    local_reg = eng._load_registry(project)
                    concepts_dir = eng._concepts_dir(project)
                    vault.sync_from_registry(local_reg, concepts_dir)
                    print(render_panel("Vault Sync", ["Global vault synchronized successfully."], status="ok"))
                except Exception as e:
                    print(render_panel("Vault Sync Failure", [f"Error: {e}"], status="error"))
            elif args.action == "candidates":
                candidates = vault.vault_candidates(project)
                lines = [f"Candidates: {len(candidates)}", ""]
                for c in candidates:
                    lines.append(f"  - {c['concept_id']} ({c['canonical_name']}) - Evidences: {c['evidence_count']}, Occurrences: {c['project_occurrences']}")
                print(render_panel("Global Vault Candidates", lines, status="ok"))
            elif args.action == "promote":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for promotion."], status="error"))
                else:
                    try:
                        vault.promote_to_global(args.concept_id, project)
                        print(render_panel("Vault Promotion", [f"Successfully promoted {args.concept_id} to Global Vault."], status="ok"))
                    except Exception as e:
                        print(render_panel("Error", [f"Promotion failed: {e}"], status="error"))
            elif args.action == "demote":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for demotion."], status="error"))
                else:
                    try:
                        vault.demote_from_global(args.concept_id, project)
                        print(render_panel("Vault Demotion", [f"Successfully demoted {args.concept_id} from Global Vault."], status="ok"))
                    except Exception as e:
                        print(render_panel("Error", [f"Demotion failed: {e}"], status="error"))

        elif args.command == "identity":
            from oem_knowledge.identity_resolver import SemanticIdentityResolver
            resolver = SemanticIdentityResolver(eng)
            if args.action == "scan":
                duplicates = resolver.scan_duplicates(project)
                lines = [f"Potential duplicates found: {len(duplicates)}", ""]
                for d in duplicates:
                    lines.append(f"  - Pair: {d['concept_a']} & {d['concept_b']}")
                    lines.append(f"    Names: {d['name_a']} | {d['name_b']}")
                    lines.append(f"    Similarity: {d['similarity']:.4f}")
                    lines.append("")
                print(render_panel("Identity Scan", lines, status="ok"))
            elif args.action == "review":
                if not args.concept_a or not args.concept_b:
                    print(render_panel("Error", ["Two concept IDs required for review."], status="error"))
                else:
                    registry = eng._load_registry(project)
                    if args.concept_a not in registry or args.concept_b not in registry:
                        print(render_panel("Error", ["One or both concepts not found in registry."], status="error"))
                    else:
                        lines = [
                            f"Reviewing similarity for {args.concept_a} and {args.concept_b}:",
                            f"  Concept A: {registry[args.concept_a].get('canonical_name')}",
                            f"  Concept B: {registry[args.concept_b].get('canonical_name')}",
                        ]
                        print(render_panel("Identity Review", lines, status="ok"))

        elif args.command == "concept":
            if args.action == "evolve":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for evolution."], status="error"))
                else:
                    from oem_knowledge.evolution import ConceptEvolutionEngine
                    evolve_engine = ConceptEvolutionEngine(eng)
                    res = evolve_engine.evolve_concept(args.concept_id, project)
                    if res.get("status") == "error":
                        print(render_panel("Evolution Failure", [res.get("message", "")], status="error"))
                    else:
                        print(render_panel("Concept Evolved", [res.get("message", "")], status="ok"))
            elif args.action == "health":
                registry = eng._load_registry(project)
                from oem_knowledge.health import calculate_concept_health
                if args.concept_id:
                    if args.concept_id not in registry:
                        print(render_panel("Error", [f"Concept {args.concept_id} not found."], status="error"))
                    else:
                        cdata = registry[args.concept_id]
                        score = calculate_concept_health(cdata)
                        lines = [
                            f"Concept: {cdata.get('canonical_name')} ({args.concept_id})",
                            f"Health Score: {score}/100",
                            f"  Confidence: {cdata.get('confidence', 1)}/5",
                            f"  Evidence Count: {cdata.get('evidence_count', 0)}",
                            f"  Failure Count: {cdata.get('failure_count', 0)}",
                            f"  Status: {cdata.get('status', 'candidate')}",
                        ]
                        print(render_panel("Concept Health Breakdown", lines, status="ok"))
                else:
                    lines = [f"Total concepts scanned: {len(registry)}", ""]
                    for cid, cdata in registry.items():
                        score = calculate_concept_health(cdata)
                        lines.append(f"  - {cid} ({cdata.get('canonical_name')}) -> Health: {score}/100 (Status: {cdata.get('status')})")
                    print(render_panel("System Health Summary", lines, status="ok"))

            elif args.action == "fitness":
                def dict_to_yaml(d: dict, indent: int = 0) -> str:
                    lines_yaml = []
                    for k, v in d.items():
                        prefix = " " * indent
                        if isinstance(v, dict):
                            lines_yaml.append(f"{prefix}{k}:")
                            lines_yaml.append(dict_to_yaml(v, indent + 2))
                        elif isinstance(v, list):
                            lines_yaml.append(f"{prefix}{k}:")
                            for item in v:
                                lines_yaml.append(f"{prefix}- {item}")
                        else:
                            if v is None:
                                lines_yaml.append(f"{prefix}{k}: null")
                            elif isinstance(v, bool):
                                lines_yaml.append(f"{prefix}{k}: {str(v).lower()}")
                            else:
                                lines_yaml.append(f"{prefix}{k}: {v}")
                    return "\n".join(lines_yaml)

                fitness_data = eng.calculate_fitness(project)
                report = {}
                for cid, fit in fitness_data.items():
                    report[cid] = {
                        "retrieved": fit.retrieved,
                        "referenced": fit.referenced,
                        "ignored": fit.ignored,
                        "successful_sessions": fit.successful_sessions,
                        "failed_sessions": fit.failed_sessions,
                        "evidence_count": fit.evidence_count,
                        "fitness_score": fit.fitness_score,
                    }

                if args.concept_id:
                    if args.concept_id not in report:
                        resolved_id = eng.fitness_service._find_concept_id(args.concept_id, eng._load_registry(project))
                        if resolved_id in report:
                            report = {resolved_id: report[resolved_id]}
                        else:
                            print(render_panel("Error", [f"Concept '{args.concept_id}' not found in fitness statistics."], status="error"))
                            sys.exit(1)
                    else:
                        report = {args.concept_id: report[args.concept_id]}

                if args.format == "json":
                    print(json.dumps(report, indent=2))
                elif args.format == "yaml":
                    print(dict_to_yaml(report))
                else:
                    lines = [
                        "Note: Outcome metrics indicate correlation, not direct causation.",
                        "Concepts sorted by active usage count (referenced sessions).",
                        "",
                        f"{'Concept Name (ID)':<30} | {'Retr':<5} | {'Ref':<5} | {'Ign':<5} | {'Succ':<5} | {'Fail':<5} | {'Evid':<5} | {'Fitness':<7}",
                        "-" * 88
                    ]
                    sorted_concepts = sorted(
                        report.items(),
                        key=lambda x: (x[1]["referenced"], x[1]["retrieved"]),
                        reverse=True
                    )
                    registry = eng._load_registry(project)
                    for cid, m in sorted_concepts:
                        name = registry.get(cid, {}).get("canonical_name", cid)
                        label = f"{name} ({cid})"
                        if len(label) > 30:
                            label = label[:27] + "..."
                        lines.append(
                            f"{label:<30} | {m['retrieved']:<5} | {m['referenced']:<5} | {m['ignored']:<5} | {m['successful_sessions']:<5} | {m['failed_sessions']:<5} | {m['evidence_count']:<5} | {m['fitness_score']:.4f}"
                        )
                    print(render_panel("Knowledge Fitness Telemetry", lines, status="stats"))

        elif args.command == "contradictions":
            from oem_knowledge.evolution import ContradictionDetector
            detector = ContradictionDetector(eng)
            contradictions = detector.detect_contradictions(project)
            lines = [f"Contradictions detected: {len(contradictions)}", ""]
            for c in contradictions:
                lines.append(f"  \u274c Conflict between {c['concept_a']} and {c['concept_b']}")
                lines.append(f"     Names: {c['name_a']} | {c['name_b']}")
                lines.append(f"     Description: {c['description']}")
                lines.append("")
            print(render_panel("Contradiction Scan", lines, status="error" if contradictions else "ok"))

        elif args.command == "merge":
            res = eng.merge_concepts(project, args.primary_id, args.secondary_id)
            if res.get("status") == "error":
                print(render_panel("Merge Failure", [res.get("message", "")], status="error"))
            else:
                print(render_panel("Concepts Merged", [res.get("message", "")], status="ok"))

        elif args.command == "run":
            run_agent(args.agent, eng, project)

        elif args.command == "recover":
            cmd_recover(eng, project, abort=args.abort, status=args.status)


        elif args.command == "todo":
            from oem_knowledge.tools.todos import oem_todo_read, oem_todo_write, oem_todo_advance
            if args.todo_action == "read":
                print(oem_todo_read(project or ""))
            elif args.todo_action == "write":
                print(oem_todo_write(args.items, project or ""))
            elif args.todo_action == "advance":
                print(oem_todo_advance(args.item_id, args.status, project or ""))

        elif args.command == "outcome":
            referenced = args.referenced_concepts if args.referenced_concepts else None
            reason = args.reason if args.reason else None
            session_id = args.session_id if args.session_id else None
            res = eng.record_outcome(
                args.status,
                referenced_concepts=referenced,
                reason=reason,
                session_id=session_id,
                project=project,
            )
            lines = [
                f"Session ID:  {res['session_id']}",
                f"Outcome:     {res['outcome'].upper()}",
                f"Concepts:    {', '.join(res['referenced_concepts']) if res['referenced_concepts'] else 'None'}",
            ]
            if res["reason"]:
                lines.append(f"Reason:      {res['reason']}")
            lines.append("")
            lines.append(f"Metrics (Injected/Referenced): {res['metrics']['concepts_injected']}/{res['metrics']['concepts_referenced']}")
            print(render_panel("Outcome Logged", lines, status="ok"))

        elif args.command == "metrics":
            if getattr(args, "report", False):
                from oem_knowledge.tools.metrics import report_usage
                try:
                    used = json.loads(args.used)
                    ignored = json.loads(args.ignored) if args.ignored else None
                    decisions = json.loads(args.decisions) if args.decisions else None
                except Exception as e:
                    print(render_panel("Report Error", [f"Invalid arguments: {e}"], status="error"))
                    sys.exit(1)
                
                print(report_usage(used, ignored, decisions, project))
                return

            metrics_file = eng._resolve_harness(project) / "state" / "metrics.json"
            if args.usage_log is not None:
                try:
                    resolved_dir = eng._resolve_harness(project)
                    log_file = resolved_dir / "state" / "usage_log.jsonl"
                except Exception:
                    log_file = Path(project or ".") / ".oem" / "state" / "usage_log.jsonl"

                if not log_file.exists():
                    print(render_panel("Usage Log", ["No usage log records found yet."], status="info"))
                else:
                    try:
                        lines = log_file.read_text(encoding="utf-8").splitlines()
                        limit = args.usage_log
                        recent = lines[-limit:] if limit > 0 else []
                        log_lines = []
                        for r in recent:
                            try:
                                entry = json.loads(r)
                                ts = entry.get("timestamp", "N/A")
                                used = entry.get("concepts_used", [])
                                ignored = entry.get("concepts_ignored", [])
                                decs = entry.get("decisions", [])
                                log_lines.append(f"[{ts}]")
                                log_lines.append(f"  Used:    {', '.join(used) if used else 'None'}")
                                log_lines.append(f"  Ignored: {', '.join(ignored) if ignored else 'None'}")
                                if decs:
                                    log_lines.append(f"  Decisions: {'; '.join(decs)}")
                            except Exception:
                                pass
                        if not log_lines:
                            log_lines = ["No valid entries found."]
                        print(render_panel("Recent Usage Log", log_lines, status="info"))
                    except Exception as e:
                        print(render_panel("Log Error", [f"Failed to read usage log: {e}"], status="error"))
            elif args.reset:
                if metrics_file.exists():
                    try:
                        metrics_file.unlink()
                    except Exception:
                        pass
                try:
                    resolved_dir = eng._resolve_harness(project)
                    log_file = resolved_dir / "state" / "usage_log.jsonl"
                    if log_file.exists():
                        log_file.unlink()
                except Exception:
                    pass
                try:
                    resolved_dir = eng._resolve_harness(project)
                    session_state_file = resolved_dir / "state" / "session_state.json"
                    if session_state_file.exists():
                        session_state_file.unlink()
                except Exception:
                    pass

                empty_metrics = {
                    "retrieval": {
                        "search_count": 0,
                        "search_latency_total": 0.0,
                        "search_latency_min": None,
                        "search_latency_max": None,
                        "last_search_latency": None,
                        "last_search_at": None,
                        "cache_hits": 0,
                        "cache_misses": 0,
                        "concepts_retrieved": 0
                    },
                    "context": {
                        "context_count": 0,
                        "context_latency_total": 0.0,
                        "context_latency_min": None,
                        "context_latency_max": None,
                        "last_context_latency": None,
                        "last_context_at": None
                    },
                    "knowledge_usage": {
                        "concepts_injected": 0,
                        "concepts_referenced": 0,
                        "concepts_ignored": 0,
                        "agent_decisions_aligned": 0,
                        "last_report_at": None
                    }
                }
                try:
                    metrics_file.parent.mkdir(parents=True, exist_ok=True)
                    metrics_file.write_text(json.dumps(empty_metrics, indent=2), encoding="utf-8")
                except Exception as e:
                    print(render_panel("Reset Error", [f"Failed to reset metrics: {e}"], status="error"))
                    sys.exit(1)
                print(render_panel("Metrics Reset", ["All retrieval and context metrics have been reset to zero."], status="ok"))
            elif args.export:
                if not metrics_file.exists():
                    print(render_panel("Export Error", ["No metrics found to export."], status="error"))
                    sys.exit(1)
                try:
                    dest = Path(args.export)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(metrics_file, dest)
                    print(render_panel("Metrics Exported", [f"Raw metrics exported successfully to: {dest}"], status="ok"))
                except Exception as e:
                    print(render_panel("Export Error", [f"Failed to export metrics: {e}"], status="error"))
                    sys.exit(1)
            else:
                if not metrics_file.exists():
                    print(render_panel("Retrieval Metrics", ["No metrics recorded yet."], status="info"))
                else:
                    try:
                        data = json.loads(metrics_file.read_text(encoding="utf-8"))
                        retrieval = data.get("retrieval", {})
                        context = data.get("context", {})
                        usage = data.get("knowledge_usage", {})

                        search_count = retrieval.get("search_count", 0)
                        search_total = retrieval.get("search_latency_total", 0.0)
                        search_min = retrieval.get("search_latency_min")
                        search_max = retrieval.get("search_latency_max")
                        search_last = retrieval.get("last_search_latency")
                        search_last_at = retrieval.get("last_search_at")
                        search_avg = (search_total / search_count) if search_count > 0 else 0.0
                        concepts_retrieved = retrieval.get("concepts_retrieved", 0)

                        hits = retrieval.get("cache_hits", 0)
                        misses = retrieval.get("cache_misses", 0)
                        total_lookups = hits + misses
                        hit_rate = (hits / total_lookups * 100) if total_lookups > 0 else 0.0

                        context_count = context.get("context_count", 0)
                        context_total = context.get("context_latency_total", 0.0)
                        context_min = context.get("context_latency_min")
                        context_max = context.get("context_latency_max")
                        context_last = context.get("last_context_latency")
                        context_last_at = context.get("last_context_at")
                        context_avg = (context_total / context_count) if context_count > 0 else 0.0

                        lines = [
                            "Retrieval Search Metrics:",
                            f"  Total Searches:     {search_count}",
                            f"  Concepts Retrieved: {concepts_retrieved}",
                            f"  Avg Latency:        {search_avg:.2f} ms",
                            f"  Min/Max Latency:    {f'{search_min:.2f}/{search_max:.2f}' if search_min is not None else 'N/A'} ms",
                            f"  Last Latency:       {f'{search_last:.2f}' if search_last is not None else 'N/A'} ms",
                            f"  Last Run:           {search_last_at or 'N/A'}",
                            "",
                            "Cache (RegistryCache) Metrics:",
                            f"  Cache Hits:         {hits}",
                            f"  Cache Misses:       {misses}",
                            f"  Cache Hit Rate:     {hit_rate:.1f}%",
                            "",
                            "Context Metrics:",
                            f"  Context Loads:      {context_count}",
                            f"  Avg Latency:        {context_avg:.2f} ms",
                            f"  Min/Max Latency:    {f'{context_min:.2f}/{context_max:.2f}' if context_min is not None else 'N/A'} ms",
                            f"  Last Latency:       {f'{context_last:.2f}' if context_last is not None else 'N/A'} ms",
                            f"  Last Run:           {context_last_at or 'N/A'}",
                            "",
                            "Knowledge Attribution Metrics (Self-Reported):",
                            f"  Concepts Injected:   {usage.get('concepts_injected', 0)}",
                            f"  Concepts Referenced: {usage.get('concepts_referenced', 0)}",
                            f"  Concepts Ignored:    {usage.get('concepts_ignored', 0)}",
                            f"  Decisions Aligned:   {usage.get('agent_decisions_aligned', 0)}",
                            f"  Last Report At:      {usage.get('last_report_at') or 'N/A'}",
                        ]
                        print(render_panel("Retrieval Metrics", lines, status="info"))
                    except Exception as e:
                        print(render_panel("Metrics Error", [f"Failed to read metrics: {e}"], status="error"))
        elif args.command == "warmup":
            res = eng.warmup()
            print(render_panel("Model Warm-Up", [f"Status: {res['status']}", f"Model: {res['model']}", "", "Embedding model is now cached globally.", "Run `oem doctor` to verify."], status="ok"))

        elif args.command == "doctor":
            try:
                resolved_dir = eng._resolve_harness(project)
                workspace_root = resolved_dir
            except Exception:
                workspace_root = Path(project or ".")

            # Walk up to find workspace root containing pyproject.toml
            while workspace_root.parent != workspace_root:
                if (workspace_root / "pyproject.toml").exists():
                    break
                workspace_root = workspace_root.parent

            pyproject_path = workspace_root / "pyproject.toml"
            root_venv_path = workspace_root / ".venv"
            
            # Detect if this is the OpenEmpiric development workspace
            is_dev_workspace = False
            if pyproject_path.exists():
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if 'name = "oem-mcp"' in content:
                        is_dev_workspace = True
                except Exception:
                    pass

            lines = []
            status = "ok"

            if is_dev_workspace:
                # 1. Root workspace check
                if pyproject_path.exists():
                    lines.append("✓ Root workspace detected")
                else:
                    lines.append("✗ Root workspace pyproject.toml not found")
                    status = "error"

                # 2. Root venv check
                if root_venv_path.exists():
                    lines.append("✓ Root .venv exists")
                else:
                    lines.append("✗ Root .venv not found")
                    status = "error"

                # 3. UV workspace health check
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if "[tool.uv.workspace]" in content:
                        lines.append("✓ UV workspace healthy")
                    else:
                        lines.append("✗ [tool.uv.workspace] missing in root pyproject.toml")
                        status = "error"
                except Exception as e:
                    lines.append(f"✗ Failed to read root pyproject.toml: {e}")
                    status = "error"

                # 4. Nested virtualenvs scan
                nested_venvs = []
                packages_dir = workspace_root / "packages"
                if packages_dir.exists() and packages_dir.is_dir():
                    for p in packages_dir.iterdir():
                        if p.is_dir():
                            sub_venv = p / ".venv"
                            if sub_venv.exists():
                                nested_venvs.append(str(sub_venv.relative_to(workspace_root)))

                if nested_venvs:
                    status = "error"
                    for nv in nested_venvs:
                        lines.append(f"✗ Nested virtualenv detected: {nv}")
                    lines.append("")
                    lines.append("Suggested Fix:")
                    lines.append(f"  rm -rf {Path(packages_dir.relative_to(workspace_root)) / '*/.venv'}")
                    lines.append("  uv sync")
                else:
                    lines.append("✓ No nested virtualenvs detected")
            else:
                lines.append("✓ Running as globally installed user tool")
                lines.append(f"✓ Project directory: {workspace_root.resolve()}")
                if shutil.which("oem"):
                    lines.append("✓ OEM executable available")
                else:
                    lines.append("✗ OEM executable not found in PATH")
                    status = "error"
                try:
                    import oem_knowledge  # noqa: F401
                    lines.append("✓ Package importable")
                except ImportError:
                    lines.append("✗ Package not importable")
                    status = "error"
                lines.append("⚠ Development workspace not detected")

            # 5. Events log schema version check
            try:
                schema_status = eng.event_migrator.get_schema_status(project)
                if schema_status["status"] == "up_to_date":
                    lines.append(f"✓ Events schema up to date ({schema_status['message']})")
                elif schema_status["status"] == "outdated":
                    lines.append(f"✗ Events schema outdated: {schema_status['message']}")
                    status = "error"
                else:
                    lines.append(f"✗ Events schema check: {schema_status.get('message')}")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Events schema check failed: {e}")
                status = "error"

            # 6. Skill installation check & adapter detection
            adapter_name = "opencode"
            try:
                h_dir = eng._resolve_harness(project)
                skills_file = h_dir / "skills" / "openempiric.yaml"
                if skills_file.exists():
                    lines.append("✓ OEM Skill Installed")
                    try:
                        import yaml
                        with open(skills_file, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            if data and "adapter" in data:
                                adapter_name = data["adapter"]
                    except Exception:
                        pass
                else:
                    lines.append("✗ OEM Skill not installed (missing skills/openempiric.yaml)")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to verify OEM Skill installation: {e}")
                status = "error"

            # 7. MCP Registered check (adapter-aware)
            try:
                from oem_knowledge.adapters import get_adapter
                adapter = get_adapter(adapter_name, eng, project)
                if adapter.verify_mcp():
                    lines.append("✓ MCP Registered")
                else:
                    lines.append(f"✗ MCP not registered (for adapter: {adapter_name})")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to verify MCP registration: {e}")
                status = "error"

            # 8. Embedding Cache Ready check
            try:
                from fastembed import TextEmbedding
                TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
                lines.append("✓ Embedding Cache Ready")
            except Exception:
                lines.append("✗ Embedding Cache not ready")
                lines.append("  → Run `oem warmup` once per machine to pre-download")
                status = "error"

            # 9. Context Injection Working check
            try:
                _ = _compile_oem_context(eng)
                if adapter_name == "opencode":
                    context_dir = _OEM_RUNTIME_CONTEXT_PATH.parent
                elif adapter_name in ("agy", "antigravity"):
                    from oem_knowledge.adapters import get_adapter
                    adapter = get_adapter(adapter_name, eng, project)
                    context_dir = adapter.get_app_data_dir()
                else:
                    context_dir = Path.home() / ".config" / "opencode" / "plugins"
                
                context_dir.mkdir(parents=True, exist_ok=True)
                test_file = context_dir / ".oem_doctor_write_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
                lines.append("✓ Context Injection Working")
            except Exception as e:
                lines.append(f"✗ Context Injection not working: {e}")
                status = "error"

            # 10. Managed Runtime Available check
            try:
                bin_name = "opencode"
                if adapter_name == "opencode":

                    bin_name = "opencode"
                elif adapter_name in ("agy", "antigravity"):
                    bin_name = "agy"
                else:
                    bin_name = adapter_name
                
                if shutil.which(bin_name):
                    lines.append("✓ Managed Runtime Available")
                else:
                    lines.append(f"✗ Managed Runtime not available (executable '{bin_name}' not found in PATH)")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to check Managed Runtime: {e}")
                status = "error"

            # 11. Search Pipeline Available check
            try:
                _ = eng.search_service.stats()
                eng.search_service.search("test", k=1)
                lines.append("✓ Search Pipeline Available")
            except Exception as e:
                lines.append(f"✗ Search Pipeline not available: {e}")
                status = "error"


            print(render_panel("OEM Environment Check", lines, status=status))

            # --- Knowledge Health Dashboard ---
            try:
                fitness_data = eng.calculate_fitness(project)
                registry = eng._load_registry(project)

                tested = []
                untested = []

                for cid, fit in fitness_data.items():
                    conf = registry.get(cid, {}).get("confidence", 1)
                    entry = {
                        "id": cid,
                        "name": fit.canonical_name.replace("-", " ").title(),
                        "fitness": fit.fitness_score,
                        "evidence": fit.evidence_count,
                        "referenced": fit.referenced,
                        "successful": fit.successful_sessions,
                        "failed": fit.failed_sessions,
                        "confidence": conf,
                    }
                    if fit.referenced > 0:
                        # Composite score: fitness weighted by log of evidence
                        # Concepts with more evidence are more reliable signals
                        import math
                        entry["composite"] = fit.fitness_score * (1.0 + 0.3 * math.log1p(fit.evidence_count))
                        tested.append(entry)
                    else:
                        untested.append(entry)

                tested_by_composite = sorted(tested, key=lambda x: x["composite"], reverse=True)
                top = tested_by_composite[:5]
                bottom = [x for x in tested_by_composite[::-1] if x["fitness"] < 1.0][:5]

                dash_lines = [
                    "⚠  Scores are outcome correlations, not causation.",
                    "   Ranked by: Fitness × Evidence (composite score).",
                    "",
                ]

                if not tested:
                    dash_lines.append("No session outcome data yet.")
                    dash_lines.append("Run sessions and record outcomes with:")
                    dash_lines.append("  oem outcome success")
                    dash_lines.append("  oem outcome failure")
                else:
                    dash_lines.append("Top Concepts:")
                    for c in top:
                        sessions = c["successful"] + c["failed"]
                        label = c["name"][:28]
                        dash_lines.append(
                            f"  ✦ {label:<28}  Fitness: {c['fitness'] * 100:.0f}%"
                            f"  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5"
                            f"  ({c['successful']}/{sessions} sessions)"
                        )

                    if bottom and bottom != top[:len(bottom)]:
                        dash_lines.append("")
                        dash_lines.append("Underperforming Concepts:")
                        for c in bottom:
                            sessions = c["successful"] + c["failed"]
                            label = c["name"][:28]
                            dash_lines.append(
                                f"  ✗ {label:<28}  Fitness: {c['fitness'] * 100:.0f}%"
                                f"  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5"
                                f"  ({c['successful']}/{sessions} sessions)"
                            )

                if untested:
                    dash_lines.append("")
                    dash_lines.append(f"Untested Concepts ({len(untested)} total — no session outcomes):")
                    for c in untested[:5]:
                        dash_lines.append(f"  ○ {c['name'][:28]:<28}  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5")
                    if len(untested) > 5:
                        dash_lines.append(f"  … and {len(untested) - 5} more")

                print(render_panel("Knowledge Health Dashboard", dash_lines, status="stats"))

            except Exception as e:
                print(render_panel("Knowledge Health Dashboard", [f"Could not compute: {e}"], status="error"))

            if status == "error":
                sys.exit(1)

    except Exception as e:
        logging.exception("Unhandled error in command '%s'", args.command)
        print(render_panel("Error", [str(e)], status="error"))
        sys.exit(1)


if __name__ == "__main__":
    main()
