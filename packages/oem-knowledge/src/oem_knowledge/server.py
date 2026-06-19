from __future__ import annotations

import time
import json
import os
import sys
from pathlib import Path

from .ui import render_panel

from .engine import KnowledgeEngine


class ProjectResolutionError(Exception):
    def __init__(self, message: str, suggestion: str = "", reason: str = ""):
        super().__init__(message)
        self.suggestion = suggestion
        self.reason = reason


class ProjectMismatchError(ProjectResolutionError):
    def __init__(self, resolved_project: str, cwd: str):
        super().__init__(
            f"Active project mismatch: resolved {resolved_project} but cwd is {cwd}.",
            suggestion="Verify you are in the correct workspace directory or pass the project explicitly.",
            reason="project_mismatch"
        )
        self.resolved_project = resolved_project
        self.cwd = cwd


class ProjectUnresolvedError(ProjectResolutionError):
    def __init__(self, message: str, suggestion: str = ""):
        super().__init__(
            message,
            suggestion=suggestion,
            reason="project_unresolved"
        )


SESSION_TO_PROJECT: dict[str, Path] = {}


def find_nearest_oem_root(path: Path) -> Path | None:
    try:
        p = path.resolve()
        for parent in [p] + list(p.parents):
            if (parent / ".oem").is_dir():
                return parent
    except Exception:
        pass
    return None


def is_oem_dev_repo(path: Path) -> bool:
    try:
        resolved = path.resolve()
        current_file = Path(__file__).resolve()
        for parent in [current_file] + list(current_file.parents):
            if (parent / ".git").exists() and (parent / "packages" / "oem-knowledge").is_dir():
                if resolved == parent:
                    return True
    except Exception:
        pass
    return False


def is_path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def get_project_root_from_active_session(path: Path) -> Path | None:
    root = find_nearest_oem_root(path)
    if root:
        active_json = root / ".oem" / "state" / "active_session.json"
        if active_json.exists():
            try:
                data = json.loads(active_json.read_text(encoding="utf-8"))
                p = data.get("project")
                if p:
                    return Path(p).resolve()
            except Exception:
                pass
    return None


def _should_bypass_mismatch_check() -> bool:
    if os.environ.get("OEM_FORCE_MISMATCH_CHECK") == "1":
        return False
    return "pytest" in sys.modules


def resolve_active_project(project_arg: str = "", session_id: str = "") -> Path:
    # 1. Explicit project argument if provided (one-off override, does not rebind session)
    if project_arg:
        p = Path(project_arg).resolve()
        root = find_nearest_oem_root(p)
        if root:
            return root
        if p.is_dir():
            return p
        raise ProjectUnresolvedError(
            f"Explicit project path '{project_arg}' does not exist or is not a directory.",
            suggestion="Verify the path exists and contains a .oem folder."
        )

    # 2. Active project root recorded in SESSION_TO_PROJECT for this session
    if session_id and session_id in SESSION_TO_PROJECT:
        return SESSION_TO_PROJECT[session_id]

    # Check active_session.json under env var directories
    for env_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
        val = os.environ.get(env_var)
        if val:
            p = Path(val).resolve()
            root = get_project_root_from_active_session(p)
            if root:
                return root

    # Check active_session.json under CWD
    root_from_cwd_session = get_project_root_from_active_session(Path.cwd())
    if root_from_cwd_session:
        return root_from_cwd_session

    # 3. Environment variables provided by agent runtime
    for env_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
        val = os.environ.get(env_var)
        if val:
            p = Path(val).resolve()
            root = find_nearest_oem_root(p)
            if root:
                if is_oem_dev_repo(root) and not _should_bypass_mismatch_check():
                    for check_var in ["OEM_PROJECT_ROOT", "WORKSPACE", "PWD"]:
                        chk_val = os.environ.get(check_var)
                        if chk_val:
                            chk_p = Path(chk_val).resolve()
                            if chk_p.is_dir() and not is_path_inside(chk_p, root):
                                raise ProjectMismatchError(str(root), str(chk_p))
                return root

    # 4. Nearest parent directory containing .oem starting from os.getcwd()
    cwd_path = Path.cwd().resolve()
    root = find_nearest_oem_root(cwd_path)
    if root:
        if is_oem_dev_repo(root) and not _should_bypass_mismatch_check():
            pwd_val = os.environ.get("PWD")
            if pwd_val:
                pwd_p = Path(pwd_val).resolve()
                if not is_path_inside(pwd_p, root):
                    raise ProjectMismatchError(str(root), str(pwd_p))
        return root

    raise ProjectUnresolvedError(
        "Active OEM project root could not be resolved from the current context.",
        suggestion="Pass project explicitly or start session from a directory containing .oem."
    )


def handle_resolution_error(operation: str, e: ProjectResolutionError) -> str:
    if isinstance(e, ProjectMismatchError):
        return json.dumps({
            "status": "error",
            "reason": "project_mismatch",
            "resolved_project": e.resolved_project,
            "cwd": e.cwd
        }, indent=2)
    elif isinstance(e, ProjectUnresolvedError):
        return json.dumps({
            "status": "error",
            "operation": operation,
            "reason": "project_unresolved",
            "suggestion": e.suggestion or "Pass project explicitly or start session from a directory containing .oem."
        }, indent=2)
    else:
        return json.dumps({
            "status": "error",
            "operation": operation,
            "message": str(e),
            "suggestion": getattr(e, "suggestion", None)
        }, indent=2)


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

    def _commit_session_from_tool(
        eng: KnowledgeEngine,
        project_root: Path,
        conversation_text: str,
        session_id: str,
        events: list[dict] | None = None,
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
            if res.get("status") in ("success", "partial", "warn", "empty") and session_id in SESSION_TO_PROJECT:
                SESSION_TO_PROJECT.pop(session_id, None)

            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_session_commit"
            res["deprecated"] = True
            
            warning_msg = "knowledge_session_commit is deprecated. Use knowledge_session_end instead."
            warnings_list = res.get("warnings", [])
            if warning_msg not in warnings_list:
                warnings_list.append(warning_msg)
            res["warnings"] = warnings_list
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
            if res.get("status") in ("success", "partial", "warn", "empty") and session_id in SESSION_TO_PROJECT:
                SESSION_TO_PROJECT.pop(session_id, None)

            res["project_root"] = str(project_root)
            res["memory_root"] = str(memory_root)
            res["operation"] = "knowledge_session_end"
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
            from oem_knowledge.health import build_runtime_health
            res = build_runtime_health(str(project_root))

            with KnowledgeEngine(str(project_root)) as eng:
                stale = eng.state.detect_stale_concepts(stale_sessions, str(project_root))
                merges = eng.propose_merges(similarity_threshold, str(project_root))
                conflicts = eng.detect_contradictions(str(project_root))
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
        if conflicts:
            for c in conflicts:
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
            "stale": stale,
            "merges": merges,
            "conflicts": conflicts
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

    @mcp.tool()
    def knowledge_skill_candidates(project: str = "") -> str:
        """List all skill candidates.

        Args:
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                candidates = eng.skills.list_skill_candidates(str(project_root))
                if not candidates:
                    return json.dumps({
                        "status": "success",
                        "operation": "knowledge_skill_candidates",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": "No skill candidates found.",
                        "candidates": []
                    }, indent=2)
                
                lines = [
                    "| Slug | Title | Confidence | Status | Evidence Count |",
                    "| --- | --- | --- | --- | --- |"
                ]
                for c in candidates:
                    lines.append(f"| {c.slug} | {c.title} | {c.confidence} | {c.status} | {len(c.evidence)} |")
                panel = "\n".join(lines)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidates",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "candidates": [c.to_dict() if hasattr(c, "to_dict") else vars(c) for c in candidates]
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidates", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidates",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_show(slug: str, project: str = "") -> str:
        """Show detailed candidate or approved skill.

        Args:
            slug: The slug of the skill candidate or approved skill.
            project: Project directory path. Defaults to current directory.
        """
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                candidate = eng.skills.load_skill_candidate(slug, str(project_root))
                if not candidate:
                    layout = eng.layout(str(project_root))
                    approved_path = layout.skills_dir / f"{slug}.md"
                    if approved_path.exists():
                        content = approved_path.read_text(encoding="utf-8")
                        return json.dumps({
                            "status": "success",
                            "operation": "knowledge_skill_candidate_show",
                            "project_root": str(project_root),
                            "memory_root": str(memory_root),
                            "message": content
                        }, indent=2)
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_show",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate/Skill '{slug}' not found."
                    }, indent=2)
                
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
                panel = "\n".join(lines)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_show",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel,
                    "candidate": candidate.to_dict() if hasattr(candidate, "to_dict") else vars(candidate)
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_show", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_show",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_approve(slug: str = "", force: bool = False, project: str = "") -> str:
        """Approve a skill candidate and promote it to a project skill.

        Args:
            slug: The slug of the skill candidate.
            force: Force approval even if rejected previously.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_approve",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "approved", str(project_root), force=force)
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_approve",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                panel = (
                    "Status: approved\n"
                    f"Skill: {slug}\n"
                    f"Approved skill written: .oem/skills/{slug}.md"
                )
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_approve",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": panel
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_approve", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_approve",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_reject(slug: str = "", project: str = "") -> str:
        """Reject a skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_reject",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "rejected", str(project_root))
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_reject",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_reject",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": f"Status: rejected\nSkill: {slug}"
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_reject", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_reject",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_skill_candidate_defer(slug: str = "", project: str = "") -> str:
        """Defer a skill candidate.

        Args:
            slug: The slug of the skill candidate.
            project: Project directory path. Defaults to current directory.
        """
        if not slug:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_defer",
                "message": "Slug is required."
            }, indent=2)
        try:
            project_root = resolve_active_project(project)
            memory_root = project_root / ".oem"
            with KnowledgeEngine(str(project_root)) as eng:
                cand = eng.skills.update_skill_candidate_status(slug, "deferred", str(project_root))
                if not cand:
                    return json.dumps({
                        "status": "error",
                        "operation": "knowledge_skill_candidate_defer",
                        "project_root": str(project_root),
                        "memory_root": str(memory_root),
                        "message": f"Candidate '{slug}' not found."
                    }, indent=2)
                return json.dumps({
                    "status": "success",
                    "operation": "knowledge_skill_candidate_defer",
                    "project_root": str(project_root),
                    "memory_root": str(memory_root),
                    "message": f"Status: deferred\nSkill: {slug}"
                }, indent=2)
        except ProjectResolutionError as e:
            return handle_resolution_error("knowledge_skill_candidate_defer", e)
        except Exception as e:
            return json.dumps({
                "status": "error",
                "operation": "knowledge_skill_candidate_defer",
                "message": str(e)
            }, indent=2)

    @mcp.tool()
    def knowledge_source_search(query: str, k: int = 5, project: str = "") -> str:
        """Search indexed project source files (separate source corpus).

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


def main() -> None:
    import os
    # Suppress fastmcp logs on stdout/stderr to protect MCP JSON-RPC transport stream
    if "FASTMCP_LOG_LEVEL" not in os.environ:
        os.environ["FASTMCP_LOG_LEVEL"] = "WARNING"
    if "FASTMCP_LOG_ENABLED" not in os.environ:
        os.environ["FASTMCP_LOG_ENABLED"] = "false"

    from oem_knowledge.engine import apply_oem_process_env_defaults
    apply_oem_process_env_defaults()

    from fastmcp import FastMCP
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
