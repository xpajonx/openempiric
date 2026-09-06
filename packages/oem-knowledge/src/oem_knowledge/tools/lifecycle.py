from __future__ import annotations

import time
import json
from pathlib import Path

from ..engine import KnowledgeEngine
from ..project import (
    resolve_active_project,
    handle_resolution_error,
    ProjectResolutionError,
    ProjectUnresolvedError,
    SESSION_TO_PROJECT
)
from ..ui import render_panel

def _decorate_session_result(res: dict, project_root: Path, session_id: str, operation: str, deprecated: bool = False) -> dict:
    if res.get("status") in ("success", "partial", "warn", "empty") and session_id:
        SESSION_TO_PROJECT.pop(session_id, None)
    res["project_root"] = str(project_root)
    res["memory_root"] = str(project_root / ".oem")
    res["operation"] = operation
    if deprecated:
        res["deprecated"] = True
        warning_msg = "knowledge_session_commit is deprecated. Use knowledge_session_end instead."
        warnings_list = res.get("warnings", [])
        if warning_msg not in warnings_list:
            warnings_list.append(warning_msg)
        res["warnings"] = warnings_list
    return res

def _commit_session_from_tool(
    eng: KnowledgeEngine,
    project_root: Path,
    conversation_text: str,
    session_id: str,
    events: list | None = None,
    extraction_mode: str = "auto",
    timeout_seconds: float | None = None,
) -> dict:
    try:
        res = eng.session_commit(
            str(project_root),
            conversation_text,
            session_id,
            events=events,
            extraction_mode=extraction_mode,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"# Session End Failure\n\nError: {e}"
        }

    status = res.get("status", "success")
    failed_step = res.get("failed_step")
    warnings = res.get("warnings", [])
    timings = res.get("phase_timings", {})

    if status == "warn":
        lines = [
            "# Session End / Commit Complete with Warnings",
            "",
            res.get("message", "Session closed, but dense LLM reflection was skipped because no LLM provider is configured."),
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
        res["message"] = "\n".join(lines)
        return res

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
        res["message"] = "\n".join(lines)
        return res

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
        res["message"] = "\n".join(lines)
        return res

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
        res["message"] = "\n".join(lines)
        return res

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

    res["message"] = "\n".join(lines)
    return res


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    engine = KnowledgeEngine()

    @mcp.tool()
    def knowledge_init(project: str = "") -> str:
        """Bootstrap the .oem/ memory in a project directory. If project is empty, uses current directory."""
        target = project or "."
        try:
            target_path = Path(target).resolve()
            res = engine.init_project(str(target_path))
            lines = [res["message"], "", "Created Directories:"]
            for d in res.get("created_directories", []):
                lines.append(f"  📁 {d}")
            lines.append("")
            lines.append("Created Baseline Documents:")
            for f in res.get("created_files", []):
                lines.append(f"  📄 {f}")
            panel = render_panel("OpenEmpiric Initialized", lines, status="bootstrap")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_init",
                "project_root": str(target_path),
                "memory_root": str(target_path / ".oem"),
                "message": panel
            }, indent=2)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_init",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_add_memory(
        memory_type: str,
        content: str,
        scope: str = "project",
        confidence: int = 3,
        evidence: str = "",
        project: str = "",
    ) -> str:
        """Add a durable fact discovered now during active work; use concise content with evidence. This is not a replacement for session_end.

        Args:
            memory_type: Type of memory: 'decision', 'observation', 'preference', 'failure', 'workaround'
            content: The memory content/summary
            scope: Memory scope: 'project', 'user', or 'session'
            confidence: Confidence rating 1-5. Auto-accepted if >= 3.
            evidence: Supporting evidence or context for the memory
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            # Get session_id from active session state
            session_id = ""
            try:
                state_dir = project_root / ".oem" / "state"
                active_session_file = state_dir / "active_session.json"
                if active_session_file.exists():
                    import json as _json
                    session_data = _json.loads(active_session_file.read_text())
                    session_id = session_data.get("session_id", "")
            except Exception:
                pass
            with KnowledgeEngine(str(project_root)) as eng:
                result = eng.reflection.add_inline_memory(
                    memory_type=memory_type,
                    content=content,
                    scope=scope,
                    confidence=confidence,
                    evidence=evidence,
                    session_id=session_id,
                    project=str(project_root),
                )
            result["project_root"] = str(project_root)
            result["operation"] = "knowledge_add_memory"
            return json.dumps(result, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_add_memory", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_add_memory",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_index(force: bool = False, project: str = "") -> str:
        """Re-index all markdown files in the project's .oem/ concept wiki/registry/skills trees.

        Args:
            force: If True, re-index everything. If False, only new/changed files.
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            start = time.time()
            with KnowledgeEngine(str(project_root)) as eng:
                s = eng.search.index_all(force=force)
                try:
                    eng.search.index_user_events()
                except Exception:
                    pass
            duration = time.time() - start
            lines = [
                f"Files Scanned: {s.get('scanned', 0)}",
                f"New:           {s.get('new', 0)}",
                f"Updated:       {s.get('updated', 0)}",
                f"Unchanged:     {s.get('unchanged', 0)}",
                f"Failed:        {s.get('failed', 0)}",
                f"Duration:      {duration:.2f}s",
            ]
            panel = render_panel("Knowledge Index", lines, status="index")
            return json.dumps({
                "status": "success",
                "operation": "knowledge_index",
                "project_root": str(project_root),
                "memory_root": str(memory_root),
                "message": panel,
                "stats": s
            }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_index", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_index",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_reflect(
        project: str = "",
        conversation_text: str = "",
        session_id: str = "",
        events: list | None = None,
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
            project_root = resolve_active_project(project, session_id)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.reflection.reflect_session(
                    project=str(project_root),
                    conversation_text=conversation_text,
                    session_id=session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
            status = res.get("status")
            events_list = res.get("knowledge_events", [])
            lines = [
                f"Report: {Path(res.get('report_path', '')).name if res.get('report_path') else 'None'}",
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
            panel = render_panel("Session Reflection", lines, status="ok")
            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_reflect"
            res["message"] = panel
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_reflect", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_reflect",
                "message": str(e),
                "suggestion": "Check the tool arguments or workspace lock status."
            }, indent=2)

    @mcp.tool()
    def knowledge_session_start(project: str = "") -> str:
        """Start or restore an OpenEmpiric session lifecycle.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            try:
                project_root = resolve_active_project(project)
            except ProjectUnresolvedError:
                return json.dumps({
                    "status": "error",
                    "reason": "project_not_initialized",
                    "suggestion": "Run `oem init` or call with an explicit project path."
                }, indent=2)
            except ProjectResolutionError as e:
                return handle_resolution_error("knowledge_session_start", e)

            if not (project_root / ".oem").is_dir():
                return json.dumps({
                    "status": "error",
                    "reason": "project_not_initialized",
                    "suggestion": "Run `oem init` or call with an explicit project path."
                }, indent=2)

            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.session_start(str(project_root))

            if res.get("status") == "success":
                sid = res.get("session_id")
                if sid:
                    SESSION_TO_PROJECT[sid] = project_root

            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_session_start"
            return json.dumps(res, indent=2)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_session_start",
                "message": str(e),
                "suggestion": "Check the tool arguments or workspace lock status.",
                "warnings": [str(e)],
            }, indent=2)

    @mcp.tool()
    def knowledge_read(scope: str = "project", project: str = "", limit: int = 10) -> str:
        """Read the project memory baseline to learn or orient from project memory.

        Use this whenever you need broad understanding (e.g. at startup or when confused about conventions).
        Returns a bounded, read-only summary based on the requested scope.

        Args:
            scope: The scope of memory to read: 'project', 'recent', 'skills', or 'health'.
            project: Project directory path. Defaults to current directory.
            limit: Max items per section (default 10).
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.knowledge_read(str(project_root), scope, limit)
            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_read"
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_read", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_read",
                "message": str(e),
                "suggestion": "Check the tool arguments or workspace lock status.",
                "warnings": [str(e)],
            }, indent=2)

    @mcp.tool()
    def knowledge_preflight(task: str, project: str = "", mode: str = "auto", limit: int = 8, write_audit: bool = True) -> str:
        """Run deterministic, read-only OEM preflight before non-trivial planning. For required, follow the returned context before planning; for suggest, use the context and retrieve only a specific remaining gap; for noop, proceed without retrieval just to confirm.

        Args:
            task: The user task or planning prompt to evaluate.
            project: Project directory path. Defaults to current directory.
            mode: Preflight mode. Only 'auto' is supported in Batch 2.
            limit: Max items per result category (default 8; clamped to 1..20).
            write_audit: Whether to append the preflight audit log.
        """
        if mode != "auto":
            return json.dumps({
                "status": "error",
                "operation": "knowledge_preflight",
                "decision": "error",
                "reason": "unsupported_mode",
                "reason_detail": f"Unsupported preflight mode: {mode}",
                "project_root": "",
                "memory_root": "",
                "matched_skills": [],
                "matched_concepts": [],
                "matched_memory": [],
                "source_suggestions": [],
                "context": "",
                "warnings": [],
                "suggestion": "Use mode='auto'.",
            }, indent=2)

        try:
            with KnowledgeEngine(project or None) as eng:
                res = eng.preflight(
                    task=task,
                    project=project or None,
                    limit=limit,
                    write_audit=write_audit,
                )
            return json.dumps(res, indent=2)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_preflight",
                "decision": "error",
                "reason": "preflight_error",
                "reason_detail": str(e),
                "project_root": "",
                "memory_root": "",
                "matched_skills": [],
                "matched_concepts": [],
                "matched_memory": [],
                "source_suggestions": [],
                "context": "",
                "warnings": [str(e)],
                "suggestion": "Check the tool arguments or workspace lock status.",
            }, indent=2)

    @mcp.tool()
    def knowledge_materialize(project: str = "") -> str:
        """Promote candidate/emerging concepts to validated/canonical status and materialize markdown nodes.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.materialization.materialize_concepts(str(project_root))
            lines = [res.get("message", "Materialization complete."), ""]
            if res.get("materialized"):
                for m in res["materialized"]:
                    lines.append(f"  ✨ {m}")
            else:
                lines.append("  No new or modified concepts.")
            panel = render_panel("Concept Materialization", lines, status="ok")
            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_materialize"
            res["message"] = panel
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_materialize", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_materialize",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_export(project: str = "", output_path: str = "") -> str:
        """Export project memory to a tar.gz archive for backup or transfer.

        Args:
            project: Project directory path. Defaults to current directory.
            output_path: Path for the output .tar.gz archive. Required.
        """
        if not output_path:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_export",
                "message": "output_path is required"
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            with KnowledgeEngine(str(project_root)) as eng:
                result = eng.export_memory(output_path, str(project_root))
            result["project_root"] = str(project_root)
            result["operation"] = "knowledge_export"
            return json.dumps(result, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_export", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_export",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_import(project: str = "", input_path: str = "") -> str:
        """Import project memory from a tar.gz archive, merging with existing memory.

        Uses event_id dedup to avoid duplicates. Returns counts of imported
        and skipped events.

        Args:
            project: Project directory path. Defaults to current directory.
            input_path: Path to the .tar.gz archive to import. Required.
        """
        if not input_path:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_import",
                "message": "input_path is required"
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            with KnowledgeEngine(str(project_root)) as eng:
                result = eng.import_memory(input_path, str(project_root))
            result["project_root"] = str(project_root)
            result["operation"] = "knowledge_import"
            return json.dumps(result, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_import", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_import",
                "message": str(e)
            }, indent=2)

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
            project_root = resolve_active_project(project, session_id)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = _commit_session_from_tool(
                    eng,
                    project_root,
                    conversation_text,
                    session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
            res = _decorate_session_result(res, project_root, session_id, "knowledge_session_commit", deprecated=True)
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_session_commit", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_session_commit",
                "message": f"# Session Commit Failure\n\nError: {e}"
            }, indent=2)

    @mcp.tool()
    def knowledge_session_end(
        project: str = "",
        conversation_text: str = "",
        session_id: str = "",
        events: list | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> str:
        """Preferred session close: trigger reflection/materialization, update graph, and re-index. knowledge_session_commit is deprecated. Structured root fields are authoritative; message is display text.

        Args:
            project: Project directory path. Defaults to current directory.
            conversation_text: Raw conversation text or history for knowledge extraction.
            session_id: Optional session ID.
            events: Optional pre-extracted structured events.
            extraction_mode: Mode to use: 'auto', 'structured', 'markers', or 'llm'.
            timeout_seconds: Optional timeout in seconds for LLM extraction.
        """
        try:
            project_root = resolve_active_project(project, session_id)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = _commit_session_from_tool(
                    eng,
                    project_root,
                    conversation_text,
                    session_id,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
            res = _decorate_session_result(res, project_root, session_id, "knowledge_session_end")
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_session_end", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_session_end",
                "message": f"# Session End Failure\n\nError: {e}"
            }, indent=2)

    @mcp.tool()
    def knowledge_dream(project: str = None, force: bool = False) -> str:
        """Run the memory maintainer dream cycle (consolidation, decay, promotion, archive, merge).

        Args:
            project: Project path (None for auto-detect).
            force: Run even with fewer than 2 concepts.

        Returns:
            Dict with status and per-phase results.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                res = eng.dream(project=str(project_root), force=force)
            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_dream"
            return json.dumps(res, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_dream", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_dream",
                "message": str(e)
            }, indent=2)