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
from ..health import build_health_report

def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

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
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            res = build_health_report(str(project_root), include_daemon_runtime=False)

            with KnowledgeEngine(str(project_root)) as eng:
                stale = eng.state.detect_stale_concepts(stale_sessions, str(project_root))
                merges = eng.propose_merges(similarity_threshold, str(project_root))
                concept_conflicts = eng.detect_contradictions(str(project_root))
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_health_check", e)
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
        lines.append("Runtime Checks:")
        for c in res["runtime"]["checks"]:
            symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
            lines.append(f"  {symbol} {c['name']}")
        lines.append("")

        lines.append("Stale Concepts:")
        if stale:
            for s in stale:
                if s.get("sessions_since_reference") is None:
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - reference session unknown")
                else:
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - untouched for {s['sessions_since_reference']} sessions")
        else:
            lines.append("  None")
        lines.append("")
        
        lines.append("Duplicate Merge Proposals:")
        if merges:
            for m in merges:
                lines.append(f"  ✦ Suggest merging {m['secondary_name']} ({m['secondary_id']}) into {m['primary_name']} ({m['primary_id']})")
                lines.append(f"    Reason: {m['reason']}")
        else:
            lines.append("  None")
        lines.append("")
        
        lines.append("Contradictions Detected:")
        if res.get("contradictions"):
            for c in res["contradictions"]:
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
                lines.append(f"    Description: {c['description']}")
        else:
            lines.append("  None")
            
        panel = render_panel("Knowledge Health Scan", lines, status="stats")
        return json.dumps({
            "status": "success",
            "operation": "knowledge_health_check",
            "project_root": str(project_root),
            "memory_root": str(memory_root),
            "message": panel,
            "health": res,
            "contradictions": res.get("contradictions", []),
            "active_project": res.get("active_project", {}),
            "stale": stale,
            "merges": merges,
            "concept_contradictions": concept_conflicts,
            "conflicts": concept_conflicts
        }, indent=2)

    @mcp.tool()
    def knowledge_stats(project: str = "") -> str:
        """Show oem/ knowledge statistics.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                registry = eng.state._load_registry(str(project_root))
                harness = eng._resolve_harness(str(project_root))
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
                panel = render_panel("Knowledge Stats", lines, status="stats")
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_stats",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "total_concepts": len(registry),
                    "vector_db_size_mb": db_size / (1024 * 1024)
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_stats", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_stats",
                "message": str(e)
            }, indent=2)
