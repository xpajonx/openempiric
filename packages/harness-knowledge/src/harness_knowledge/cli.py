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


def run_agent(agent_name: str, project_dir: str, eng: KnowledgeEngine):
    # 1. Resolve repository root
    repo_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    plugin_src = repo_root / "plugins" / "openempiric.ts"
    
    # 2. Ensure plugin is symlinked/copied to plugins folder
    plugin_dest_dir = Path.home() / ".config" / "opencode" / "plugins"
    plugin_dest_dir.mkdir(parents=True, exist_ok=True)
    plugin_dest = plugin_dest_dir / "openempiric.ts"
    
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
                print(f"Warning: Failed to copy plugin file to {plugin_dest}: {e}")

    # 3. Compile OEMRuntimeContext
    active_concepts = []
    try:
        registry = eng._load_registry()
        for cid, cdata in registry.items():
            if cdata.get("status") in ("validated", "canonical", "global"):
                desc = ""
                wiki_file = eng._concepts_dir() / f"{cid}.md"
                if wiki_file.exists():
                    try:
                        text = wiki_file.read_text(encoding="utf-8")
                        body_match = re.search(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
                        body = body_match.group(1).strip() if body_match else text.strip()
                        body = re.sub(r"^#.*?\n", "", body).strip()
                        desc = body.split("\n")[0][:150].strip()
                    except Exception:
                        pass
                active_concepts.append({
                    "id": cid,
                    "name": cdata.get("canonical_name", cid),
                    "description": desc
                })
    except Exception as e:
        print(f"Warning: Failed to compile active concepts: {e}")

    active_decisions = []
    relevant_failures = []
    try:
        events = eng._load_events()
        
        # Gather decisions
        seen_decisions = set()
        for ev in reversed(events):
            if ev.get("event_type") == "decision":
                d = ev.get("evidence", "")
                if d and d not in seen_decisions:
                    seen_decisions.add(d)
                    active_decisions.append(d)
                    if len(active_decisions) >= 5:
                        break
        active_decisions.reverse()

        # Gather failures
        seen_failures = set()
        for ev in reversed(events):
            if ev.get("event_type") == "failure":
                f = ev.get("evidence", "")
                if f and f not in seen_failures:
                    seen_failures.add(f)
                    relevant_failures.append(f)
                    if len(relevant_failures) >= 5:
                        break
        relevant_failures.reverse()
    except Exception as e:
        print(f"Warning: Failed to compile events context: {e}")

    open_questions = []
    try:
        session_state = eng.restore_session_state()
        open_questions = session_state.get("active_goals", [])
    except Exception as e:
        print(f"Warning: Failed to compile active goals: {e}")

    oem_context = {
        "active_concepts": active_concepts,
        "active_decisions": active_decisions,
        "relevant_failures": relevant_failures,
        "open_questions": open_questions
    }

    # 4. Inject OEMRuntimeContext into opencode.jsonc
    config_path = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    orig_content = ""
    if config_path.exists():
        try:
            orig_content = config_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"Warning: Failed to read opencode.jsonc: {e}")

    def clean_jsonc(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"(?<!:)//.*", "", text)
        return text.strip()

    if orig_content:
        try:
            cleaned = clean_jsonc(orig_content)
            config = json.loads(cleaned)
            config["openempiric"] = oem_context
            config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Warning: Failed to inject context into opencode.jsonc: {e}")

    # 5. Spawn the coding agent
    print(f"Spawning coding agent: {agent_name}...")
    try:
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
        # Restore configuration
        if orig_content:
            try:
                config_path.write_text(orig_content, encoding="utf-8")
            except Exception as e:
                print(f"Warning: Failed to restore opencode.jsonc: {e}")
        
        # Delete transient instruction file
        temp_inst = Path.home() / ".config" / "opencode" / "plugins" / ".openempiric_temp_instructions.md"
        if temp_inst.exists():
            try:
                temp_inst.unlink()
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
    explain_parser.add_argument("--history", action="store_true", help="Show revision history")
    explain_parser.add_argument("--project", type=str, default="")

    # vault
    vault_parser = sub.add_parser("vault")
    vault_parser.add_argument("action", choices=["sync", "candidates", "promote", "demote"])
    vault_parser.add_argument("concept_id", type=str, nargs="?", default="")
    vault_parser.add_argument("--project", type=str, default="")

    # identity
    identity_parser = sub.add_parser("identity")
    identity_parser.add_argument("action", choices=["scan", "review"])
    identity_parser.add_argument("concept_a", type=str, nargs="?", default="")
    identity_parser.add_argument("concept_b", type=str, nargs="?", default="")
    identity_parser.add_argument("--project", type=str, default="")

    # concept
    concept_parser = sub.add_parser("concept")
    concept_parser.add_argument("action", choices=["evolve", "health"])
    concept_parser.add_argument("concept_id", type=str, nargs="?", default="")
    concept_parser.add_argument("--project", type=str, default="")

    # contradictions
    contradictions_parser = sub.add_parser("contradictions")
    contradictions_parser.add_argument("--project", type=str, default="")

    # merge
    merge_parser = sub.add_parser("merge")
    merge_parser.add_argument("primary_id", type=str)
    merge_parser.add_argument("secondary_id", type=str)
    merge_parser.add_argument("--auto", action="store_true", help="Automatically merge")
    merge_parser.add_argument("--project", type=str, default="")

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
            f"Global Concepts: {len(res.get('global_concepts', []))}",
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
            if hasattr(args, "history") and args.history:
                history = eng.get_concept_history(args.id, args.project or None)
                lines = [f"Revision History for Concept: {args.id}", ""]
                for entry in history:
                    lines.append(f"📅 [{entry.get('timestamp')}] - File: {entry.get('file_name')}")
                    if entry.get("diff"):
                        lines.append("Diff:")
                        for diff_line in entry.get("diff").splitlines():
                            lines.append(f"  {diff_line}")
                    lines.append("")
                if not history:
                    lines.append("No revision history found.")
                print(render_panel("Concept History", lines, status="ok"))
            else:
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

    elif args.command == "vault":
        from harness_knowledge.vault import GlobalVault
        vault = GlobalVault()
        if args.action == "sync":
            try:
                local_reg = eng._load_registry(args.project or None)
                concepts_dir = eng._concepts_dir(args.project or None)
                vault.sync_from_registry(local_reg, concepts_dir)
                print(render_panel("Vault Sync", ["Global vault synchronized successfully."], status="ok"))
            except Exception as e:
                print(render_panel("Vault Sync Failure", [f"Error: {e}"], status="error"))
        elif args.action == "candidates":
            candidates = vault.vault_candidates(args.project or None)
            lines = [f"Candidates: {len(candidates)}", ""]
            for c in candidates:
                lines.append(f"  - {c['concept_id']} ({c['canonical_name']}) - Evidences: {c['evidence_count']}, Occurrences: {c['project_occurrences']}")
            print(render_panel("Global Vault Candidates", lines, status="ok"))
        elif args.action == "promote":
            if not args.concept_id:
                print(render_panel("Error", ["Concept ID required for promotion."], status="error"))
            else:
                try:
                    vault.promote_to_global(args.concept_id, args.project or None)
                    print(render_panel("Vault Promotion", [f"Successfully promoted {args.concept_id} to Global Vault."], status="ok"))
                except Exception as e:
                    print(render_panel("Error", [f"Promotion failed: {e}"], status="error"))
        elif args.action == "demote":
            if not args.concept_id:
                print(render_panel("Error", ["Concept ID required for demotion."], status="error"))
            else:
                try:
                    vault.demote_from_global(args.concept_id, args.project or None)
                    print(render_panel("Vault Demotion", [f"Successfully demoted {args.concept_id} from Global Vault."], status="ok"))
                except Exception as e:
                    print(render_panel("Error", [f"Demotion failed: {e}"], status="error"))

    elif args.command == "identity":
        from harness_knowledge.identity_resolver import SemanticIdentityResolver
        resolver = SemanticIdentityResolver(eng)
        if args.action == "scan":
            duplicates = resolver.scan_duplicates(args.project or None)
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
                registry = eng._load_registry(args.project or None)
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
                from harness_knowledge.evolution import ConceptEvolutionEngine
                evolve_engine = ConceptEvolutionEngine(eng)
                res = evolve_engine.evolve_concept(args.concept_id, args.project or None)
                if res.get("status") == "error":
                    print(render_panel("Evolution Failure", [res.get("message", "")], status="error"))
                else:
                    print(render_panel("Concept Evolved", [res.get("message", "")], status="ok"))
        elif args.action == "health":
            registry = eng._load_registry(args.project or None)
            from harness_knowledge.health import calculate_concept_health
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

    elif args.command == "contradictions":
        from harness_knowledge.evolution import ContradictionDetector
        detector = ContradictionDetector(eng)
        contradictions = detector.detect_contradictions(args.project or None)
        lines = [f"Contradictions detected: {len(contradictions)}", ""]
        for c in contradictions:
            lines.append(f"  ❌ Conflict between {c['concept_a']} and {c['concept_b']}")
            lines.append(f"     Names: {c['name_a']} | {c['name_b']}")
            lines.append(f"     Description: {c['description']}")
            lines.append("")
        print(render_panel("Contradiction Scan", lines, status="error" if contradictions else "ok"))

    elif args.command == "merge":
        res = eng.merge_concepts(args.project or None, args.primary_id, args.secondary_id)
        if res.get("status") == "error":
            print(render_panel("Merge Failure", [res.get("message", "")], status="error"))
        else:
            print(render_panel("Concepts Merged", [res.get("message", "")], status="ok"))

    elif args.command == "run":
        run_agent(args.agent, project_dir, eng)


if __name__ == "__main__":
    main()
