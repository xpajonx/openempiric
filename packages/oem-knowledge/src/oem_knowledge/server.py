from __future__ import annotations

import time
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
        project: str = "", conversation_text: str = "", session_id: str = ""
    ) -> str:
        """Extract structured Knowledge Events from conversation text and write a session report.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text or structured knowledge events.
            session_id: Optional session ID for correlation.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.reflection.reflect_session(project or None, conversation_text, session_id)
        except Exception as e:
            return render_panel("Reflection Failure", [f"Error: {e}"], status="error")

        events = res.get("knowledge_events", [])
        lines = [
            f"Report: {Path(res.get('report_path', '')).name}",
            "",
            "Knowledge Events Extracted:",
        ]
        for ev in events:
            lines.append(f"  - [{ev.get('type', '').upper()}] {ev.get('concept', '')}")
        if not events:
            lines.append("  - None")
        if res.get("warnings"):
            lines.extend([
                "",
                "Warnings:",
                *[f"  - ⚠ {w}" for w in res["warnings"]]
            ])
        return render_panel("Session Reflection", lines, status="ok")

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

    @mcp.tool()
    def knowledge_update_graph(project: str = "") -> str:
        """Update bidirectional [[concept_id|Name]] wikilinks between materialized concept nodes.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.materialization.update_graph(project or None)
        except Exception as e:
            return render_panel("Graph Update Failure", [f"Error: {e}"], status="error")

        lines = [
            res.get("message", "Graph updated."),
            f"Links added/updated: {res.get('links_updated', 0)}",
            f"Files scanned: {res.get('files_scanned', 0)}",
        ]
        return render_panel("Knowledge Graph", lines, status="organize")

    def _commit_session_from_tool(
        eng: KnowledgeEngine, project: str, conversation_text: str, session_id: str
    ) -> str:
        try:
            res = eng.session_commit(project or None, conversation_text, session_id)
        except Exception as e:
            return f"# Session End Failure\n\nError: {e}"

        if res.get("status") == "error":
            failed_step = res.get("failed_step", "reflection/materialization")
            reason = res.get("message", "Unknown failure")
            if "lock" in reason.lower() or "lock" in failed_step.lower():
                return f"""# Session End Failed

OEM could not acquire the project memory lock.

Reason: {reason}

Another OEM process may still be committing memory. Retry after it finishes."""
            
            return f"""# Session End Failure

Session reflection completed, but materialization/reflection failed.

Failed step: {failed_step}  
Reason: {reason}

Your conversation was not fully committed to OEM memory. Please retry session end after fixing the issue."""

        events = res.get("knowledge_events", [])
        event_counts: dict[str, int] = {}
        for ev in events:
            t = ev.get("type", "observation")
            event_counts[t] = event_counts.get(t, 0) + 1

        res_status = res.get("status", "success")
        if res_status == "partial":
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
            
        if res.get("warnings"):
            lines.extend([
                "",
                "### Warnings:",
                *[f"- ⚠ {w}" for w in res["warnings"]]
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
        return "\n".join(lines)

    @mcp.tool()
    def knowledge_session_commit(
        project: str = "", conversation_text: str = "", session_id: str = ""
    ) -> str:
        """Deprecated internal lifecycle hook.

        Invoked automatically by OEM runtime.
        Agents should not call this tool directly.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text for knowledge extraction.
            session_id: Optional session ID for correlation.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                return _commit_session_from_tool(eng, project, conversation_text, session_id)
        except Exception as e:
            return f"# Session Commit Failure\n\nError: {e}"

    @mcp.tool()
    def knowledge_session_end(
        project: str = "", conversation_text: str = "", session_id: str = ""
    ) -> str:
        """End the current knowledge session, trigger reflection/materialization, update graph, and re-index.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text or history for knowledge extraction.
            session_id: Optional session ID.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                return _commit_session_from_tool(eng, project, conversation_text, session_id)
        except Exception as e:
            return f"# Session End Failure\n\nError: {e}"

    @mcp.tool()
    def knowledge_consolidate(project: str = "") -> str:
        """Deduplicate and merge overlapping concept markdown files.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.state.consolidate(project or None)
        except Exception as e:
            return render_panel(
                "Consolidation Failure", [f"Error: {e}"], status="error"
            )

        lines = [res.get("message", "Consolidation complete."), ""]
        for m in res.get("merged", []):
            lines.append(f"  🧹 {m}")
        if not res.get("merged"):
            lines.append("  No duplicates found.")
        return render_panel("Consolidation", lines, status="organize")

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
    def knowledge_lint(
        project: str = "", max_parallel: int = 4, fix: bool = False
    ) -> str:
        """Check the knowledge base for broken links and orphan concepts in parallel.

        Args:
            project: Project directory path. Defaults to current directory.
            max_parallel: Concurrency limit for link validation.
            fix: Automatically heal broken links that match existing aliases.
        """
        import asyncio
        from .linter import run_lint
        from pathlib import Path

        target = Path(project) if project else Path.cwd()
        try:
            res = asyncio.run(run_lint(target, max_parallel=max_parallel, fix=fix))
        except Exception as e:
            return render_panel("Lint Failure", [f"Error: {e}"], status="error")

        if res.get("status") == "error":
            return render_panel("Lint Error", [res.get("message", "")], status="error")

        lines = [
            f"Files Scanned: {res.get('files_scanned', 0)}",
            f"Broken Links:  {len(res.get('broken_links', []))}",
            f"Healed Links:  {len(res.get('healed_links', []))}",
            f"Orphan Nodes:  {len(res.get('orphans', []))}",
        ]
        if fix:
            lines.append(f"Files Fixed:   {res.get('fixed_files_count', 0)}")
        lines.append("")

        if res.get("broken_links"):
            lines.append("Broken Links:")
            for bl in res["broken_links"]:
                lines.append(
                    f"  ❌ {bl['source']}:{bl['line']} -> {bl['target']} (in {Path(bl['file']).name})"
                )
            lines.append("")

        if res.get("healed_links"):
            action = "Fixed" if fix else "Can Heal"
            lines.append(f"Healed Links ({action}):")
            for hl in res["healed_links"]:
                lines.append(
                    f"  ✅ {hl['source']}:{hl['line']} -> resolved to {hl['target_concept']} (originally: {hl['original']})"
                )
            lines.append("")

        if res.get("orphans"):
            lines.append("Orphan Concepts:")
            for op in res["orphans"]:
                lines.append(f"  ⚠️ {op}")

        if (
            not res.get("broken_links")
            and not res.get("orphans")
            and not res.get("healed_links")
        ):
            lines.append("✨ All links verified successfully and no orphans found!")

        return render_panel(
            "Lint Results",
            lines,
            status="error" if res.get("broken_links") else "ok",
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
            with KnowledgeEngine(project or None) as eng:
                stale = eng.state.detect_stale_concepts(stale_sessions, project or None)
                merges = eng.propose_merges(similarity_threshold, project or None)
                conflicts = eng.detect_contradictions(project or None)
        except Exception as e:
            return render_panel("Health Check Failure", [f"Error: {e}"], status="error")

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
    def knowledge_graph_query(concept_id: str, direction: str = "both", project: str = "") -> str:
        """Query semantic relationships for a concept.

        Args:
            concept_id: Target concept ID
            direction: incoming, outgoing, or both. Defaults to both.
            project: Project directory path. Defaults to current directory.
        """
        try:
            with KnowledgeEngine(project or None) as eng:
                registry = eng.state._load_registry(project or None)
                cdata = registry.get(concept_id)
                if not cdata:
                    return render_panel("Query Error", [f"Concept {concept_id} not found."], status="error")

                lines = [
                    f"Concept: {cdata.get('canonical_name', '').replace('-', ' ').upper()} ({concept_id})",
                    ""
                ]

                if direction in ("outgoing", "both"):
                    lines.append("Outgoing Relationships:")
                    relationships = cdata.get("relationships", [])
                    for r in relationships:
                        target_id = r.get("target")
                        target_name = registry.get(target_id, {}).get("canonical_name") or target_id
                        lines.append(f"  - [{r.get('type')}] -> {target_name} ({target_id})")
                    if not relationships:
                        lines.append("  - None")
                    lines.append("")

                if direction in ("incoming", "both"):
                    lines.append("Incoming Relationships:")
                    incoming_count = 0
                    for cid, data in registry.items():
                        if cid == concept_id:
                            continue
                        relationships = data.get("relationships", [])
                        for r in relationships:
                            if r.get("target") == concept_id:
                                lines.append(f"  - {data.get('canonical_name')} ({cid}) -> [{r.get('type')}]")
                                incoming_count += 1
                    if incoming_count == 0:
                        lines.append("  - None")

                return render_panel("Graph Query Results", lines, status="ok")
        except Exception as e:
            return render_panel("Query Failure", [f"Error: {e}"], status="error")


def main() -> None:
    from oem_knowledge.engine import apply_oem_process_env_defaults
    apply_oem_process_env_defaults()

    from fastmcp import FastMCP
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    mcp.run()


if __name__ == "__main__":
    main()
