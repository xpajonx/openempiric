from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import warnings
from collections import Counter
from pathlib import Path
def build_embedding_runtime_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Builds a runtime environment mapping for embeddings and model execution,
    setting defaults for CUDA_VISIBLE_DEVICES and TOKENIZERS_PARALLELISM if not already set,
    without mutating the global os.environ.
    """
    env = dict(base_env or os.environ)
    env.setdefault("CUDA_VISIBLE_DEVICES", "")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    return env


def apply_oem_process_env_defaults() -> None:
    """Explicitly mutates os.environ to set safe defaults for openempiric-knowledge execution.
    Only call this from explicit process entrypoints (like CLI main or MCP server startup).
    """
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from oem_knowledge.fs import FileLock, SecureFileSystem
from oem_knowledge.project_layout import ProjectLayout
from contextlib import contextmanager
from typing import Callable

class PhaseTimer:
    def __init__(self):
        self.timings = {}
        self.current_phase = None
        self.failed_phase = None

    @contextmanager
    def phase(self, name: str, callback: Callable[[str], None] | None = None):
        if callback is not None:
            try:
                callback(name)
            except Exception:
                pass
        self.current_phase = name
        start = time.perf_counter()
        try:
            yield
        except Exception:
            self.failed_phase = name
            raise
        finally:
            self.timings[name] = time.perf_counter() - start
            self.current_phase = None

OEM_DIR = ".oem"
DEFAULT_DIRS = [
    "wiki",
    "sessions",
    "state",
    "graph",
    "skills",
    "skill_candidates",
]

def migrate_harness_to_oem(project_dir: Path):
    old_dir = project_dir / ".harness"
    new_dir = project_dir / ".oem"
    if old_dir.is_dir() and not new_dir.exists():
        old_dir.rename(new_dir)
        
        old_wiki = new_dir / "directives" / "wiki_concepts"
        new_wiki = new_dir / "wiki"
        if old_wiki.is_dir():
            new_wiki.parent.mkdir(parents=True, exist_ok=True)
            old_wiki.rename(new_wiki)
            
        old_sess = new_dir / "directives" / "sessions"
        new_sess = new_dir / "sessions"
        if old_sess.is_dir():
            new_sess.parent.mkdir(parents=True, exist_ok=True)
            old_sess.rename(new_sess)
            
        old_reg = new_dir / "state" / "concept_registry.json"
        new_reg = new_dir / "concept_registry.json"
        if old_reg.exists():
            old_reg.rename(new_reg)
            
        old_events = new_dir / "state" / "events.jsonl"
        new_events = new_dir / "events.jsonl"
        if old_events.exists():
            old_events.rename(new_events)
            
        old_inbox = new_dir / "directives" / "wiki_inbox.md"
        new_inbox = new_dir / "wiki" / "inbox.md"
        if old_inbox.exists():
            new_inbox.parent.mkdir(parents=True, exist_ok=True)
            old_inbox.rename(new_inbox)
            
        old_idx = new_dir / "directives" / "index.md"
        new_idx = new_dir / "wiki" / "index.md"
        if old_idx.exists():
            new_idx.parent.mkdir(parents=True, exist_ok=True)
            old_idx.rename(new_idx)

        old_log = new_dir / "directives" / "log.md"
        new_log = new_dir / "wiki" / "log.md"
        if old_log.exists():
            new_log.parent.mkdir(parents=True, exist_ok=True)
            old_log.rename(new_log)

        old_prog = new_dir / "directives" / "progress.md"
        new_prog = new_dir / "progress.md"
        if old_prog.exists():
            old_prog.rename(new_prog)

        old_handoff = new_dir / "directives" / "session-handoff.md"
        new_handoff = new_dir / "session-handoff.md"
        if old_handoff.exists():
            old_handoff.rename(new_handoff)

        directives_dir = new_dir / "directives"
        if directives_dir.is_dir() and not any(directives_dir.iterdir()):
            try:
                directives_dir.rmdir()
            except Exception:
                pass


def find_harness_root(path: str | Path) -> Path | None:
    """Walk up from path looking for .oem/ or .harness/ directory, stopping at boundaries."""
    p = Path(path).resolve()
    for parent in [p] + list(p.parents):
        if (parent / ".oem").is_dir() or (parent / ".harness").is_dir():
            return parent
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists() or (parent / ".opencode").exists():
            break
    return None


def find_all_projects(base_dir: str | Path | None = None) -> list[Path]:
    """Find all projects with .oem/ or .harness/ directories."""
    if base_dir is None:
        base_dir = Path.home() / "projects"
    base = Path(base_dir)
    if not base.is_dir():
        return []
    return [d for d in base.iterdir() if d.is_dir() and ((d / ".oem").is_dir() or (d / ".harness").is_dir())]


class KnowledgeEngine:
    def __init__(self, project_path: str | Path | None = None):
        self._model = None
        self.project_path = Path(project_path).resolve() if project_path else None
        self._db_dir: Path | None = None

        # Instantiate services dynamically with self-injection
        from oem_knowledge.services.search import SearchService
        from oem_knowledge.services.materialization import MaterializationService
        from oem_knowledge.services.reflection import ReflectionService
        from oem_knowledge.services.state import StateService
        from oem_knowledge.services.event_migration import EventMigrator
        from oem_knowledge.services.fitness import FitnessService
        from oem_knowledge.services.skills import SkillService

        self.search = SearchService(self)
        self.materialization = MaterializationService(self)
        self.reflection = ReflectionService(self)
        self.state = StateService(self)
        self.event_migrator = EventMigrator(self)
        self.fitness = FitnessService(self)
        self.skills = SkillService(self)

    def close(self) -> None:
        for service in (getattr(self, "search", None),):
            close = getattr(service, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> "KnowledgeEngine":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _sfs(self, project: str | Path | None = None) -> SecureFileSystem:
        p = Path(project or self.project_path or ".").resolve()
        return SecureFileSystem(p)

    def _resolve_harness(self, project_path: str | Path | None = None) -> Path:
        p = Path(project_path or self.project_path or ".").resolve()
        root = find_harness_root(p) or p
        harness = root / OEM_DIR
        if not harness.exists():
            harness.mkdir(parents=True, exist_ok=True)
            self._bootstrap_harness(root)
        return harness

    def _bootstrap_harness(self, project_path: Path):
        """Create a minimal .oem/ structure."""
        harness = project_path / OEM_DIR
        for d in DEFAULT_DIRS:
            (harness / d).mkdir(parents=True, exist_ok=True)
        sfs = self._sfs(project_path)
        for fname, content in [
            (
                "progress.md",
                f"# Progress — {project_path.name}\n- **{time.strftime('%Y-%m-%d')}:** Initialized.\n",
            ),
            (
                "session-handoff.md",
                (
                    "# Session Handoff\n\n"
                    "## Historical Context\n"
                    "Complete phase one.\n\n"
                    "## Previous Decisions\n"
                    "- Setup initial directory structure.\n\n"
                    "## Open Questions\n"
                    "- What goals/concepts should be mapped next?\n"
                ),
            ),
        ]:
            fp = harness / fname
            if not sfs.exists(fp):
                sfs.write_text(fp, content, force_allow_truncation=True)

        # Install default skills
        try:
            from oem_knowledge.adapters import get_adapter
            adapter = get_adapter("opencode", self, project_path)
            adapter.install_skill()
        except Exception:
            pass

    @property
    def model(self):
        if self._model is None:
            import sys
            try:
                from fastembed import TextEmbedding
            except ImportError as e:
                import logging
                logging.warning(
                    "[OEM] fastembed is not installed. Hybrid search is disabled. "
                    "Install it with 'uv tool install \"git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge[semantic]\"'."
                )
                return None

            cache_path = str(Path.home() / ".cache" / "fastembed")
            try:
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_path, local_files_only=True)
            except Exception:
                print("\n[OEM] Embedding model 'BAAI/bge-small-en-v1.5' not found in cache.", file=sys.stderr)
                print("[OEM] Downloading model (~67 MB)...", file=sys.stderr)
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_path, local_files_only=False)
        return self._model

    def layout(self, project: str | None = None) -> ProjectLayout:
        """Return a ProjectLayout for the resolved .oem directory.

        KnowledgeEngine.layout() is the single resolver — ProjectLayout itself
        is a pure value object and does no path resolution.
        """
        return ProjectLayout(root=self._resolve_harness(project))

    def _registry_path(self, project: str | None = None) -> Path:
        return self.layout(project).registry_path

    def _events_path(self, project: str | None = None) -> Path:
        return self.layout(project).events_path

    def _sessions_dir(self, project: str | None = None) -> Path:
        return self.layout(project).sessions_dir

    def _concepts_dir(self, project: str | None = None) -> Path:
        return self.layout(project).concepts_dir

    def _wiki_paths(self, project: str | None = None) -> dict:
        return self.layout(project).wiki_paths()



    # --- Internal State Management helper methods ---
    # Deprecated: StateService now owns registry/events I/O directly via oem_knowledge.fs.
    # These stubs remain temporarily for any external callers; they will be removed in a future cleanup.

    def _load_registry_before_extraction(self, project: str | None = None) -> dict:
        warnings.warn(
            "engine._load_registry_before_extraction() is deprecated. Use engine.state._load_registry() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state._load_registry(project)

    def _save_registry_before_extraction(self, registry: dict, project: str | None = None):
        warnings.warn(
            "engine._save_registry_before_extraction() is deprecated. Use engine.state._save_registry() directly.",
            DeprecationWarning, stacklevel=2
        )
        self.state._save_registry(registry, project)

    def _load_events_before_extraction(self, project: str | None = None) -> list[dict]:
        warnings.warn(
            "engine._load_events_before_extraction() is deprecated. Use engine.state._load_events() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state._load_events(project)

    def _append_event_before_extraction(self, event: dict | KnowledgeEvent, project: str | None = None):
        warnings.warn(
            "engine._append_event_before_extraction() is deprecated. Use engine.state._append_event() directly.",
            DeprecationWarning, stacklevel=2
        )
        self.state._append_event(event, project)


    # --- Orchestrator Level Methods kept on engine ---

    def init_project(self, name: str) -> dict:
        path = Path(name)
        if path.is_absolute():
            project_dir = path
        else:
            base = Path.cwd() if not self.project_path else self.project_path
            project_dir = base / name if not (base / name).exists() else base

        harness = project_dir / OEM_DIR

        created_dirs = []
        for d in DEFAULT_DIRS:
            p = harness / d
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)

        created_files = []

        for fname, content in [
            (
                "wiki/inbox.md",
                "# Wiki Inbox\n\nAppend raw lessons, API observations, and style guidelines here.\n",
            ),
            (
                "progress.md",
                f"# Project Progress Log — {name}\n\n- **{time.strftime('%Y-%m-%d')}:** OpenEmpiric initialized.\n",
            ),
            (
                "session-handoff.md",
                (
                    "# Session Handoff\n\n"
                    "## Historical Context\n"
                    "Initialized project layout.\n\n"
                    "## Previous Decisions\n"
                    "- Setup initial directory structure.\n\n"
                    "## Open Questions\n"
                    "- What goals/concepts should be mapped next?\n"
                ),
            ),
        ]:
            fp = harness / fname
            if not fp.exists():
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
                created_files.append(fname)

        # Install adapter skills
        try:
            from oem_knowledge.adapters import get_adapter
            adapter = get_adapter("opencode", self, project_dir)
            if adapter.install_skill():
                created_files.append("skills/openempiric.yaml")
        except Exception:
            pass

        return {
            "status": "success",
            "message": f"OpenEmpiric initialized in {project_dir}",
            "created_directories": created_dirs,
            "created_files": created_files,
        }

    def is_initialized(self, project: str | None = None) -> bool:
        """Check if project has been initialized (has .oem/ dir with state/)."""
        p = Path(project or self.project_path or ".").resolve()
        root = find_harness_root(p)
        if root is None:
            return False
        harness = root / OEM_DIR
        return (harness / "state").is_dir()

    def warmup(self) -> dict:
        import sys
        print("[OEM] Warming up embedding model 'BAAI/bge-small-en-v1.5'...", file=sys.stderr)
        model = self.model
        if model is None:
            print("[OEM] Warmup failed: fastembed is not installed.", file=sys.stderr)
            return {
                "status": "error",
                "message": "fastembed is not installed. Install it with 'uv tool install \"git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge[semantic]\"'.",
            }
        print("[OEM] Embedding model ready (cached globally, one-time per machine).", file=sys.stderr)
        return {"status": "success", "model": "BAAI/bge-small-en-v1.5"}

    def warmup_if_needed(self) -> dict:
        """Warm up embedding model if not already cached/loaded."""
        try:
            from fastembed import TextEmbedding
            cache_path = str(Path.home() / ".cache" / "fastembed")
            TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=cache_path, local_files_only=True)
            return {"status": "success", "cached": True}
        except Exception:
            return self.warmup()


    def restore_session_state(self, project: str | None = None) -> dict:
        h = self._resolve_harness(project)
        state_dir = h / "state"

        progress = h / "progress.md"
        handoff = h / "session-handoff.md"
        goals = state_dir / "current-goals.md"
        issues = state_dir / "open-issues.md"
        decisions = state_dir / "active-decisions.md"

        active_goals = []
        blockers = []
        discoveries = []
        full_content = ""

        for fp, target_list, attr in [
            (goals, active_goals, "goals"),
            (handoff, active_goals, "handoff"),
            (issues, blockers, "issues"),
            (decisions, discoveries, "decisions"),
            (progress, None, "progress"),
        ]:
            if fp.exists():
                text = fp.read_text()
                full_content += text + "\n"
                if attr == "handoff":
                    # Parse "Historical Context"
                    hist_match = re.search(r"## Historical Context\s*\n\s*([^#]+)", text)
                    if hist_match:
                        lines_of_hist = hist_match.group(1).strip().split("\n")
                        if lines_of_hist:
                            first_line = lines_of_hist[0].strip().lstrip("-").strip()
                            if first_line:
                                active_goals.append(first_line)
                    
                    # Parse "Previous Decisions"
                    dec_match = re.search(r"## Previous Decisions\s*\n\s*([^#]+)", text)
                    if dec_match:
                        for line in dec_match.group(1).splitlines():
                            s = line.strip()
                            if s.startswith("-"):
                                clean = re.sub(r"^-\s*(?:\[[ xX/]\])?\s*", "", s).strip()
                                if clean and clean not in discoveries:
                                    discoveries.append(clean)
                    
                    # Parse "Open Questions"
                    q_match = re.search(r"## Open Questions\s*\n\s*([^#]+)", text)
                    if q_match:
                        for line in q_match.group(1).splitlines():
                            s = line.strip()
                            if s.startswith("-"):
                                clean = re.sub(r"^-\s*(?:\[[ xX/]\])?\s*", "", s).strip()
                                if clean and clean not in active_goals:
                                    active_goals.append(clean)

                    # Fallback to Next Action
                    if not active_goals:
                        m = re.search(r"## Next Action\s*\n\s*(?:-\s*)?([^\n#]+)", text)
                        if m:
                            active_goals.append(m.group(1).strip())
                elif attr == "goals":
                    for line in text.splitlines():
                        s = line.strip()
                        if s.startswith("-"):
                            clean = re.sub(r"^-\s*(?:\[[ xX/]\])?\s*", "", s)
                            if clean.strip():
                                active_goals.append(clean.strip())
                elif attr == "issues":
                    for line in text.splitlines():
                        s = line.strip()
                        if s.startswith("-"):
                            clean = re.sub(r"^-\s*(?:\[[ xX/]\])?\s*", "", s)
                            if clean.strip():
                                blockers.append(clean.strip())
                elif attr == "decisions":
                    for line in text.splitlines():
                        s = line.strip()
                        if s.startswith("-") and s.strip("-").strip():
                            discoveries.append(s.strip("-").strip())

        keywords = " ".join(re.findall(r"\w+", full_content.strip())[:15])
        rec_files = []
        if keywords:
            try:
                results = self.search.search(keywords, k=4)
                rec_files = [r["metadata"]["rel_path"] for r in results]
            except Exception:
                pass

        global_concepts = []
        try:
            from .vault import GlobalVault
            vault = GlobalVault()
            global_concepts = vault.get_global_context()
        except Exception:
            pass

        return {
            "status": "success",
            "active_goals": active_goals[:5],
            "blockers": blockers[:5],
            "recent_discoveries": discoveries[:5],
            "recommended_files": rec_files,
            "query_context": keywords[:60],
            "global_concepts": global_concepts,
        }


    def get_events(
        self,
        project: str | None = None,
        concept: str = "",
        event_type: str = "",
        session_id: str = "",
    ) -> list[dict]:
        warnings.warn(
            "engine.get_events() is deprecated. Use engine.state.get_events() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state.get_events(project, concept, event_type, session_id)

    def get_event(self, project: str | None = None, event_id: str = "") -> dict:
        warnings.warn(
            "engine.get_event() is deprecated. Use engine.state.get_event() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state.get_event(project, event_id)


    def session_commit(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
        update_index: bool = True,
        index_budget_seconds: float | None = 10.0,
        progress_callback = None,
        events: list[dict] | None = None,
        extraction_mode: str = "auto",
        timeout_seconds: float | None = None,
    ) -> dict:
        from oem_knowledge.fs import LockTimeoutError
        from oem_knowledge.runtime.supervisor import CommitProgressSupervisor
        
        timer = PhaseTimer()
        start_time = time.perf_counter()
        
        progress = CommitProgressSupervisor()
        progress.start()
        
        warnings = []
        res = {"report_path": None, "knowledge_events": [], "explainability": {}}
        mat_log = []
        idx_res = {}
        links_updated = 0
        failed_step = None
        status = "success"
        message = ""

        try:
            progress.update_step("transcript", "running")
            with timer.phase("load_state", progress_callback):
                if not session_id:
                    session_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"
            progress.update_step("transcript", "success")

            progress.update_step("reflection", "running")
            with timer.phase("reflection", progress_callback):
                res = self.reflection.reflect_session(
                    project,
                    conversation_text,
                    session_id=session_id,
                    telemetry=telemetry,
                    session_started_at=session_started_at,
                    progress_callback=progress_callback,
                    events=events,
                    extraction_mode=extraction_mode,
                    timeout_seconds=timeout_seconds,
                )
                if "phase_timings" in res:
                    timer.timings.update(res["phase_timings"])

                if res.get("status") == "empty" or (res.get("status") == "partial" and res.get("failed_step") == "llm_extraction"):
                    progress.update_step("reflection", "failed")
                    timer.timings["total"] = time.perf_counter() - start_time
                    p_timings = {
                        "load_state": timer.timings.get("load_state", 0.0),
                        "reflection": timer.timings.get("reflection", 0.0),
                        "append_events": 0.0,
                        "materialization": 0.0,
                        "search_index": 0.0,
                        "write_report": 0.0,
                        "cleanup": 0.0,
                        "total": timer.timings["total"],
                    }
                    return {
                        "status": res.get("status"),
                        "failed_step": res.get("failed_step"),
                        "message": res.get("message", "Reflection did not complete successfully."),
                        "suggestion": res.get("suggestion"),
                        "events_written": 0,
                        "warnings": res.get("warnings", []),
                        "phase_timings": p_timings,
                    }

                if res.get("status") == "error":
                    progress.update_step("reflection", "failed")
                    timer.timings["total"] = time.perf_counter() - start_time
                    p_timings = {
                        "load_state": timer.timings.get("load_state", 0.0),
                        "reflection": timer.timings.get("reflection", 0.0),
                        "append_events": timer.timings.get("append_events", 0.0),
                        "materialization": 0.0,
                        "search_index": 0.0,
                        "write_report": timer.timings.get("write_report", 0.0),
                        "cleanup": 0.0,
                        "total": timer.timings["total"],
                    }
                    return {
                        "status": "error",
                        "failed_step": "reflection",
                        "message": res.get("message", "Reflection failed"),
                        "report_path": res.get("report_path"),
                        "knowledge_events": res.get("knowledge_events", []),
                        "materialized_log": [],
                        "links_updated": 0,
                        "index_stats": {},
                        "explainability": res.get("explainability", {}),
                        "warnings": res.get("warnings", []),
                        "phase_timings": p_timings,
                    }
            progress.update_step("reflection", "success")

            progress.update_step("materialization", "running")
            with timer.phase("materialization", progress_callback):
                mat_res = self.materialization.materialize_concepts(project)
                if mat_res.get("status") == "error":
                    progress.update_step("materialization", "failed")
                    timer.timings["total"] = time.perf_counter() - start_time
                    p_timings = {
                        "load_state": timer.timings.get("load_state", 0.0),
                        "reflection": timer.timings.get("reflection", 0.0),
                        "append_events": timer.timings.get("append_events", 0.0),
                        "materialization": timer.timings.get("materialization", 0.0),
                        "search_index": 0.0,
                        "write_report": timer.timings.get("write_report", 0.0),
                        "cleanup": 0.0,
                        "total": timer.timings["total"],
                    }
                    return {
                        "status": "error",
                        "failed_step": "materialization",
                        "message": mat_res.get("message", "Materialization failed"),
                        "report_path": res.get("report_path"),
                        "knowledge_events": res.get("knowledge_events", []),
                        "materialized_log": [],
                        "links_updated": 0,
                        "index_stats": {},
                        "explainability": res.get("explainability", {}),
                        "warnings": warnings + mat_res.get("warnings", []),
                        "phase_timings": p_timings,
                    }
                mat_log = mat_res.get("materialized", [])
                warnings.extend(mat_res.get("warnings", []))
            progress.update_step("materialization", "success")

            links_updated = self.materialization.update_graph(project).get("links_updated", 0)

            idx_res = {
                "status": "success",
                "new": 0,
                "updated": 0,
                "scanned": 0,
                "unchanged": 0,
                "failed": 0,
                "new_chunks": 0,
                "updated_chunks": 0,
                "unchanged_chunks": 0,
                "failed_chunks": 0,
                "failed_files": [],
            }
            index_failed_reason = None
            
            progress.update_step("index", "running")
            if not update_index or index_budget_seconds == 0:
                progress.update_step("index", "success")
                warnings.append("Search indexing skipped after budget; run `oem index --project ...` to rebuild derived search index.")
            else:
                with timer.phase("search_index", progress_callback):
                    try:
                        def index_progress(current, total):
                            mode_str = "embeddings" if self.search.resolve_retrieval_mode() == "hybrid" else "files"
                            progress.update_step("index", "running", detail=f"{current} / {total} {mode_str}")
                        
                        idx_res = self.search.index_all(
                            progress_callback=index_progress,
                            budget_seconds=index_budget_seconds
                        )
                        
                        if idx_res.get("status") == "error":
                            progress.update_step("index", "failed")
                            timer.timings["total"] = time.perf_counter() - start_time
                            p_timings = {
                                "load_state": timer.timings.get("load_state", 0.0),
                                "reflection": timer.timings.get("reflection", 0.0),
                                "append_events": timer.timings.get("append_events", 0.0),
                                "materialization": timer.timings.get("materialization", 0.0),
                                "search_index": timer.timings.get("search_index", 0.0),
                                "write_report": timer.timings.get("write_report", 0.0),
                                "cleanup": 0.0,
                                "total": timer.timings["total"],
                            }
                            return {
                                "status": "error",
                                "failed_step": "indexing",
                                "message": idx_res.get("error", "Indexing failed"),
                                "report_path": res.get("report_path"),
                                "knowledge_events": res.get("knowledge_events", []),
                                "materialized_log": mat_log,
                                "links_updated": links_updated,
                                "index_stats": idx_res,
                                "explainability": res.get("explainability", {}),
                                "warnings": warnings + [f"indexing failed: {idx_res.get('error')}"],
                                "phase_timings": p_timings,
                            }
                        elif idx_res.get("status") == "partial" and idx_res.get("error") == "Indexing budget exceeded":
                            index_failed_reason = "Indexing budget exceeded"
                            failed_step = "indexing"
                            status = "partial"
                            warnings.append("Search indexing skipped after timeout budget; run `oem index --project ...` to rebuild derived search index.")
                            progress.update_step("index", "failed")
                        elif idx_res.get("status") == "partial":
                            index_failed_reason = idx_res.get("error") or f"Some files failed to index: {', '.join(idx_res.get('failed_files', []))}"
                            failed_step = "indexing"
                            status = "partial"
                            progress.update_step("index", "failed")
                        else:
                            progress.update_step("index", "success")
                    except Exception as e:
                        index_failed_reason = str(e)
                        progress.update_step("index", "failed")
                        failed_step = "indexing"
                        status = "partial"
                        idx_res = {
                            "status": "error",
                            "new": 0, "updated": 0, "scanned": 0, "unchanged": 0, "failed": 0,
                            "new_chunks": 0, "updated_chunks": 0, "unchanged_chunks": 0, "failed_chunks": 0,
                            "failed_files": [],
                            "error": str(e),
                        }
                        warnings.append(f"Indexing error: {e}")

            progress.update_step("vault", "running")
            with timer.phase("cleanup", progress_callback):
                import os
                if os.environ.get("OEM_VAULT_SYNC") == "1":
                    try:
                        from .vault import GlobalVault
                        vault = GlobalVault()
                        local_reg = self.state._load_registry(project)
                        concepts_dir = self._concepts_dir(project)
                        vault.sync_from_registry(local_reg, concepts_dir)
                    except Exception:
                        pass
            progress.update_step("vault", "success")

        except LockTimeoutError as e:
            timer.timings["total"] = time.perf_counter() - start_time
            p_timings = {
                "load_state": timer.timings.get("load_state", 0.0),
                "reflection": timer.timings.get("reflection", 0.0),
                "append_events": timer.timings.get("append_events", 0.0),
                "materialization": timer.timings.get("materialization", 0.0),
                "search_index": timer.timings.get("search_index", 0.0),
                "write_report": timer.timings.get("write_report", 0.0),
                "cleanup": timer.timings.get("cleanup", 0.0),
                "total": timer.timings["total"],
            }
            return {
                "status": "error",
                "failed_step": timer.failed_phase or timer.current_phase or "state",
                "message": f"Lock acquisition timeout: {e}",
                "report_path": None,
                "knowledge_events": [],
                "materialized_log": [],
                "links_updated": 0,
                "index_stats": {},
                "explainability": {},
                "warnings": [f"Lock failure: Registry/state/runtime_events lock contention on file. {e}"],
                "phase_timings": p_timings,
            }

        timer.timings["total"] = time.perf_counter() - start_time
        explainability = res.get("explainability", {})
        explainability["materialized"] = len(mat_log)
        
        p_timings = {
            "load_state": timer.timings.get("load_state", 0.0),
            "reflection": timer.timings.get("reflection", 0.0),
            "append_events": timer.timings.get("append_events", 0.0),
            "materialization": timer.timings.get("materialization", 0.0),
            "search_index": timer.timings.get("search_index", 0.0),
            "write_report": timer.timings.get("write_report", 0.0),
            "cleanup": timer.timings.get("cleanup", 0.0),
            "total": timer.timings["total"],
        }

        ret_status = status
        if index_failed_reason:
            warnings.append(f"Session commit partial: reflection/materialization succeeded, indexing failed: {index_failed_reason}")
            ret_status = "partial"

        return {
            "status": ret_status,
            "failed_step": failed_step,
            "report_path": res.get("report_path"),
            "knowledge_events": res.get("knowledge_events", []),
            "materialized_log": mat_log,
            "links_updated": links_updated,
            "index_stats": idx_res,
            "explainability": explainability,
            "warnings": warnings + res.get("warnings", []),
            "phase_timings": p_timings,
        }

    def record_outcome(
        self,
        outcome: str,
        referenced_concepts: list[str] | None = None,
        reason: str | None = None,
        session_id: str | None = None,
        project: str | None = None,
        goal_satisfaction: float | None = None,
    ) -> dict:
        warnings.warn(
            "engine.record_outcome() is deprecated. Use engine.state.record_outcome() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state.record_outcome(
            outcome, referenced_concepts, reason, session_id, project, goal_satisfaction
        )

    def calculate_fitness(self, project: str | None = None) -> dict[str, ConceptFitness]:
        warnings.warn(
            "engine.calculate_fitness() is deprecated. Use engine.fitness.calculate_fitness() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.fitness.calculate_fitness(project)

    def detect_stale_concepts(self, n_sessions: int = 5, project: str | None = None) -> list[dict]:
        warnings.warn(
            "engine.detect_stale_concepts() is deprecated. Use engine.state.detect_stale_concepts() directly.",
            DeprecationWarning, stacklevel=2
        )
        return self.state.detect_stale_concepts(n_sessions, project)

    def propose_merges(self, similarity_threshold: float = 0.85, project: str | None = None) -> list[dict]:
        from oem_knowledge.evolution import ConceptEvolutionEngine
        ev = ConceptEvolutionEngine(self)
        return ev.propose_merges(similarity_threshold, project)

    def detect_contradictions(self, project: str | None = None) -> list[dict]:
        from oem_knowledge.evolution import ContradictionDetector
        detector = ContradictionDetector(self)
        return detector.detect_contradictions(project)

    def embedding_cache_ready(self) -> bool:
        """Inspect if the embedding model is present in fastembed cache without instantiating it."""
        try:
            from fastembed import TextEmbedding
            if TextEmbedding.__name__ == "MockTextEmbedding":
                return True
        except Exception:
            pass

        try:
            from fastembed.common.utils import define_cache_dir
            from pathlib import Path
            
            # Check both the custom cache dir we use and the default fastembed one
            cache_dirs = [
                Path.home() / ".cache" / "fastembed",
                Path(define_cache_dir(None))
            ]
            
            for cache_dir in cache_dirs:
                # Check HuggingFace model cache directory (standard/preferred)
                hf_dir = cache_dir / "models--qdrant--bge-small-en-v1.5-onnx-q"
                if hf_dir.is_dir() and any(hf_dir.iterdir()):
                    return True
                    
                # Check GCS fallback directories
                gcs_dir = cache_dir / "bge-small-en-v1.5"
                if gcs_dir.is_dir() and any(gcs_dir.iterdir()):
                    return True
                    
                fast_gcs_dir = cache_dir / "fast-bge-small-en-v1.5"
                if fast_gcs_dir.is_dir() and any(fast_gcs_dir.iterdir()):
                    return True
                    
            return False
        except Exception:
            return False

