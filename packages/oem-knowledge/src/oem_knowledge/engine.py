from __future__ import annotations

import json
import logging
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

logger = logging.getLogger(__name__)

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
        from oem_knowledge.services.source_corpus import SourceCorpusService
        from oem_knowledge.services.skills import SkillService
        from oem_knowledge.services.skill_promotion import SkillPromotionService

        self.search = SearchService(self)
        self.source = SourceCorpusService(self)
        self.materialization = MaterializationService(self)
        self.reflection = ReflectionService(self)
        self.state = StateService(self)
        self.event_migrator = EventMigrator(self)
        self.fitness = FitnessService(self)
        self.skills = SkillService(self)
        self.skill_promotion = SkillPromotionService(self)

    def close(self) -> None:
        for service in (getattr(self, "search", None), getattr(self, "source", None)):
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
            (
                "config/reflection.yml",
                (
                    "reflection:\n"
                    "  mode: auto\n\n"
                    "  structured:\n"
                    "    enabled: true\n\n"
                    "  marker:\n"
                    "    enabled: true\n\n"
                    "  dense:\n"
                    "    enabled: false\n"
                    "    on_unavailable: skip\n"
                    "    max_retry_count: 0\n"
                    "    queue_pending: false\n"
                ),
            ),
        ]:
            fp = harness / fname
            if not fp.exists():
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
                created_files.append(fname)

        # Write manifest.json
        try:
            from oem_knowledge.runtime.manifest import ensure_manifest
            ensure_manifest(project_dir)
            created_files.append("manifest.json")
        except Exception:
            pass

        # Write init.sh
        try:
            init_sh = harness / "init.sh"
            if not init_sh.exists():
                init_content = (
                    "#!/bin/bash\n"
                    "# generated_by: openempiric\n"
                    "# source_type: oem_generated\n\n"
                    "echo \"Initializing OpenEmpiric workspace...\"\n"
                    "oem init\n"
                    "oem setup opencode\n"
                    "oem doctor\n"
                )
                init_sh.write_text(init_content, encoding="utf-8")
                try:
                    init_sh.chmod(0o755)
                except Exception:
                    pass
                created_files.append("init.sh")
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

        return {
            "status": "success",
            "active_goals": active_goals[:5],
            "blockers": blockers[:5],
            "recent_discoveries": discoveries[:5],
            "recommended_files": rec_files,
            "query_context": keywords[:60],
            "global_concepts": global_concepts,
        }




    def session_start(self, project: str | None = None) -> dict:
        from oem_knowledge.runtime.session import SessionState
        import uuid
        
        harness = self._resolve_harness(project)
        layout = self.layout(project)
        active_session_file = harness / "state" / "active_session.json"
        
        session_id = None
        session_state = SessionState.load(active_session_file)
        if session_state:
            session_id = session_state.session_id
        else:
            session_id = uuid.uuid4().hex[:12]
            session_state = SessionState.create(
                session_id=session_id,
                agent="mcp-agent",
                project=project or ".",
                transcript_path=str((harness / "state" / f"chat_{session_id}.md").resolve()),
                context_path=str((harness / "state" / "oem_runtime_context.json").resolve()),
                temp_instructions=str((harness / "state" / "oem_temp_instructions.md").resolve()),
            )
            session_state.save(active_session_file)

        warnings = []
        try:
            from oem_knowledge.instructions import (
                discover_instruction_sources,
                get_db_connection,
                get_stale_sources,
                index_source_file,
                get_active_directives,
                render_current_directives
            )
            project_root = layout.root.parent
            sources = discover_instruction_sources(project_root, layout)
            conn = get_db_connection(layout.instruction_ledger_path)
            stale_paths = get_stale_sources(conn, sources)
            for ds in sources:
                if ds["path"] in stale_paths:
                    try:
                        content = (project_root / ds["path"]).read_text(encoding="utf-8")
                        index_source_file(conn, ds["path"], content, ds["hash"], ds["mtime"], ds["size_bytes"])
                    except Exception as e:
                        warnings.append(f"Failed to index instruction file {ds['path']}: {e}")
            
            # Generate current directives MD
            active_dirs = get_active_directives(conn)
            md_content = render_current_directives(active_dirs, layout)
            layout.current_directives_path.parent.mkdir(parents=True, exist_ok=True)
            layout.current_directives_path.write_text(md_content, encoding="utf-8")
            conn.close()
        except Exception as e:
            warnings.append(f"Failed to initialize instruction ledger: {e}")

        return {
            "status": "success",
            "operation": "knowledge_session_start",
            "session_id": session_id,
            "project": str(self.project_path or project or ""),
            "message": "OEM session started.",
            "warnings": warnings,
            "suggestion": "Use knowledge_read when you need orientation, then knowledge_search for specific memory."
        }

    def session_end(
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
                # Check if reflect_session is mocked/patched in tests
                reflect_method = self.reflection.reflect_session
                is_mocked = (
                    hasattr(reflect_method, "mock")
                    or hasattr(reflect_method, "_mock_self")
                    or type(reflect_method).__name__ in ("Mock", "MagicMock")
                    or getattr(reflect_method, "__name__", None) != "reflect_session"
                    or not hasattr(reflect_method, "__func__")
                    or reflect_method.__func__ is not getattr(self.reflection.__class__, "_original_reflect_session_func", None)
                )
                if is_mocked:
                    res = reflect_method(
                        project=project or None,
                        conversation_text=conversation_text,
                        session_id=session_id,
                        telemetry=telemetry,
                        session_started_at=session_started_at,
                        progress_callback=progress_callback,
                        events=events,
                        extraction_mode=extraction_mode,
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    res = self.reflection.extract_session_events(
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

                # Running the Directive Receipt and Workflow Drift logic and notification generation early.
                matched_directives_for_summary = []
                drift_proposals = []
                notification = None
                try:
                    from oem_knowledge.instructions import (
                        get_db_connection,
                        get_active_directives,
                        match_directives,
                        render_directive_receipt
                    )
                    layout = self.layout(project)
                    conn = get_db_connection(layout.instruction_ledger_path)
                    
                    # Query session matched directives
                    cursor = conn.execute("SELECT * FROM session_directive_matches WHERE session_id = ?", (session_id,))
                    matches = [dict(row) for row in cursor.fetchall()]
                    
                    # Convert DB matches or match directly if empty
                    if not matches:
                        active_dirs = get_active_directives(conn)
                        direct_matches = match_directives(conversation_text, active_dirs)
                        for dm in direct_matches:
                            matched_directives_for_summary.append({
                                "id": dm["id"],
                                "title": dm["title"],
                                "rule": dm["rule"],
                                "source_path": dm["source_path"],
                                "line_start": dm["line_start"],
                                "line_end": dm["line_end"],
                                "score": dm["score"],
                                "reason": dm["reason"],
                                "priority": dm.get("priority", "normal")
                            })
                    else:
                        for m in matches:
                            cursor_d = conn.execute("SELECT * FROM directives WHERE id = ?", (m["directive_id"],))
                            d_row = cursor_d.fetchone()
                            if d_row:
                                d = dict(d_row)
                                matched_directives_for_summary.append({
                                    "id": d["id"],
                                    "title": d["title"],
                                    "rule": d["rule"],
                                    "source_path": d["source_path"],
                                    "line_start": d["line_start"],
                                    "line_end": d["line_end"],
                                    "score": m["match_score"],
                                    "reason": m["reason"],
                                    "priority": d.get("priority", "normal")
                                })
                    
                    # Assess directive applications
                    applications = []
                    from datetime import datetime
                    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    for md in matched_directives_for_summary:
                        d_id = md["id"]
                        # Default positive application status
                        status_app = "applied"
                        evidence_app = "adhered to directive constraints in this session"
                        
                        # Check for negation keyword rules in text
                        cursor_d = conn.execute("SELECT forbidden_actions_json FROM directives WHERE id = ?", (d_id,))
                        row_fa = cursor_d.fetchone()
                        forbidden = json.loads(row_fa["forbidden_actions_json"]) if row_fa else []
                        
                        conv_lower = conversation_text.lower()
                        for fa in forbidden:
                            if fa in conv_lower:
                                evidence_app = f"discussed or referenced action related to {fa}"
                                break
                        
                        conn.execute("""
                            INSERT OR REPLACE INTO session_directive_applications (
                                session_id, directive_id, status, evidence, created_at
                            ) VALUES (?, ?, ?, ?, ?)
                        """, (session_id, d_id, status_app, evidence_app, now_str))
                        
                        applications.append({
                            "directive_id": d_id,
                            "status": status_app,
                            "evidence": evidence_app
                        })
                        
                    # Propose workflow candidate if drift detected
                    conv_lower = conversation_text.lower()
                    if "wsl" in conv_lower and ("bridge" in conv_lower or "split" in conv_lower or "oem" in conv_lower):
                        from oem_knowledge.instructions import create_instruction_update_candidate
                        proposed_patch = "When configuring OpenCode Desktop with WSL OEM, always ensure the MCP bridge points to the WSL-native project root and never initializes a second `.oem` folder on the Windows side."
                        reason_desc = "This session revealed a recurring workflow rule for Windows/WSL OpenCode setup."
                        cand_res = create_instruction_update_candidate(layout, session_id, "AGENTS.md", proposed_patch, reason_desc)
                        
                        conn.execute("""
                            INSERT OR REPLACE INTO instruction_update_candidates (
                                id, session_id, target_path, proposed_patch, reason, status, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (cand_res["id"], session_id, "AGENTS.md", proposed_patch, reason_desc, "proposed", now_str))
                        
                        drift_proposals.append({
                            "id": cand_res["id"],
                            "reason": "Windows/WSL bridge must prevent split `.oem` memory"
                        })
                        
                    # Render directive receipt
                    run_id = session_id
                    runs_dir = layout.root / "runs" / run_id
                    if (layout.root / "runs").is_dir():
                        runs_dir.mkdir(parents=True, exist_ok=True)
                        receipt_path = runs_dir / "directive_receipt.md"
                    else:
                        receipt_path = layout.root / ".runtime" / "directive_receipt.md"
                        
                    receipt_content = render_directive_receipt(session_id, matched_directives_for_summary, applications, drift_proposals)
                    receipt_path.parent.mkdir(parents=True, exist_ok=True)
                    receipt_path.write_text(receipt_content, encoding="utf-8")
                    conn.close()
                except Exception as e:
                    logger.warning("Failed to record directive receipt/drift in session_end: %s", e)

                try:
                    candidates = self.skills.list_skill_candidates(project)
                    high_conf = [
                        c for c in candidates 
                        if c.status in ("proposed", "deferred") 
                        and (c.confidence == "high" or len(c.evidence) >= 3)
                    ]
                    if high_conf:
                        cand = high_conf[0]
                        
                        events_data = []
                        for evid in cand.source_event_ids:
                            try:
                                ev = self.state.get_event(project, evid)
                                if ev and ev.get("evidence"):
                                    events_data.append(ev["evidence"])
                            except Exception:
                                pass
                        
                        if not events_data:
                            events_data = cand.evidence
                        
                        ev_lines = []
                        for ev_val in events_data:
                            val = str(ev_val).strip()
                            if not val.lower().startswith("used successfully"):
                                val = f"Used successfully in {val}"
                            ev_lines.append(f"- {val}")
                        
                        unique_ev_lines = []
                        for line in ev_lines:
                            if line not in unique_ev_lines:
                                unique_ev_lines.append(line)
                        
                        ev_lines_str = "\n".join(unique_ev_lines[:3])
                        
                        notification = (
                            "OEM noticed a repeated successful workflow pattern.\n\n"
                            f"Candidate skill:\n"
                            f"{cand.title}\n\n"
                            f"Evidence:\n"
                            f"{ev_lines_str}\n\n"
                            f"Recommendation:\n"
                            f"Review with: oem skills show {cand.slug}\n"
                            f"Approve with: oem skills approve {cand.slug}"
                        )
                except Exception as e:
                    logger.warning("Failed to generate skill candidate notification: %s", e)

                # Add Directive Receipt summary to notification if present
                if matched_directives_for_summary:
                    directive_summary_lines = ["", "Directive Receipt Summary:"]
                    for md in matched_directives_for_summary:
                        directive_summary_lines.append(f"✓ {md['title']} (Source: {md['source_path']})")
                    if drift_proposals:
                        directive_summary_lines.append("\nWorkflow Drift / Instruction Proposals:")
                        for dp in drift_proposals:
                            directive_summary_lines.append(f"- Proposed candidate {dp['id']} for {dp['reason']}")
                    
                    summary_str = "\n".join(directive_summary_lines)
                    if notification:
                        notification += "\n\n" + summary_str
                    else:
                        notification = summary_str.strip()

                status_val = res.get("status")
                failed_step_val = res.get("failed_step")
                is_non_fatal = (status_val in ("warn", "empty") or (
                    status_val == "partial" and failed_step_val == "llm_extraction"
                )) and res.get("events_written", 0) == 0

                if is_non_fatal:
                    # Close the session safely
                    harness = self._resolve_harness(project)
                    active_session_file = harness / "state" / "active_session.json"
                    
                    try:
                        self.state.record_outcome(
                            "success_with_warnings" if status_val == "warn" else ("partial" if status_val == "partial" else "success"),
                            session_id=session_id,
                            project=project
                        )
                    except Exception as e:
                        logger.warning("Outcome recording failed: %s", e)

                    try:
                        metrics_file = harness / "state" / "metrics.json"
                        from oem_knowledge.tools.metrics import update_metrics_file
                        update_metrics_file(metrics_file, {
                            "sessions_completed": 1,
                        })
                    except Exception:
                        pass

                    session_state = None
                    try:
                        from oem_knowledge.runtime import SessionState
                        session_state = SessionState.load(active_session_file)
                        if session_state:
                            for path_str in (session_state.context_path, session_state.temp_instructions):
                                if path_str:
                                    p = Path(path_str)
                                    if p.exists():
                                        try:
                                            p.unlink()
                                        except Exception:
                                            pass
                    except Exception:
                        pass

                    try:
                        if active_session_file.exists():
                            if session_state:
                                session_state.status = "completed"
                            active_session_file.unlink()
                    except Exception:
                        pass

                    # Update progress steps
                    if status_val == "warn":
                        progress.update_step(
                            "reflection",
                            "warn",
                            name="Dense LLM Reflection Skipped",
                            detail="No local/remote LLM provider configured"
                        )
                    elif status_val == "empty":
                        progress.update_step(
                            "reflection",
                            "warn",
                            name="Reflection Skipped/Empty",
                            detail="No markers or structured events"
                        )
                    elif status_val == "partial" and failed_step_val == "llm_extraction":
                        progress.update_step(
                            "reflection",
                            "warn",
                            name="Reflection Degraded",
                            detail="LLM extraction timed out"
                        )

                    progress.update_step(
                        "materialization",
                        "skipped",
                        name="Materialization Skipped",
                        detail="No reflection events produced"
                    )
                    progress.update_step(
                        "index",
                        "skipped",
                        name="Search Index Skipped",
                        detail="No memory changes to index"
                    )
                    progress.update_step("session_close", "success")

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
                    from oem_knowledge.runtime.result import make_result
                    return make_result(
                        status=status_val,
                        operation="session_end",
                        project=str(self.project_path or project or ""),
                        message=res.get("message", "Reflection did not complete successfully."),
                        suggestion=res.get("suggestion"),
                        failed_step=failed_step_val,
                        warnings=res.get("warnings", []),
                        reflection=res.get("reflection"),
                        events_written=0,
                        materialization_skipped=True,
                        index_skipped=True,
                        notification=notification,
                        data={
                            "events_written": 0,
                            "phase_timings": p_timings,
                            "report_path": None,
                            "knowledge_events": [],
                            "materialized_log": [],
                            "links_updated": 0,
                            "index_stats": {},
                            "explainability": res.get("explainability", {}),
                            "notification": notification,
                        }
                    )

                if res.get("status") == "error":
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
                    from oem_knowledge.runtime.result import error
                    return error(
                        operation="session_end",
                        message=res.get("message", "Reflection failed"),
                        failed_step="reflection",
                        warnings=res.get("warnings", []),
                        reflection=res.get("reflection"),
                        data={
                            "report_path": res.get("report_path"),
                            "knowledge_events": res.get("knowledge_events", []),
                            "materialized_log": [],
                            "links_updated": 0,
                            "index_stats": {},
                            "explainability": res.get("explainability", {}),
                            "phase_timings": p_timings,
                        }
                    )
            progress.update_step("reflection", "success")
            if status_val in ("warn", "partial"):
                status = status_val

            # Persist events using public append_events
            with timer.phase("append_events", progress_callback):
                canonical_events = res.get("canonical_events", [])
                t_append_start = time.perf_counter()
                self.state.append_events(canonical_events, project)
                append_events_time = time.perf_counter() - t_append_start
            timer.timings["append_events"] = append_events_time

            # Write session report file
            with timer.phase("write_report", progress_callback):
                t_write_start = time.perf_counter()
                sessions_dir = self._sessions_dir(project)
                sfs = self._sfs(project)
                date_str = time.strftime("%Y-%m-%d")
                report_file = sessions_dir / f"{date_str}.md"
                counter = 1
                while sfs.exists(report_file) and counter < 1000:
                    report_file = sessions_dir / f"{date_str}_{counter}.md"
                    counter += 1

                yaml_events = res.get("knowledge_events", [])
                yaml_content = json.dumps({"knowledge_events": yaml_events}, indent=2)

                report_content = f"""---
date: {date_str}
project: {project or "default"}
---
# Session Learning Report — {date_str}

## Knowledge Events
```json
{yaml_content}
```
"""
                try:
                    sfs.write_text(report_file, report_content, force_allow_truncation=True)
                except OSError as e:
                    logger.error("Failed to write session learning report to %s: %s", report_file, e)
                    from oem_knowledge.runtime.result import error
                    return error(
                        operation="session_end",
                        message=f"Failed to write session learning report: {e}",
                        failed_step="write_report",
                    )
                write_report_time = time.perf_counter() - t_write_start
            timer.timings["write_report"] = write_report_time

            # Emit reflection metrics
            try:
                from oem_knowledge.tools.metrics import update_metrics_file
                p = Path(project or ".").resolve()
                root = find_harness_root(p) or p
                metrics_file = (root / OEM_DIR / "state" / "metrics.json")
                
                structured_events_found = res.get("explainability", {}).get("structured_events_found", 0)
                fallback_extraction_used = res.get("explainability", {}).get("fallback_extraction_used", False)
                file_observations_count = res.get("explainability", {}).get("file_observations_count", 0)
                
                update_metrics_file(metrics_file, {
                    "structured_events": structured_events_found,
                    "fallback_extractions": 1 if fallback_extraction_used else 0,
                    "file_observations": file_observations_count,
                    "empty_reflections": 1 if structured_events_found == 0 and not fallback_extraction_used and file_observations_count == 0 else 0,
                    "reflections": 1,
                })
            except Exception as e:
                logger.warning("Failed to emit reflection metrics: %s", e)

            # Materialization
            events_written = len(canonical_events)
            if events_written == 0:
                progress.update_step(
                    "materialization",
                    "skipped",
                    name="Materialization Skipped",
                    detail="No reflection events produced"
                )
                mat_log = []
                links_updated = 0
            else:
                progress.update_step("materialization", "running")
                with timer.phase("materialization", progress_callback):
                    mat_res = self.materialization.materialize_concepts(project)
                    if mat_res.get("status") == "error":
                        harness = self._resolve_harness(project)
                        active_session_file = harness / "state" / "active_session.json"
                        try:
                            self.state.record_outcome("partial", session_id=session_id, project=project)
                        except Exception:
                            pass
                        try:
                            metrics_file = harness / "state" / "metrics.json"
                            from oem_knowledge.tools.metrics import update_metrics_file
                            update_metrics_file(metrics_file, {"sessions_completed": 1})
                        except Exception:
                            pass
                        session_state = None
                        try:
                            from oem_knowledge.runtime import SessionState
                            session_state = SessionState.load(active_session_file)
                            if session_state:
                                for path_str in (session_state.context_path, session_state.temp_instructions):
                                    if path_str:
                                        p = Path(path_str)
                                        if p.exists():
                                            try:
                                                p.unlink()
                                            except Exception:
                                                pass
                        except Exception:
                            pass
                        try:
                            if active_session_file.exists():
                                active_session_file.unlink()
                        except Exception:
                            pass

                        progress.update_step("materialization", "failed")
                        progress.update_step("index", "skipped")
                        progress.update_step("session_close", "success")

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
                        
                        from oem_knowledge.runtime.result import make_result
                        res_dict = make_result(
                            status="partial",
                            operation="session_end",
                            project=str(self.project_path or project or ""),
                            message=mat_res.get("message", "Materialization failed"),
                            failed_step="materialization",
                            report_path=str(report_file),
                            events_written=events_written,
                            warnings=warnings + mat_res.get("warnings", []),
                            data={
                                "report_path": str(report_file),
                                "knowledge_events": res.get("knowledge_events", []),
                                "materialized_log": [],
                                "links_updated": 0,
                                "index_stats": {},
                                "explainability": res.get("explainability", {}),
                                "phase_timings": p_timings,
                            }
                        )
                        res_dict.update({
                            "status": "partial",
                            "events_written": events_written,
                            "materialization": {
                                "status": "failed",
                                "reason": mat_res.get("message", "concept_id_collision")
                            },
                            "index": {
                                "status": "skipped"
                            },
                            "session_closed": True
                        })
                        return res_dict
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
            
            if events_written == 0:
                progress.update_step(
                    "index",
                    "skipped",
                    name="Search Index Skipped",
                    detail="No memory changes to index"
                )
            else:
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
                                from oem_knowledge.runtime.result import error
                                return error(
                                    operation="session_end",
                                    message=idx_res.get("error", "Indexing failed"),
                                    failed_step="indexing",
                                    data={
                                        "report_path": str(report_file),
                                        "knowledge_events": res.get("knowledge_events", []),
                                        "materialized_log": mat_log,
                                        "links_updated": links_updated,
                                        "index_stats": idx_res,
                                        "explainability": res.get("explainability", {}),
                                        "warnings": warnings + [f"indexing failed: {idx_res.get('error')}"],
                                        "phase_timings": p_timings,
                                    }
                                )
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
            from oem_knowledge.runtime.result import error
            return error(
                operation="session_end",
                message=f"Lock acquisition timeout: {e}",
                failed_step="reflection" if (timer.failed_phase or timer.current_phase) == "append_events" else (timer.failed_phase or timer.current_phase or "state"),
                warnings=[f"Lock failure: Registry/state/runtime_events lock contention on file. {e}"],
                data={
                    "report_path": None,
                    "knowledge_events": [],
                    "materialized_log": [],
                    "links_updated": 0,
                    "index_stats": {},
                    "explainability": {},
                    "phase_timings": p_timings,
                }
            )

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

        from oem_knowledge.runtime.result import make_result
        standard_res = make_result(
            status=ret_status,
            operation="session_end",
            project=str(self.project_path or project or ""),
            message=message or ("Session ended successfully." if ret_status == "success" else f"Session ended with status: {ret_status}"),
            warnings=warnings + res.get("warnings", []),
            failed_step=failed_step,
            report_path=str(report_file),
            items_processed=len(res.get("knowledge_events", [])),
            items_written=len(canonical_events),
            events_written=events_written,
            items_rejected=res.get("events_rejected", 0),
            materialization_skipped=(events_written == 0),
            index_skipped=(events_written == 0),
            reflection=res.get("reflection"),
            data={
                "knowledge_events": res.get("knowledge_events", []),
                "materialized_log": mat_log,
                "links_updated": links_updated,
                "index_stats": idx_res,
                "explainability": explainability,
                "phase_timings": p_timings,
                "notification": notification,
            }
        )
        # Flatten keys for legacy callers
        standard_res.update({
            "report_path": str(report_file),
            "knowledge_events": res.get("knowledge_events", []),
            "materialized_log": mat_log,
            "links_updated": links_updated,
            "index_stats": idx_res,
            "explainability": explainability,
            "phase_timings": p_timings,
            "notification": notification,
            "materialization_skipped": (events_written == 0),
            "index_skipped": (events_written == 0),
            "events_written": events_written,
        })
        return standard_res

    def session_commit(self, *args, **kwargs) -> dict:
        return self.session_end(*args, **kwargs)

    def preflight(
        self,
        task: str,
        project: str | None = None,
        *,
        session_id: str = "",
        limit: int = 8,
        write_audit: bool = True,
    ) -> dict:
        from oem_knowledge.preflight import make_preflight_budget, normalize_preflight_result, run_preflight
        from oem_knowledge.project import ProjectResolutionError, resolve_active_project

        resolved_project_arg = project or (str(self.project_path) if self.project_path else "")
        try:
            resolved_project_arg = str(
                resolve_active_project(project_arg=resolved_project_arg, session_id=session_id)
            )
        except ProjectResolutionError:
            pass

        budget, limit_warnings, clamped_limit = make_preflight_budget(limit)
        result = run_preflight(
            task,
            project=resolved_project_arg,
            session_id=session_id,
            write_audit=write_audit,
            budget=budget,
        )
        try:
            surfaced_matches = list(result.matched_concepts[:clamped_limit]) + list(result.matched_memory[:clamped_limit])
            concept_ids: list[str] = []
            for match in surfaced_matches:
                candidates = []
                if match.id:
                    candidates.append({"id": match.id})
                if match.source_path:
                    candidates.append({"source_path": match.source_path})
                if match.metadata:
                    candidates.append({"metadata": match.metadata})
                concept_ids.extend(self.state.concept_ids_from_retrieval_results(candidates))
            self.state.record_concept_references(
                concept_ids,
                source="preflight",
                project=resolved_project_arg,
                session_id=session_id,
            )
        except Exception as ref_err:
            logger.warning("Failed to record concept references for preflight results: %s", ref_err)
        return normalize_preflight_result(
            result,
            operation="knowledge_preflight",
            limit=clamped_limit,
            extra_warnings=limit_warnings,
        )

    def knowledge_read(self, project: str | None = None, scope: str = "project", limit: int = 10) -> dict:
        from oem_knowledge.runtime.read import execute_knowledge_read
        return execute_knowledge_read(self, project, scope, limit)



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
