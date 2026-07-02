from __future__ import annotations

import json
import sys
from pathlib import Path

from oem_knowledge.ui import render_panel


def _humanize_preflight_reason(reason: str) -> str:
    labels = {
        "approved_skill_match": "approved skill matched",
        "skill_match": "skill matched",
        "concept_match": "concept matched",
        "memory_match": "memory matched",
        "no_relevant_oem_context": "no relevant OEM context",
        "project_unresolved": "project unresolved",
        "project_mismatch": "project mismatch",
        "oem_missing": ".oem missing",
        "preflight_blocked": "preflight blocked",
        "preflight_error": "unexpected preflight error",
        "unsupported_mode": "unsupported mode",
    }
    return labels.get(reason, reason.replace("_", " "))


def _preflight_next_steps(payload: dict) -> list[str]:
    steps: list[str] = []
    if payload.get("matched_skills"):
        first = payload["matched_skills"][0]
        steps.append(f'knowledge_search "{first.get("title", "")}"')
    if payload.get("source_suggestions"):
        first_source = payload["source_suggestions"][0]
        steps.append(f'knowledge_source_search "{first_source.get("title", "")}"')
    return steps[:2]


def run_knowledge_command(args):
    import sys
    from oem_knowledge.fs import LockTimeoutError
    try:
        _run_knowledge_command_impl(args)
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

def _run_knowledge_command_impl(args):
    # Setup deferred logging Configuration
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    # Lazy-load to avoid eager framework imports on help/version path
    from oem_knowledge.engine import KnowledgeEngine

    eng = KnowledgeEngine(project)
    import atexit; atexit.register(eng.close)

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
        if getattr(args, "debug_ranking", False):
            report = eng.search.debug_ranking(args.query, k=args.k)
            print(json.dumps(report, indent=2))
            return
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

    elif args.command == "read":
        limit = getattr(args, "limit", 10)
        scope = getattr(args, "scope", "project")
        res = eng.knowledge_read(project, scope=scope, limit=limit)

        if res.get("status") in ("error", "not_implemented"):
            panel_lines = [res.get("message", "Unknown error.")]
            if res.get("suggestion"):
                panel_lines += ["", f"Suggestion: {res['suggestion']}"]
            print(render_panel("Read Error", panel_lines, status="error"))
            sys.exit(1)

        sections = res.get("sections", {})

        def _section_lines(title: str, key: str) -> list[str]:
            items = sections.get(key, [])
            if not items:
                return [f"{title}:", "  (none)", ""]
            return [f"{title}:"] + [f"  - {item}" for item in items[:limit]] + [""]

        lines = [
            res.get("message", "OEM project memory baseline loaded."),
            "",
        ]
        if "project" in sections:
            lines += _section_lines("Project", "project")
        if "runtime_status" in sections:
            lines += _section_lines("Runtime", "runtime_status")
        if "contradictions" in sections:
            contradictions = sections.get("contradictions", [])
            lines.append("Contradictions:")
            if contradictions:
                for c in contradictions[:limit]:
                    symbol = "✗" if c.get("severity") == "error" else "⚠"
                    lines.append(f"  {symbol} {c.get('type')}")
                    for source, detail in c.get("sources", {}).items():
                        lines.append(f"    {source}: {detail.get('project') or detail.get('value')}")
            else:
                lines.append("  (none)")
            lines.append("")
        if "active_project" in sections:
            active_project = sections.get("active_project", {})
            lines.append("Active project:")
            selected = active_project.get("selected_project") or active_project.get("latest_project")
            lines.append(f"  - selected: {selected or '(none)'}")
            lines.append(f"  - source: {active_project.get('selected_source') or '(none)'}")
            lines.append("")
        if "recent_sessions" in sections:
            lines += _section_lines("Recent memory", "recent_sessions")
        if "important_concepts" in sections:
            lines += _section_lines("Important concepts", "important_concepts")
        if "approved_skills" in sections:
            lines += _section_lines("Approved skills", "approved_skills")
        if "suggested_next_searches" in sections:
            lines += _section_lines("Suggested next searches", "suggested_next_searches")

        if res.get("warnings"):
            lines += ["Warnings:"] + [f"  ⚠ {w}" for w in res["warnings"]] + [""]
        if res.get("suggestion"):
            lines += [f"Tip: {res['suggestion']}"]

        print(render_panel("Project Memory Baseline", lines, status="ok"))

    elif args.command == "preflight":
        payload = eng.preflight(
            task=args.task,
            project=project,
            limit=getattr(args, "limit", 8),
            write_audit=not getattr(args, "no_audit", False),
        )

        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2))
            if payload.get("status") == "error":
                sys.exit(1)
            return

        lines = [
            f"Decision: {payload.get('decision', 'error')}",
            f"Reason: {_humanize_preflight_reason(payload.get('reason', 'preflight_error'))}",
            "",
        ]

        matched_skills = payload.get("matched_skills", [])
        lines.append("Matched skills:")
        if matched_skills:
            lines.extend([f"  - {item.get('title', '')}" for item in matched_skills])
        else:
            lines.append("  - (none)")
        lines.append("")

        relevant_memory = payload.get("matched_memory", [])
        lines.append("Relevant memory:")
        if relevant_memory:
            lines.extend(
                [
                    f"  - {item.get('snippet') or item.get('title', '')}"
                    for item in relevant_memory[: getattr(args, 'limit', 8)]
                ]
            )
        elif matched_skills and matched_skills[0].get("snippet"):
            lines.append(f"  - {matched_skills[0]['snippet']}")
        elif payload.get("matched_concepts") and payload["matched_concepts"][0].get("snippet"):
            lines.append(f"  - {payload['matched_concepts'][0]['snippet']}")
        else:
            lines.append("  - (none)")
        lines.append("")

        next_steps = _preflight_next_steps(payload)
        lines.append("Suggested next steps:")
        if next_steps:
            lines.extend([f"  - {step}" for step in next_steps])
        else:
            lines.append("  - Proceed normally.")

        if payload.get("warnings"):
            lines.extend(["", "Warnings:"])
            lines.extend([f"  - ⚠ {warning}" for warning in payload["warnings"]])

        print(render_panel("OEM Preflight", lines, status="error" if payload.get("status") == "error" else "ok"))
        if payload.get("status") == "error":
            sys.exit(1)

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

    elif args.command == "index":
        res = eng.search.index_all(force=True)
        if res.get("status") == "error":
            print(render_panel(
                "Indexing Failure",
                [res.get("error", "Unknown error")],
                status="error"
            ))
            sys.exit(1)
        else:
            print(render_panel(
                "Indexing Complete",
                ["Search index rebuilt successfully."],
                status="ok"
            ))

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
        from oem_knowledge.health import build_health_report
        health_res = build_health_report(project)

        stale = eng.state.detect_stale_concepts(args.stale_sessions, project)
        merges = eng.propose_merges(args.similarity_threshold, project)
        concept_conflicts = eng.detect_contradictions(project)

        # Compute stale reference summary
        registry = eng.state._load_registry(project)
        total_concepts = len(registry)

        active_stale = sum(1 for s in stale if s.get("stale_status") == "stale")
        unknown_ref = sum(1 for s in stale if s.get("stale_status") in (
            "legacy_no_reference_metadata", "reference_metadata_missing",
            "reference_session_missing", "reference_history_unavailable",
            "never_referenced_since_tracking_enabled"
        ))

        unknown_breakdown = {}
        for status in (
            "legacy_no_reference_metadata", "reference_metadata_missing",
            "reference_session_missing", "reference_history_unavailable",
            "never_referenced_since_tracking_enabled"
        ):
            count = sum(1 for s in stale if s.get("stale_status") == status)
            if count > 0:
                unknown_breakdown[status] = count

        known_recent = total_concepts - active_stale - unknown_ref
        stale_ref_summary = {
            "active_stale": active_stale,
            "unknown_reference": unknown_ref,
            "known_recent": max(0, known_recent),
            **unknown_breakdown
        }

        health_res["stale_reference_summary"] = stale_ref_summary
        health_res["stale_concepts"] = stale

        if getattr(args, "json", False):
            health_res["duplicate_merge_proposals"] = merges
            health_res["concept_contradictions"] = concept_conflicts
            print(json.dumps(health_res, indent=2))
            sys.exit(0)
            return

        lines = []

        # Runtime health section
        lines.append("Runtime Checks:")
        for check in health_res["runtime"]["checks"]:
            symbol = "✓" if check["status"] == "success" else ("⚠" if check["status"] == "warn" else "✗")
            lines.append(f"  {symbol} {check['name']}")
        lines.append("")
        
        # Concept Integrity section
        concept_integrity = health_res.get("concept_integrity", {})
        lines.append("Concept Integrity:")
        if concept_integrity.get("checks"):
            for check in concept_integrity["checks"]:
                symbol = "✓" if check["status"] == "success" else ("⚠" if check["status"] == "warn" else "✗")
                lines.append(f"  {symbol} {check['name']}")
        else:
            lines.append("  None")
        lines.append("")

        # Stale concepts section
        lines.append("Stale Concepts:")
        if unknown_ref > 0:
            lines.append(
                f"  Stale reference metadata: {unknown_ref} concepts have unknown reference history, "
                "likely legacy concepts from before reference tracking. This is informational and not a release blocker."
            )
            lines.append("")

        if stale:
            for s in stale:
                status = s.get("stale_status")
                if status == "legacy_no_reference_metadata":
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference session unknown")
                elif status == "reference_metadata_missing":
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference session unknown")
                elif status == "reference_session_missing":
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference session unknown")
                elif status == "reference_history_unavailable":
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference history unavailable")
                elif status == "never_referenced_since_tracking_enabled":
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference session unknown")
                else:
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
        if health_res.get("contradictions"):
            for c in health_res["contradictions"]:
                symbol = "✗" if c.get("severity") == "error" else "⚠"
                lines.append(f"  {symbol} {c.get('type')}")
                for source, detail in c.get("sources", {}).items():
                    lines.append(f"    {source}: {detail.get('project') or detail.get('value')}")
        else:
            lines.append("  None")

        lines.append("")
        lines.append("Concept Contradictions Detected:")
        if concept_conflicts:
            for c in concept_conflicts:
                lines.append(f"  ✗ Conflict between {c['name_a']} ({c['concept_a']}) and {c['name_b']} ({c['concept_b']})")
        else:
            lines.append("  None")
             
        print(render_panel("Knowledge Health Scan", lines, status="stats"))


    elif args.command == "merge":
        res = eng.state.merge_concepts(project, args.primary_id, args.secondary_id)
        if res.get("status") == "error":
            print(render_panel("Merge Failure", [res.get("message", "")], status="error"))
        else:
            print(render_panel("Concepts Merged", [res.get("message", "")], status="ok"))

    elif args.command == "source":
        if args.source_command == "index":
            res = eng.source.index(force=args.force, dry_run=args.dry_run)
            if res.get("status") == "error":
                print(render_panel(
                    "Source Indexing Failure",
                    [res.get("message", "Unknown error")],
                    status="error"
                ))
                sys.exit(1)
            else:
                summary = res.get("summary", {})
                lines = [
                    f"Operation:         {res.get('mode', 'write').upper()}",
                    f"Scanned Files:     {res.get('scanned_files', 0)}",
                    f"Indexed Files:     {res.get('indexed_files', 0)}",
                    f"Metadata-only:     {res.get('metadata_only_files', 0)}",
                    f"New Files:         {res.get('new_files', 0)}",
                    f"Updated Files:     {res.get('updated_files', 0)}",
                    f"Removed Files:     {res.get('removed_files', 0)}",
                    f"Total Chunks:      {res.get('total_chunks', 0)}",
                    f"Est. Tokens:       {res.get('estimated_source_tokens', 0)}",
                ]
                if res.get("warnings"):
                    lines += ["", "Warnings:"] + [f"  ⚠ {w}" for w in res["warnings"]]
                print(render_panel(
                    "Source Indexing Complete" if not args.dry_run else "Source Indexing (Dry Run)",
                    lines,
                    status="ok"
                ))

        elif args.source_command == "search":
            res = eng.source.search(args.query, k=args.k)
            if res.get("status") == "error":
                print(render_panel(
                    "Source Search Failure",
                    [res.get("message", "Unknown error.")],
                    status="error"
                ))
                sys.exit(1)
            results = res.get("results", [])
            lines = [f'Query: "{args.query}"', f"Results: {len(results)}", ""]
            for idx, r in enumerate(results):
                meta = r.get("metadata", {})
                rel_path = meta.get("rel_path", "unknown")
                start = meta.get("start_line")
                end = meta.get("end_line")
                loc = f"L{start}-{end}" if start and end else "metadata"
                lines.append(
                    f"{idx + 1}. [{rel_path}#{loc}] (score: {r['score']:.4f})"
                )
                lines.append(f"   {r['document'][:150]}...")
                lines.append("")
            if not results:
                lines = [f"No matches for: '{args.query}'"]
            print(render_panel("Source Search Results", lines, status="search"))

        elif args.source_command == "read":
            res = eng.source.read(args.path, start_line=args.start_line, end_line=args.end_line)
            if res.get("status") == "error":
                panel_lines = [res.get("message", "Unknown error.")]
                if res.get("suggestion"):
                    panel_lines += ["", f"Suggestion: {res['suggestion']}"]
                print(render_panel("Source Read Error", panel_lines, status="error"))
                sys.exit(1)
            
            content = res.get("content", "")
            line_range = res.get("line_range", {})
            warnings = res.get("warnings", [])
            
            lines = [
                f"File: {res.get('path')}",
                f"Range: {line_range.get('start')}-{line_range.get('end')} (Total lines: {line_range.get('total_lines')})",
                "",
                content,
                ""
            ]
            if warnings:
                lines += ["Warnings:"] + [f"  ⚠ {w}" for w in warnings] + [""]
            if res.get("suggestion"):
                lines += [f"Tip: {res['suggestion']}"]

            print(render_panel("Source Content", lines, status="ok"))

        elif args.source_command == "stats":
            res = eng.source.stats()
            if res.get("status") == "error":
                print(render_panel("Source Stats Error", [res.get("message", "Unknown error")], status="error"))
                sys.exit(1)
            s = res.get("summary", {})
            lines = [
                f"Baseline Tokens:     {s.get('estimated_baseline_tokens', 0)}",
                f"Retrieval Tokens:    {s.get('estimated_retrieval_tokens', 0)}",
                f"Estimated Savings:   {s.get('estimated_savings', 0)}",
                f"Database Size:       {s.get('db_size_mb', 0.0):.4f} MB",
                f"Total Chunks:        {s.get('total_chunks', 0)}",
                f"Indexed Files:       {s.get('indexed_files', 0)}",
                f"Metadata-only:       {s.get('metadata_only_files', 0)}",
                f"Skipped Large Files: {s.get('skipped_large_files', 0)}",
            ]
            if res.get("warnings"):
                lines += ["", "Warnings:"] + [f"  ⚠ {w}" for w in res["warnings"]]
            print(render_panel("Source Stats", lines, status="stats"))

    elif args.command == "active-work":
        if getattr(args, "active_work_action", None) == "repair":
            from oem_knowledge.runtime.active_work import repair_active_work
            dry = bool(getattr(args, "dry_run", False))
            app = bool(getattr(args, "apply", False))
            bkp = getattr(args, "backup", None)
            if not dry and not app:
                # default to dry-run for safety
                dry = True
            harness = eng._resolve_harness(project)
            res = repair_active_work(harness, dry_run=dry, apply=app, backup=bkp)
            status = "ok" if res.get("status") in ("ok", "repaired", "no_changes_needed") else ("stats" if res.get("status") in ("conflict_detected", "repair_needed", "noop") else "error")
            lines: list[str] = [
                f"Mode: {res.get('mode')}",
                f"Memory root: {res.get('memory_root')}",
                f"Workspace root: {res.get('workspace_root')}",
                f"Status: {res.get('status')}",
                f"Backup: {res.get('backup_dir') or 'none'}",
            ]
            if res.get("planned_changes"):
                lines += ["", "Planned changes:"]
                for c in res["planned_changes"]:
                    lines.append(f"  - {c.get('action')}: {c.get('file')}")
            if res.get("changes_applied"):
                lines += ["", "Applied:"]
                for c in res["changes_applied"]:
                    lines.append(f"  - {c.get('action')}: {c.get('file')}")
            if res.get("detected_conflicts"):
                lines += ["", "Conflicts (pre-repair):"]
                for c in res["detected_conflicts"]:
                    lines.append(f"  {c.get('type')}: {', '.join(c.get('sources', []))}")
            print(render_panel("Active-Work Repair", lines, status=status))
            if res.get("status") == "error":
                sys.exit(1)
