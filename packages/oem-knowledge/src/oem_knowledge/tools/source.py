from __future__ import annotations

import json
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
    def knowledge_source_search(query: str, k: int = 5, project: str = "") -> str:
        """Search implementation/source evidence in indexed project files (separate source corpus), not learned memory.

        Args:
            query: Search query
            k: Number of results to return. Defaults to 5.
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.source.search(query, k=k)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_source_search", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_source_search",
                "message": str(e)
            }, indent=2)

        if res.get("status") == "error":
            panel_lines = [res.get("message", "Unknown error.")]
            if res.get("suggestion"):
                panel_lines += ["", f"Suggestion: {res['suggestion']}"]
            panel = render_panel("Source Search Error", panel_lines, status="error")
            return json.dumps({
                **res,
                "operation": "knowledge_source_search",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel
            }, indent=2)

        results = res.get("results", [])
        if not results:
            panel = render_panel(f"Source Search: 0 results", [f"No matches for: '{query}'"], status="search")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_source_search",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "results": [],
                "message": panel
            }, indent=2)

        lines = [f"Query: \"{query}\"", f"Results: {len(results)}", ""]
        for idx, r in enumerate(results):
            meta = r.get("metadata", {})
            rel_path = meta.get("rel_path", "")
            start = meta.get("start_line")
            end = meta.get("end_line")
            loc = f"L{start}-{end}" if start and end else "metadata"
            snippet = r.get("document", "No content available.")
            score = r.get("score", 0.0)

            lines.append(f"{idx + 1}. [{rel_path}#{loc}] (score: {score:.4f})")
            lines.append(f"   {snippet[:150]}...")
            lines.append("")

        panel = render_panel("Source Search Results", lines, status="search")
        return json.dumps({
            "status": "success",
            "operation": "knowledge_source_search",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "results": results,
            "message": panel
        }, indent=2)

    @mcp.tool()
    def knowledge_source_read(
        path: str, start_line: int | None = None, end_line: int | None = None, project: str = ""
    ) -> str:
        """Inspect exact code or docs with bounded line ranges (separate source corpus).

        Args:
            path: Relative or absolute path to the file.
            start_line: 1-indexed starting line number (inclusive).
            end_line: 1-indexed ending line number (inclusive).
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.source.read(path, start_line=start_line, end_line=end_line)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_source_read", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_source_read",
                "message": str(e)
            }, indent=2)

        if res.get("status") == "error":
            panel_lines = [res.get("message", "Unknown error.")]
            if res.get("suggestion"):
                panel_lines += ["", f"Suggestion: {res['suggestion']}"]
            panel = render_panel("Source Read Error", panel_lines, status="error")
            return json.dumps({
                **res,
                "operation": "knowledge_source_read",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel
            }, indent=2)

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

        panel = render_panel("Source Content", lines, status="ok")
        return json.dumps({
            **res,
            "operation": "knowledge_source_read",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "message": panel
        }, indent=2)