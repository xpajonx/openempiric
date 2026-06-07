from __future__ import annotations

import json
import sys
from pathlib import Path

from oem_knowledge.ui import render_panel


def run_knowledge_command(args):
    # Setup deferred logging Configuration
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    # Lazy-load to avoid eager framework imports on help/version path
    from oem_knowledge.engine import KnowledgeEngine

    eng = KnowledgeEngine(project)

    if args.command in ("status", "stats"):
        s = eng.search.stats()
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
        results = eng.search.search(args.query, k=args.k)
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

    elif args.command == "rebuild":
        res = eng.state.rebuild_registry(project)
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
        events = eng.state.get_events(
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
            ev = eng.state.get_event(project, args.event_id)
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
                history = eng.materialization.get_concept_history(args.id, project)
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
                res = eng.state.explain_concept(project, args.id)
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
                ev = eng.state.get_event(project, args.id)
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
        # Async boundary: load asyncio and run_lint locally
        import asyncio
        from oem_knowledge.linter import run_lint
        res = asyncio.run(run_lint(target, max_parallel=args.workers, fix=args.fix))
        if res["status"] == "error":
            print(render_panel("Lint Error", [res["message"]], status="error"))
            sys.exit(1)
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
            if res.get("broken_links"):
                sys.exit(1)

    elif args.command == "vault":
        from oem_knowledge.vault import GlobalVault
        vault = GlobalVault()
        if args.action == "sync":
            try:
                local_reg = eng.state._load_registry(project)
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
                registry = eng.state._load_registry(project)
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
            registry = eng.state._load_registry(project)
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

            fitness_data = eng.fitness.calculate_fitness(project)
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
                    resolved_id = eng.fitness._find_concept_id(args.concept_id, eng.state._load_registry(project))
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
                registry = eng.state._load_registry(project)
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

    elif args.command == "health":
        stale = eng.state.detect_stale_concepts(args.stale_sessions, project)
        merges = eng.propose_merges(args.similarity_threshold, project)
        conflicts = eng.detect_contradictions(project)
        
        lines = []
        
        # Stale concepts section
        lines.append("Stale Concepts:")
        if stale:
            for s in stale:
                lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - untouched for {s['sessions_since_reference']} sessions")
        else:
            lines.append("  None")
        lines.append("")
        
        # Merge proposals section
        lines.append("Duplicate Merge Proposals:")
        if merges:
            for m in merges:
                lines.append(f"  ✦ Suggest merging {m['secondary_name']} ({m['secondary_id']}) into {m['primary_name']} ({m['primary_id']})")
                lines.append(f"    Reason: {m['reason']}")
        else:
            lines.append("  None")
        lines.append("")
        
        # Contradictions section
        lines.append("Contradictions Detected:")
        if conflicts:
            for c in conflicts:
                lines.append(f"  ✗ Conflict between {c['name_a']} ({c['concept_a']}) and {c['name_b']} ({c['concept_b']})")
                lines.append(f"    Description: {c['description']}")
        else:
            lines.append("  None")
            
        print(render_panel("Knowledge Health Scan", lines, status="stats"))

    elif args.command == "merge":
        res = eng.state.merge_concepts(project, args.primary_id, args.secondary_id)
        if res.get("status") == "error":
            print(render_panel("Merge Failure", [res.get("message", "")], status="error"))
        else:
            print(render_panel("Concepts Merged", [res.get("message", "")], status="ok"))
