from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

from oem_knowledge.models import ConceptData, KnowledgeEvent, ConceptFitness

# Import service classes
from oem_knowledge.services.search import SearchService
from oem_knowledge.services.materialization import MaterializationService
from oem_knowledge.services.reflection import ReflectionService
from oem_knowledge.services.state import StateService
from oem_knowledge.services.event_migration import EventMigrator
from oem_knowledge.services.fitness import FitnessService

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TOKENIZERS_PARALLELISM"] = "false"


class FileLock:
    def __init__(self, lock_path: Path, timeout: float = 10.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self.acquired = False

    def __enter__(self):
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                self.lock_path.touch(exist_ok=False)
                self.acquired = True
                return self
            except FileExistsError:
                time.sleep(0.1)
        raise TimeoutError(f"Could not acquire lock on {self.lock_path}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired and self.lock_path.exists():
            self.lock_path.unlink()


class SecureFileSystem:
    def __init__(self, project_path: Path):
        self.project_path = project_path.resolve()

    def _verify_path(self, path: Path) -> Path:
        resolved = path.resolve()
        try:
            if not resolved.is_relative_to(self.project_path):
                raise PermissionError(
                    f"Security Abort: Path traversal attempted outside project boundary -> {path}"
                )
        except ValueError:
            raise PermissionError(
                f"Security Abort: Path traversal attempted outside project boundary -> {path}"
            )
        return resolved

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        verified = self._verify_path(path)
        if not verified.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return verified.read_text(encoding=encoding)

    def write_text(
        self,
        path: Path,
        content: str,
        encoding: str = "utf-8",
        force_allow_truncation: bool = False,
    ) -> bool:
        verified = self._verify_path(path)
        verified.parent.mkdir(parents=True, exist_ok=True)
        if verified.exists() and not force_allow_truncation:
            old_len = len(verified.read_text(encoding=encoding))
            new_len = len(content)
            if old_len > 10 and new_len < (old_len * 0.5):
                raise ValueError(
                    f"Safety Abort: New content is < 50% of old content. Truncation risk detected for {path}"
                )
        verified.write_text(content.strip() + "\n", encoding=encoding)
        return True

    def append_text(self, path: Path, content: str, encoding: str = "utf-8") -> bool:
        verified = self._verify_path(path)
        verified.parent.mkdir(parents=True, exist_ok=True)
        with open(verified, "a", encoding=encoding) as f:
            f.write(content)
        return True

    def exists(self, path: Path) -> bool:
        try:
            verified = self._verify_path(path)
            return verified.exists()
        except PermissionError:
            return False

    def unlink(self, path: Path):
        verified = self._verify_path(path)
        if verified.exists():
            verified.unlink()


OEM_DIR = ".oem"
DEFAULT_DIRS = [
    "wiki",
    "sessions",
    "state",
    "graph",
    "skills",
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
        self._chroma_client = None
        self._collection = None
        self.project_path = Path(project_path).resolve() if project_path else None
        self._db_dir: Path | None = None

        # Instantiate services with self-injection
        self.search_service = SearchService(self)
        self.materialization_service = MaterializationService(self)
        self.reflection_service = ReflectionService(self)
        self.state_service = StateService(self)
        self.event_migrator = EventMigrator(self)
        self.fitness_service = FitnessService(self)

    def _sfs(self, project: str | Path | None = None) -> SecureFileSystem:
        p = Path(project or self.project_path or ".").resolve()
        return SecureFileSystem(p)

    def _resolve_harness(self, project_path: str | Path | None = None) -> Path:
        p = Path(project_path or self.project_path or ".").resolve()
        root = find_harness_root(p) or p
        migrate_harness_to_oem(root)
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
            from fastembed import TextEmbedding
            try:
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
            except Exception:
                print("\n[OEM] Embedding model 'BAAI/bge-small-en-v1.5' not found in cache.", file=sys.stderr)
                print("[OEM] Downloading model (~67 MB)...", file=sys.stderr)
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=False)
        return self._model

    @property
    def chroma_client(self):
        if self._chroma_client is None:
            import chromadb
            db_path = str(self._resolve_harness() / ".local_vector_db")
            os.makedirs(db_path, exist_ok=True)
            self._chroma_client = chromadb.PersistentClient(path=db_path)
        return self._chroma_client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.chroma_client.get_or_create_collection(
                name="oem_knowledge",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def _registry_path(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "concept_registry.json"

    def _events_path(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "events.jsonl"

    def _sessions_dir(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "sessions"

    def _concepts_dir(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "wiki"

    def _wiki_paths(self, project: str | None = None) -> dict:
        h = self._resolve_harness(project)
        wiki_dir = h / "wiki"
        return {
            "inbox": wiki_dir / "inbox.md",
            "concepts": wiki_dir,
            "variant": "wiki",
        }

    def calculate_sha256(self, filepath: Path) -> str:
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(4096), b""):
                sha.update(block)
        return sha.hexdigest()

    # --- Internal State Management helper methods ---
    # These are kept on the engine for lock coordination or private state-loading,
    # and called by StateService to preserve encapsulation of filesystem tasks.

    def _load_registry_before_extraction(self, project: str | None = None) -> dict:
        p = self._registry_path(project)
        lock_path = p.with_suffix(".lock")
        sfs = self._sfs(project)
        with FileLock(lock_path):
            if sfs.exists(p):
                try:
                    return json.loads(sfs.read_text(p))
                except Exception:
                    return {}
            return {}

    def _save_registry_before_extraction(self, registry: dict, project: str | None = None):
        p = self._registry_path(project)
        lock_path = p.with_suffix(".lock")
        sfs = self._sfs(project)
        with FileLock(lock_path):
            sfs.write_text(p, json.dumps(registry, indent=2))

    def _load_events_before_extraction(self, project: str | None = None) -> list[dict]:
        p = self._events_path(project)
        lock_path = p.with_suffix(".lock")
        sfs = self._sfs(project)
        with FileLock(lock_path):
            if not sfs.exists(p):
                return []
            events = []
            try:
                for line in sfs.read_text(p).splitlines():
                    line = line.strip()
                    if line:
                        ev_dict = json.loads(line)
                        events.append(self.event_migrator.upcast(ev_dict))
            except Exception:
                return []
            return events

    def _append_event_before_extraction(self, event: dict | KnowledgeEvent, project: str | None = None):
        if isinstance(event, dict):
            event = KnowledgeEvent(**event)
        p = self._events_path(project)
        lock_path = p.with_suffix(".lock")
        sfs = self._sfs(project)
        with FileLock(lock_path):
            sfs.append_text(p, event.model_dump_json() + "\n")

    # --- Delegations to services to keep KnowledgeEngine API backwards compatible ---

    # SearchService Delegations
    def chunk_markdown(self, filepath: Path, rel_path: str) -> list[dict]:
        return self.search_service.chunk_markdown(filepath, rel_path)

    def derive_importance(self, rel_path: str) -> str:
        return self.search_service.derive_importance(rel_path)

    def index_all(self, force: bool = False) -> dict:
        return self.search_service.index_all(force=force)

    def search(self, query: str, k: int = 3, hybrid: bool = True) -> list[dict]:
        return self.search_service.search(query, k=k, hybrid=hybrid)

    def stats(self) -> dict:
        return self.search_service.stats()

    # MaterializationService Delegations
    def _sync_index(self, canonical_name: str, concept_id: str, project: str | None = None):
        self.materialization_service._sync_index(canonical_name, concept_id, project)

    def _write_revision_log(self, file_path: Path, new_content: str, project: str | None = None):
        self.materialization_service._write_revision_log(file_path, new_content, project)

    def get_concept_history(self, concept_id: str, project: str | None = None) -> list[dict]:
        return self.materialization_service.get_concept_history(concept_id, project)

    def _safe_write_concept_file(self, file_path: Path, content: str, project: str | None = None) -> bool:
        return self.materialization_service._safe_write_concept_file(file_path, content, project)

    def _log_action(self, message: str, project: str | None = None):
        self.materialization_service._log_action(message, project)

    def materialize_concepts(self, project: str | None = None) -> dict:
        return self.materialization_service.materialize_concepts(project)

    def update_graph(self, project: str | None = None) -> dict:
        return self.materialization_service.update_graph(project)

    # ReflectionService Delegations
    def reflect_session(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
    ) -> dict:
        return self.reflection_service.reflect_session(project, conversation_text, session_id, telemetry, session_started_at)

    # StateService Delegations
    def _load_registry(self, project: str | None = None) -> dict:
        return self.state_service._load_registry(project)

    def _save_registry(self, registry: dict, project: str | None = None):
        self.state_service._save_registry(registry, project)

    def _load_events(self, project: str | None = None) -> list[dict]:
        return self.state_service._load_events(project)

    def _append_event(self, event: dict | KnowledgeEvent, project: str | None = None):
        self.state_service._append_event(event, project)

    def _resolve_concept(self, term: str, registry: dict) -> tuple[str, dict]:
        return self.state_service._resolve_concept(term, registry)

    def evaluate_concept_status(self, cdata: dict, e_type: str, session_id: str, fitness_data: dict | None = None) -> dict:
        return self.state_service.evaluate_concept_status(cdata, e_type, session_id, fitness_data)

    def consolidate(self, project: str | None = None) -> dict:
        return self.state_service.consolidate(project)

    def rebuild_registry(self, project: str | None = None) -> dict:
        return self.state_service.rebuild_registry(project)

    def explain_concept(self, project: str | None = None, concept_id: str = "") -> dict:
        return self.state_service.explain_concept(project, concept_id)

    def merge_concepts(self, project: str | None = None, primary_id: str = "", secondary_id: str = "") -> dict:
        return self.state_service.merge_concepts(project, primary_id, secondary_id)

    def migrate_events(self, project: str | None = None) -> dict:
        return self.event_migrator.migrate_file(project)

    # --- Orchestrator Level Methods kept on engine ---

    def init_project(self, name: str) -> dict:
        path = Path(name)
        if path.is_absolute():
            project_dir = path
        else:
            base = Path.cwd() if not self.project_path else self.project_path
            project_dir = base / name if not (base / name).exists() else base

        migrate_harness_to_oem(project_dir)
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
                "state/workflow_state.json",
                json.dumps(
                    {
                        "current_phase": "PHASE_FIVE",
                        "completed_steps": ["PHASE_ONE", "PHASE_TWO", "PHASE_THREE", "PHASE_FOUR", "PHASE_FIVE"],
                        "history": [],
                    },
                    indent=2,
                ),
            ),
            (
                "state/feature_list.json",
                json.dumps(
                    {
                        "phases": [
                            {
                                "id": "PHASE_ONE",
                                "name": "Knowledge Event Foundation",
                                "completed": True,
                            },
                            {
                                "id": "PHASE_TWO",
                                "name": "Concept Registry & Promotion Engine",
                                "completed": True,
                            },
                            {
                                "id": "PHASE_THREE",
                                "name": "WSL OpenCode Plugin & Declarative YAML Orchestrator",
                                "completed": True,
                            },
                            {
                                "id": "PHASE_FOUR",
                                "name": "Concept Identity, Evolution & Explainability",
                                "completed": True,
                            },
                            {
                                "id": "PHASE_FIVE",
                                "name": "Typed Graphs, Wiki Linter & Safety Guards",
                                "completed": True,
                            },
                        ]
                    },
                    indent=2,
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
        _ = self.model
        print("[OEM] Embedding model ready (cached globally, one-time per machine).", file=sys.stderr)
        return {"status": "success", "model": "BAAI/bge-small-en-v1.5"}

    def warmup_if_needed(self) -> dict:
        """Warm up embedding model if not already cached/loaded."""
        try:
            from fastembed import TextEmbedding
            TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
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
                results = self.search(keywords, k=4)
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
        events = self._load_events(project)
        filtered = []
        for ev in events:
            if concept:
                c_clean = concept.strip().replace(" ", "-").lower()
                if c_clean not in [c.lower() for c in ev.get("concept_candidates", [])]:
                    continue
            if event_type and ev.get("event_type", "").lower() != event_type.lower():
                continue
            if session_id and ev.get("session_id", "") != session_id:
                continue
            filtered.append(ev)
        return filtered

    def get_event(self, project: str | None = None, event_id: str = "") -> dict:
        for ev in self._load_events(project):
            if ev.get("event_id") == event_id:
                return ev
        raise KeyError(f"Event {event_id} not found")

    def session_commit(
        self,
        project: str | None = None,
        conversation_text: str = "",
        session_id: str = "",
        telemetry: dict | None = None,
        session_started_at: float | None = None,
    ) -> dict:
        # Core Orchestration flow using extracted services
        from oem_knowledge.runtime.supervisor import CommitProgressSupervisor
        progress = CommitProgressSupervisor()
        progress.start()

        progress.update_step("transcript", "running")
        progress.update_step("transcript", "success")

        progress.update_step("reflection", "running")
        res = self.reflect_session(
            project, conversation_text, session_id=session_id, telemetry=telemetry, session_started_at=session_started_at
        )
        if res["status"] == "error":
            progress.update_step("reflection", "failed")
            return res
        progress.update_step("reflection", "success")

        progress.update_step("materialization", "running")
        mat_res = self.materialize_concepts(project)
        mat_log = mat_res.get("materialized", [])
        progress.update_step("materialization", "success")

        progress.update_step("index", "running")
        idx_res = {"new": 0, "updated": 0, "scanned": 0, "unchanged": 0, "failed": 0}
        try:
            def index_progress(current, total):
                progress.update_step("index", "running", detail=f"{current} / {total} embeddings")
            idx_res = self.search_service.index_all(progress_callback=index_progress)
        except Exception:
            pass
        progress.update_step("index", "success")

        progress.update_step("vault", "running")
        try:
            from .vault import GlobalVault
            vault = GlobalVault()
            local_reg = self._load_registry(project)
            concepts_dir = self._concepts_dir(project)
            vault.sync_from_registry(local_reg, concepts_dir)
        except Exception:
            pass
        progress.update_step("vault", "success")

        explainability = res.get("explainability", {})
        explainability["materialized"] = len(mat_log)

        return {
            "status": "success",
            "report_path": res["report_path"],
            "knowledge_events": res["knowledge_events"],
            "materialized_log": mat_log,
            "links_updated": self.update_graph(project).get("links_updated", 0),
            "index_stats": idx_res,
            "explainability": explainability,
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
        return self.state_service.record_outcome(
            outcome, referenced_concepts, reason, session_id, project, goal_satisfaction
        )

    def calculate_fitness(self, project: str | None = None) -> dict[str, ConceptFitness]:
        return self.fitness_service.calculate_fitness(project)

    def detect_stale_concepts(self, n_sessions: int = 5, project: str | None = None) -> list[dict]:
        return self.state_service.detect_stale_concepts(n_sessions, project)

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
            from fastembed.common.utils import define_cache_dir
            from pathlib import Path
            cache_dir = Path(define_cache_dir(None))
            
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

