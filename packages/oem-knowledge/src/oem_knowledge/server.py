from __future__ import annotations

import time
from pathlib import Path

from oem_tui.panels import render_panel

from .engine import KnowledgeEngine


def mount_tools(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    engine = KnowledgeEngine()

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
        eng = KnowledgeEngine(project or None)
        start = time.time()
        try:
            s = eng.index_all(force=force)
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
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.reflect_session(project or None, conversation_text, session_id)
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
        return render_panel("Session Reflection", lines, status="ok")

    @mcp.tool()
    def knowledge_materialize(project: str = "") -> str:
        """Promote candidate/emerging concepts to validated/canonical status and materialize markdown nodes.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.materialize_concepts(project or None)
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
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.update_graph(project or None)
        except Exception as e:
            return render_panel("Graph Update Failure", [f"Error: {e}"], status="error")

        lines = [
            res.get("message", "Graph updated."),
            f"Links added/updated: {res.get('links_updated', 0)}",
            f"Files scanned: {res.get('files_scanned', 0)}",
        ]
        return render_panel("Knowledge Graph", lines, status="organize")

    @mcp.tool()
    def knowledge_session_commit(
        project: str = "", conversation_text: str = "", session_id: str = ""
    ) -> str:
        """End-of-session pipeline: reflect → materialize concepts → update graph → re-index.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text for knowledge extraction.
            session_id: Optional session ID for correlation.
        """
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.session_commit(project or None, conversation_text, session_id)
        except Exception as e:
            return render_panel(
                "Session Commit Failure", [f"Error: {e}"], status="error"
            )

        events = res.get("knowledge_events", [])
        event_counts: dict[str, int] = {}
        for ev in events:
            t = ev.get("type", "observation")
            event_counts[t] = event_counts.get(t, 0) + 1

        lines = [
            "Session commit succeeded.",
            f"Report: {Path(res.get('report_path', '')).name}",
            "",
            "Extracted Knowledge Events:",
        ]
        if event_counts:
            for t, c in sorted(event_counts.items()):
                lines.append(f"  - {t.title()}: {c} events")
        else:
            lines.append("  - None")
        lines.extend(
            [
                "",
                "Graph & Index:",
                f"  Materialized:   {len(res.get('materialized_log', []))} concepts",
                f"  Links updated:  {res.get('links_updated', 0)}",
                f"  Index: {res.get('index_stats', {}).get('new', 0)} new, {res.get('index_stats', {}).get('updated', 0)} updated",
            ]
        )
        return render_panel("Session Commit Complete", lines, status="ok")

    @mcp.tool()
    def knowledge_consolidate(project: str = "") -> str:
        """Deduplicate and merge overlapping concept markdown files.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.consolidate(project or None)
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
        eng = KnowledgeEngine(project or None)
        try:
            events = eng.get_events(
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
        eng = KnowledgeEngine(project or None)
        try:
            ev = eng.get_event(project or None, event_id)
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
        eng = KnowledgeEngine(project or None)
        try:
            res = eng.merge_concepts(project or None, primary_id, secondary_id)
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


def main() -> None:
    from fastmcp import FastMCP
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    mcp.run()


if __name__ == "__main__":
    main()
