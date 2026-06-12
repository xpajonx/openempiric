from __future__ import annotations

import time
import json
from pathlib import Path

from .ui import render_panel

from .engine import KnowledgeEngine


def mount_tools(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    engine = KnowledgeEngine()
    # Module-level engine is used only for lightweight init metadata and must not touch search/vector resources.

    from oem_knowledge.tools import todos, metrics
    todos.register(mcp)
    metrics.register(mcp)

    @mcp.tool()
    def knowledge_init(project: str = "") -> str:
        """Bootstrap the .harness/ framework in a project directory. If project is empty, uses current directory."""
        target = project or "."
        try:
            res = engine.init_project(target)
        except Exception as e:
            return render_panel(
                "Initialization Failure", [f"Error: {e}"], status="error"
            )

        lines = [res["message"], "", "Created Directories:"]
        for d in res.get("created_directories", []):
            lines.append(f"  📁 {d}")
        lines.append("")
        lines.append("Created Baseline Documents:")
        for f in res.get("created_files", []):
            lines.append(f"  📄 {f}")
        return render_panel("OpenEmpiric Initialized", lines, status="bootstrap")



    @mcp.tool()
    def knowledge_index(force: bool = False, project: str = "") -> str:
        """Re-index all markdown files in the project's .harness/directives/ tree.

        Args:
            force: If True, re-index everything. If False, only new/changed files.
            project: Project directory path. Defaults to current directory.
        """
        start = time.time()
        try:
            with KnowledgeEngine(project or None) as eng:
                s = eng.search.index_all(force=force)
        except Exception as e:
            return render_panel("Index Failure", [f"Error: {e}"], status="error")

        duration = time.time() - start
        lines = [
            f"Files Scanned: {s.get('scanned', 0)}",
            f"New:           {s.get('new', 0)}",
            f"Updated:       {s.get('updated', 0)}",
            f"Unchanged:     {s.get('unchanged', 0)}",
            f"Failed:        {s.get('failed', 0)}",
            f"Duration:      {duration:.2f}s",
        ]
        return render_panel("Knowledge Index", lines, status="index")



    @mcp.tool()
    def knowledge_reflect(
        project: str = "",
        conversation_text: str = "",
        session_id: str = "",
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> str:
        """Extract structured Knowledge Events from conversation text and write a session report.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text or structured knowledge events.
            session_id: Optional session ID for correlation.
            events: Optional pre-extracted structured events.
            extraction_mode: Mode to use: 'auto', 'structured', 'markers', or 'llm'.
            timeout_seconds: Optional timeout in seconds for LLM extraction.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.reflection.reflect_session(
                    project=project or None,
                    conversation_text=conversation_text,
                    session_id=session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
        except Exception as e:
            # For JSON request contexts, we want to return structured JSON errors
            if events is not None or extraction_mode != "auto":
                return json.dumps({
                    "status": "error",
                    "mode": extraction_mode,
                    "events_written": 0,
                    "events_rejected": 0,
                    "warnings": [str(e)],
                    "suggestion": "Check the tool arguments or workspace lock status."
                }, indent=2)
            return render_panel("Reflection Failure", [f"Error: {e}"], status="error")

        status = res.get("status")
        if events is not None or status in ("partial", "empty") or extraction_mode != "auto":
            return json.dumps({
                "status": status,
                "mode": res.get("mode", extraction_mode),
                "events_written": res.get("events_written", 0),
                "events_rejected": res.get("events_rejected", 0),
                "warnings": res.get("warnings", []),
                "suggestion": res.get("suggestion"),
                "message": res.get("message"),
                "failed_step": res.get("failed_step")
            }, indent=2)

        events_list = res.get("knowledge_events", [])
        lines = [
            f"Report: {Path(res.get('report_path', '')).name}",
            "",
            "Knowledge Events Extracted:",
        ]
        for ev in events_list:
            lines.append(f"  - [{ev.get('type', '').upper()}] {ev.get('concept', '')}")
        if not events_list:
            lines.append("  - None")
        if res.get("warnings"):
            lines.extend([
                "",
                "Warnings:",
                *[f"  - ⚠ {w}" for w in res["warnings"]]
            ])
        return render_panel("Session Reflection", lines, status="ok")

    @mcp.tool()
    def knowledge_read(scope: str = "project", project: str = "") -> str:
        """Read the project memory baseline before planning.

        Args:
            scope: The scope of memory to read (defaults to 'project').
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.knowledge_read(project or None, scope)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_read",
                "message": str(e),
                "failed_step": "read",
                "warnings": [str(e)],
                "errors": [str(e)],
                "suggestion": "Check the tool arguments or workspace lock status."
            }, indent=2)

        return json.dumps(res, indent=2)

    @mcp.tool()
    def knowledge_materialize(project: str = "") -> str:
        """Promote candidate/emerging concepts to validated/canonical status and materialize markdown nodes.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.materialization.materialize_concepts(project or None)
        except Exception as e:
            return render_panel(
                "Materialization Failure", [f"Error: {e}"], status="error"
            )

        lines = [res.get("message", "Materialization complete."), ""]
        if res.get("materialized"):
            for m in res["materialized"]:
                lines.append(f"  ✨ {m}")
        else:
            lines.append("  No new or modified concepts.")
        return render_panel("Concept Materialization", lines, status="ok")



    def _commit_session_from_tool(
        eng: KnowledgeEngine,
        project: str,
        conversation_text: str,
        session_id: str,
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> str:
        try:
            res = eng.session_commit(
                project or None,
                conversation_text,
                session_id,
                events=events,
                extraction_mode=extraction_mode,
                timeout_seconds=timeout_seconds,
            )
        except Exception as e:
            return f"# Session End Failure\n\nError: {e}"

        status = res.get("status", "success")
        failed_step = res.get("failed_step")
        warnings = res.get("warnings", [])
        timings = res.get("phase_timings", {})

        if status == "empty":
            lines = [
                "# Session End / Commit Empty",
                "",
                "No extractable knowledge events found.",
            ]
            if res.get("suggestion"):
                lines.extend([
                    "",
                    "### Suggestion:",
                    res["suggestion"]
                ])
            if warnings:
                lines.extend([
                    "",
                    "### Warnings:",
                    *[f"- ⚠ {w}" for w in warnings]
                ])
            return "\n".join(lines)

        if status == "partial" and failed_step == "llm_extraction":
            lines = [
                "# Session End / Commit Partial",
                "",
                res.get("message", "LLM extraction timed out. No events were written."),
            ]
            if res.get("suggestion"):
                lines.extend([
                    "",
                    "### Suggestion:",
                    res["suggestion"]
                ])
            if warnings:
                lines.extend([
                    "",
                    "### Warnings:",
                    *[f"- ⚠ {w}" for w in warnings]
                ])
            return "\n".join(lines)

        if status == "error":
            failed_step = res.get("failed_step", "reflection/materialization")
            reason = res.get("message", "Unknown failure")
            if "lock" in reason.lower() or "lock" in failed_step.lower():
                header = "# Session End Failed"
                body = f"OEM could not acquire the project memory lock.\n\nReason: {reason}\n\nAnother OEM process may still be committing memory. Retry after it finishes."
            else:
                header = "# Session End Failure"
                body = f"Session reflection completed, but materialization/reflection failed.\n\nFailed step: {failed_step}  \nReason: {reason}\n\nYour conversation was not fully committed to OEM memory. Please retry session end after fixing the issue."
            
            lines = [header, "", body]
            if warnings:
                lines.append("\n### Warnings:")
                for w in warnings:
                    lines.append(f"- ⚠ {w}")
            if timings:
                lines.append("\n### Timing:")
                for k, v in timings.items():
                    if k == "total":
                        continue
                    if k == "search_index" and (v == 0.0 or failed_step == "indexing"):
                        lines.append(f"- {k}: skipped")
                    else:
                        lines.append(f"- {k}: {v:.1f}s")
                if "total" in timings:
                    lines.append(f"- total: {timings['total']:.1f}s")
            return "\n".join(lines)

        from pathlib import Path
        events_list = res.get("knowledge_events", [])
        event_counts: dict[str, int] = {}
        for ev in events_list:
            t = ev.get("type") or ev.get("event_type", "observation")
            event_counts[t] = event_counts.get(t, 0) + 1

        if status == "partial":
            status_line = "Session commit completed partially. Some steps succeeded, but others had failures (see warnings below)."
        else:
            status_line = "Session ended successfully / Session commit succeeded."

        lines = [
            "# Session End / Commit Complete",
            "",
            status_line,
        ]
        if res.get("report_path"):
            lines.append(f"**Report**: {Path(res.get('report_path')).name}")
            
        if warnings:
            lines.extend([
                "",
                "### Warnings:",
                *[f"- ⚠ {w}" for w in warnings]
            ])
            
        lines.extend([
            "",
            "### Extracted Knowledge Events:",
        ])
        if event_counts:
            for t, c in sorted(event_counts.items()):
                lines.append(f"- **{t.title()}**: {c} events")
        else:
            lines.append("- None")

        lines.extend(
            [
                "",
                "### Graph & Index Updates:",
                f"- **Materialized**: {len(res.get('materialized_log', []))} concepts",
                f"- **Links updated**: {res.get('links_updated', 0)}",
                f"- **Index**: {res.get('index_stats', {}).get('new', 0)} new, {res.get('index_stats', {}).get('updated', 0)} updated",
            ]
        )

        if timings:
            lines.append("\n### Timing:")
            for k, v in timings.items():
                if k == "total":
                    continue
                if k == "search_index" and (v == 0.0 or failed_step == "indexing"):
                    lines.append(f"- {k}: skipped")
                else:
                    lines.append(f"- {k}: {v:.1f}s")
            if "total" in timings:
                lines.append(f"- total: {timings['total']:.1f}s")

        if res.get("notification"):
            lines.extend([
                "",
                "### Notification:",
                res["notification"]
            ])

        return "\n".join(lines)

    @mcp.tool()
    def knowledge_session_commit(
        project: str = "",
        conversation_text: str = "",
        session_id: str = "",
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> str:
        """Deprecated internal lifecycle hook.

        Invoked automatically by OEM runtime.
        Agents should not call this tool directly.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text for knowledge extraction.
            session_id: Optional session ID for correlation.
            events: Optional pre-extracted structured events.
            extraction_mode: Mode to use: 'auto', 'structured', 'markers', or 'llm'.
            timeout_seconds: Optional timeout in seconds for LLM extraction.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                return _commit_session_from_tool(
                    eng,
                    project,
                    conversation_text,
                    session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
        except Exception as e:
            return f"# Session Commit Failure\n\nError: {e}"

    @mcp.tool()
    def knowledge_session_end(
        project: str = "",
        conversation_text: str = "",
        session_id: str = "",
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> str:
        """End the current knowledge session, trigger reflection/materialization, update graph, and re-index.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text or history for knowledge extraction.
            session_id: Optional session ID.
            events: Optional pre-extracted structured events.
            extraction_mode: Mode to use: 'auto', 'structured', 'markers', or 'llm'.
            timeout_seconds: Optional timeout in seconds for LLM extraction.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                return _commit_session_from_tool(
                    eng,
                    project,
                    conversation_text,
                    session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
        except Exception as e:
            return f"# Session End Failure\n\nError: {e}"



    @mcp.tool()
    def knowledge_get_events(
        project: str = "", concept: str = "", event_type: str = "", session_id: str = ""
    ) -> str:
        """Query knowledge events filtered by concept, event_type, or session_id.

        Args:
            project: Project directory path. Defaults to current directory.
            concept: Filter by concept name
            event_type: Filter by event type (hypothesis, experiment, etc.)
            session_id: Filter by session ID
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                events = eng.state.get_events(
                    project or None,
                    concept=concept,
                    event_type=event_type,
                    session_id=session_id,
                )
        except Exception as e:
            return render_panel("Get Events Failure", [f"Error: {e}"], status="error")

        lines = [f"Total events: {len(events)}", ""]
        for idx, ev in enumerate(events):
            lines.append(
                f"{idx + 1}. [{ev.get('event_type', '').upper()}] {ev.get('summary', '')}"
            )
            lines.append(f"   ID: {ev.get('event_id', '')}")
            lines.append(f"   Evidence: {ev.get('evidence', '')[:100]}")
            lines.append("")
        if not events:
            lines.append("No matching events.")
        return render_panel("Knowledge Events", lines, status="ok")

    @mcp.tool()
    def knowledge_get_event(project: str = "", event_id: str = "") -> str:
        """Retrieve a single knowledge event by its UUID.

        Args:
            project: Project directory path. Defaults to current directory.
            event_id: The event UUID
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                ev = eng.state.get_event(project or None, event_id)
        except KeyError as e:
            return render_panel("Event Not Found", [str(e)], status="error")
        except Exception as e:
            return render_panel("Get Event Failure", [f"Error: {e}"], status="error")

        lines = [
            f"Event: {ev.get('event_id', '')}",
            f"Type: {ev.get('event_type', '')}",
            f"Summary: {ev.get('summary', '')}",
            f"Evidence: {ev.get('evidence', '')}",
            f"Confidence: {ev.get('confidence', '')}",
            f"Session: {ev.get('session_id', '')}",
        ]
        return render_panel("Knowledge Event Detail", lines, status="ok")



    @mcp.tool()
    def knowledge_merge_concepts(
        project: str = "", primary_id: str = "", secondary_id: str = ""
    ) -> str:
        """Merge a secondary concept into a primary concept.

        Args:
            project: Project directory path. Defaults to current directory.
            primary_id: The UUID of the concept to keep
            secondary_id: The UUID of the concept to merge and remove
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.state.merge_concepts(project or None, primary_id, secondary_id)
        except Exception as e:
            return render_panel("Merge Failure", [f"Error: {e}"], status="error")

        if res.get("status") == "error":
            return render_panel("Merge Error", [res.get("message", "")], status="error")

        return render_panel(
            "Concepts Merged", [res.get("message", "")], status="organize"
        )





    @mcp.tool()
    def knowledge_search(query: str, k: int = 3, project: str = "") -> str:
        """Fast lookup and term-based search across concepts.

        Args:
            query: Search query
            k: Number of results to return. Defaults to 3.
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                results = eng.search.search(query, k=k)
        except Exception as e:
            return render_panel("Search Failure", [f"Error: {e}"], status="error")

        if not results:
            return render_panel(f"Search: 0 results", [f"No matches for: '{query}'"], status="search")

        lines = [f"Query: \"{query}\"", f"Results: {len(results)}", ""]
        for idx, r in enumerate(results):
            meta = r.get("metadata", {})
            rel_path = meta.get("file_path", "")
            if rel_path:
                try:
                    rel_path = str(Path(rel_path).relative_to(Path(project or ".")))
                except ValueError:
                    rel_path = Path(rel_path).name
            else:
                rel_path = r.get("id", "unknown")

            doc_text = r.get("document", "")
            snippet = doc_text.split("\n")[0][:150] if doc_text else "No content available."
            score = r.get("score", 0.0)

            lines.append(f"{idx + 1}. [{rel_path}] (score: {score:.4f})")
            lines.append(f"   {snippet}...")
            lines.append("")

        return render_panel("Knowledge Search Results", lines, status="search")

    @mcp.tool()
    def knowledge_health_check(
        stale_sessions: int = 5, similarity_threshold: float = 0.85, project: str = ""
    ) -> str:
        """Scan the knowledge base for stale concepts, duplicate concepts (merge proposals), and architectural contradictions.

        Args:
            stale_sessions: Number of sessions threshold to consider a concept stale. Defaults to 5.
            similarity_threshold: Similarity threshold to propose merges. Defaults to 0.85.
            project: Project directory path. Defaults to current directory.
        """
        try:
            from oem_knowledge.health import build_runtime_health
            res = build_runtime_health(project or None)

            with KnowledgeEngine(project or None) as eng:
                stale = eng.state.detect_stale_concepts(stale_sessions, project or None)
                merges = eng.propose_merges(similarity_threshold, project or None)
                conflicts = eng.detect_contradictions(project or None)
        except Exception as e:
            err_data = {
                "status": "error",
                "operation": "knowledge_health_check",
                "message": str(e),
                "failed_step": "knowledge_health_check",
                "warnings": [],
                "errors": [str(e)],
                "suggestion": "Ensure the project directory exists and contains valid .oem/ memory files."
            }
            return json.dumps(err_data, indent=2)

        lines = []
        
        # Runtime Checks
        lines.append("Runtime Checks:")
        for c in res["runtime"]["checks"]:
            symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
            lines.append(f"  {symbol} {c['name']}")
        lines.append("")

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
            
        return render_panel("Knowledge Health Scan", lines, status="stats")

    @mcp.tool()
    def knowledge_stats(project: str = "") -> str:
        """Show oem/ knowledge statistics.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                registry = eng.state._load_registry(project or None)
                harness = eng._resolve_harness(project or None)
                db_path = harness / ".local_vector_db"
                db_size = 0
                if db_path.exists():
                    def get_files_size(p: Path) -> int:
                        if p.is_file():
                            return p.stat().st_size
                        elif p.is_dir():
                            return sum(get_files_size(f) for f in p.iterdir())
                        return 0
                    db_size = get_files_size(db_path)

                lines = [
                    f"Total Concepts:       {len(registry)}",
                    f"Vector DB Size:       {(db_size / (1024 * 1024)):.2f} MB",
                    f"OEM Path:             {harness}"
                ]
                return render_panel("Knowledge Stats", lines, status="stats")
        except Exception as e:
            return render_panel("Stats Failure", [f"Error: {e}"], status="error")

    @mcp.tool()
    def knowledge_explain_concept(concept_id: str, project: str = "") -> str:
        """Explain a concept and its details from the registry.

        Args:
            concept_id: Concept ID (e.g. concept_001)
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                registry = eng.state._load_registry(project or None)
                cdata = registry.get(concept_id)
                if not cdata:
                    return render_panel("Concept Not Found", [f"Concept {concept_id} not in registry."], status="error")

                harness = eng._resolve_harness(project or None)
                wiki_file = harness / "wiki" / f"{concept_id}.md"
                recent_evidence = []
                if wiki_file.exists():
                    content = wiki_file.read_text(encoding="utf-8")
                    import re
                    ev_match = re.search(r"## Learnings.*?\n([\s\S]*)", content)
                    if ev_match and ev_match.group(1):
                        recent_evidence = [
                            line.strip().lstrip("-").strip()
                            for line in ev_match.group(1).split("\n")
                            if line.strip().startswith("-")
                        ]

                lines = [
                    f"Concept: {cdata.get('canonical_name', '').replace('-', ' ').upper()} ({concept_id})",
                    f"Status: {cdata.get('status', '').upper()}",
                    f"Confidence: {cdata.get('confidence', 1)}/5",
                    f"Aliases: {', '.join(cdata.get('aliases', []))}",
                    "",
                    "Recent Evidence:"
                ]
                if recent_evidence:
                    lines.extend(f"  - {e}" for e in recent_evidence)
                else:
                    lines.append("  - None")

                return render_panel("Concept Explanation", lines, status="ok")
        except Exception as e:
            return render_panel("Explanation Failure", [f"Error: {e}"], status="error")



    @mcp.tool()
    def knowledge_skill_candidates(project: str = "") -> str:
        """List all skill candidates.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                candidates = eng.skills.list_skill_candidates(project or None)
                if not candidates:
                    return "No skill candidates found."
                
                lines = [
                    "| Slug | Title | Confidence | Status | Evidence Count |",
                    "| --- | --- | --- | --- | --- |"
                ]
                for c in candidates:
                    lines.append(f"| {c.slug} | {c.title} | {c.confidence} | {c.status} | {len(c.evidence)} |")
                return "\n".join(lines)
        except Exception as e:
            return f"Status: error\nReason: {e}"

    @mcp.tool()
    def knowledge_skill_candidate_show(slug: str, project: str = "") -> str:
        """Show detailed candidate or approved skill.

        Args:
            slug: The slug of the skill candidate or approved skill.
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                candidate = eng.skills.load_skill_candidate(slug, project or None)
                if not candidate:
                    layout = eng.layout(project or None)
                    approved_path = layout.skills_dir / f"{slug}.md"
                    if approved_path.exists():
                        return approved_path.read_text(encoding="utf-8")
                    return f"Status: error\nReason: Candidate/Skill '{slug}' not found."
                
                # Format candidate details as simple markdown
                lines = [
                    f"# Skill Candidate: {candidate.title}",
                    "",
                    f"- **Slug**: {candidate.slug}",
                    f"- **Status**: {candidate.status}",
                    f"- **Confidence**: {candidate.confidence}",
                    "",
                    "## Trigger",
                    candidate.trigger,
                    "",
                    "## Recommended behavior",
                    candidate.recommended_behavior,
                    "",
                    "## Rationale",
                    candidate.rationale,
                    "",
                    "## Evidence",
                ]
                for ev in candidate.evidence:
                    lines.append(f"- {ev}")
                return "\n".join(lines)
        except Exception as e:
            return f"Status: error\nReason: {e}"

    @mcp.tool()
    def knowledge_skill_candidate_approve(slug: str = "", force: bool = False, project: str = "") -> str:
        """Approve a skill candidate and promote it to a project skill.

        Args:
            slug: The slug of the skill candidate.
            force: Force approval even if rejected previously.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return "Status: error\nReason: Slug is required."
        try:
            with KnowledgeEngine(project or None) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "approved", project or None, force=force)
                if not cand:
                    return f"Status: error\nReason: Candidate '{slug}' not found."
                return (
                    "Status: approved\n"
                    f"Skill: {slug}\n"
                    f"Approved skill written: .oem/skills/{slug}.md"
                )
        except Exception as e:
            return f"Status: error\nReason: {e}"

    @mcp.tool()
    def knowledge_skill_candidate_reject(slug: str = "", project: str = "") -> str:
        """Reject a skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return "Status: error\nReason: Slug is required."
        try:
            with KnowledgeEngine(project or None) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "rejected", project or None)
                if not cand:
                    return f"Status: error\nReason: Candidate '{slug}' not found."
                return f"Status: rejected\nSkill: {slug}"
        except Exception as e:
            return f"Status: error\nReason: {e}"

    @mcp.tool()
    def knowledge_skill_candidate_defer(slug: str = "", project: str = "") -> str:
        """Defer a skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return "Status: error\nReason: Slug is required."
        try:
            with KnowledgeEngine(project or None) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "deferred", project or None)
                if not cand:
                    return f"Status: error\nReason: Candidate '{slug}' not found."
                return f"Status: deferred\nSkill: {slug}"
        except Exception as e:
            return f"Status: error\nReason: {e}"


def main() -> None:
    from oem_knowledge.engine import apply_oem_process_env_defaults
    apply_oem_process_env_defaults()

    from fastmcp import FastMCP
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    mcp.run()


if __name__ == "__main__":
    main()
