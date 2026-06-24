from __future__ import annotations

import json
import re
from pathlib import Path

from ..engine import KnowledgeEngine
from ..project import (
    resolve_active_project,
    handle_resolution_error,
    ProjectResolutionError
)
from ..ui import render_panel

def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

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
            project_root = resolve_active_project(project, session_id)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                events = eng.state.get_events(
                    str(project_root),
                    concept=concept,
                    event_type=event_type,
                    session_id=session_id,
                )
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
            panel = render_panel("Knowledge Events", lines, status="ok")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_get_events",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel,
                "events": events
            }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_get_events", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_get_events",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_get_event(project: str = "", event_id: str = "") -> str:
        """Retrieve a single knowledge event by its UUID.

        Args:
            project: Project directory path. Defaults to current directory.
            event_id: The event UUID
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                ev = eng.state.get_event(str(project_root), event_id)
            lines = [
                f"Event: {ev.get('event_id', '')}",
                f"Type: {ev.get('event_type', '')}",
                f"Summary: {ev.get('summary', '')}",
                f"Evidence: {ev.get('evidence', '')}",
                f"Confidence: {ev.get('confidence', '')}",
                f"Session: {ev.get('session_id', '')}",
            ]
            panel = render_panel("Knowledge Event Detail", lines, status="ok")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_get_event",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel,
                "event": ev
            }, indent=2)
        except KeyError as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_get_event",
                "message": f"Event not found: {e}"
            }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_get_event", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_get_event",
                "message": str(e)
            }, indent=2)

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
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.state.merge_concepts(str(project_root), primary_id, secondary_id)
            if res.get("status") == "error":
                panel = render_panel("Merge Error", [res.get("message", "")], status="error")
                return json.dumps({
                    "status": "error",
                    "operation": "knowledge_merge_concepts",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel
                }, indent=2)
            panel = render_panel("Concepts Merged", [res.get("message", "")], status="organize")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_merge_concepts",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel
            }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_merge_concepts", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_merge_concepts",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_search(query: str, k: int = 3, project: str = "") -> str:
        """Fast lookup and term-based search across concepts.

        Args:
            query: Search query
            k: Number of results to return. Defaults to 3.
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                results = eng.search.search(query, k=k)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_search", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_search",
                "message": str(e)
            }, indent=2)

        if not results:
            panel = render_panel(f"Search: 0 results", [f"No matches for: '{query}'"], status="search")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_search",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "results": [],
                "message": panel
            }, indent=2)

        lines = [f"Query: \"{query}\"", f"Results: {len(results)}", ""]
        for idx, r in enumerate(results):
            meta = r.get("metadata", {})
            rel_path = meta.get("file_path", "")
            if rel_path:
                try:
                    rel_path = str(Path(rel_path).relative_to(project_root))
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

        panel = render_panel("Knowledge Search Results", lines, status="search")
        return json.dumps({
            "status": "success",
            "operation": "knowledge_search",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "results": results,
            "message": panel
        }, indent=2)

    @mcp.tool()
    def knowledge_explain_concept(concept_id: str, project: str = "") -> str:
        """Explain a concept and its details from the registry.

        Args:
            concept_id: Concept ID (e.g. concept_001)
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                registry = eng.state._load_registry(str(project_root))
                cdata = registry.get(concept_id)
                if not cdata:
                    panel = render_panel("Concept Not Found", [f"Concept {concept_id} not in registry."], status="error")
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_explain_concept",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": panel
                    }, indent=2)

                harness = eng._resolve_harness(str(project_root))
                wiki_file = harness / "wiki" / f"{concept_id}.md"
                recent_evidence = []
                if wiki_file.exists():
                    content = wiki_file.read_text(encoding="utf-8")
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

                panel = render_panel("Concept Explanation", lines, status="ok")
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_explain_concept",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "concept": cdata
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_explain_concept", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_explain_concept",
                "message": str(e)
            }, indent=2)
