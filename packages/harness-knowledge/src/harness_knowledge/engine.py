from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Literal
import difflib

from pydantic import BaseModel, Field

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

class ConceptData(BaseModel):
    concept_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    status: Literal["candidate", "emerging", "validated", "canonical", "deprecated"] = "candidate"
    confidence: int = Field(default=1, ge=1, le=5)
    evidence_count: int = 0
    sessions: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TOKENIZERS_PARALLELISM"] = "false"

class KnowledgeEvent(BaseModel):
    event_id: str = Field(description="UUID string for event identification")
    timestamp: str = Field(description="ISO 8601 UTC timestamp format")
    project: str = Field(description="Project identifier/directory name")
    session_id: str = Field(description="Source session ID")
    event_type: str = Field(description="Type: hypothesis, experiment, validation, failure, decision, deprecation, observation")
    concept_candidates: list[str] = Field(default_factory=list, description="Associated concepts")
    summary: str = Field(description="Short human-readable summary")
    evidence: str = Field(description="Log snippet or conversation line")
    confidence: int = Field(default=1, ge=1, le=5, description="Confidence rating (1-5)")
    source: str = Field(description="Source of capture: chat, diff, test")
    schema_version: int = Field(default=1)
    
    # Orchestrator Telemetry Integration
    tokens: dict[str, int] | None = Field(default=None, description="Prompt token count details (input/output/total)")
    cost: float | None = Field(default=None, description="API invocation cost in USD")
    duration_s: float | None = Field(default=None, description="Subprocess run duration in seconds")

HARNESS_DIR = ".harness"
DEFAULT_DIRS = [
    "directives",
    "directives/wiki_concepts",
    "execution/core",
    "execution/utils",
    "execution/scratch",
    "state",
    "project",
]


def find_harness_root(path: str | Path) -> Path | None:
    """Walk up from path looking for .harness/ directory."""
    p = Path(path).resolve()
    for parent in [p] + list(p.parents):
        if (parent / HARNESS_DIR).is_dir():
            return parent
    return None


def find_all_projects(base_dir: str | Path | None = None) -> list[Path]:
    """Find all projects with .harness/ directories."""
    if base_dir is None:
        base_dir = Path.home() / "projects"
    base = Path(base_dir)
    if not base.is_dir():
        return []
    return [d for d in base.iterdir() if d.is_dir() and (d / HARNESS_DIR).is_dir()]


class KnowledgeEngine:
    def __init__(self, project_path: str | Path | None = None):
        self._model = None
        self._chroma_client = None
        self._collection = None
        self.project_path = Path(project_path).resolve() if project_path else None
        self._db_dir: Path | None = None

    def _resolve_harness(self, project_path: str | Path | None = None) -> Path:
        p = Path(project_path or self.project_path or ".").resolve()
        harness = p / HARNESS_DIR
        if not harness.exists():
            root = find_harness_root(p)
            if root:
                harness = root / HARNESS_DIR
            else:
                harness.mkdir(parents=True, exist_ok=True)
                self._bootstrap_harness(p)
        return harness

    def _bootstrap_harness(self, project_path: Path):
        """Create a minimal .harness/ structure."""
        harness = project_path / HARNESS_DIR
        for d in DEFAULT_DIRS:
            (harness / d).mkdir(parents=True, exist_ok=True)
        for fname, content in [
            ("AGENTS.md", f"# Harness Framework — {project_path.name}\n\nMUST read at EVERY session.\n"),
            ("CLAUDE.md", "# CLAUDE.md\nRefer to [AGENTS.md](AGENTS.md)\n"),
            ("directives/progress.md", f"# Progress — {project_path.name}\n- **{time.strftime('%Y-%m-%d')}:** Initialized.\n"),
            ("directives/session-handoff.md", "# Session Handoff\n\n## Next Action\nComplete phase one.\n"),
        ]:
            fp = harness / fname
            if not fp.exists():
                fp.write_text(content)

    @property
    def model(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
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
                name="harness_knowledge",
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def _registry_path(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "state" / "concept_registry.json"

    def _events_path(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "state" / "events.jsonl"

    def _sessions_dir(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "directives" / "sessions"

    def _concepts_dir(self, project: str | None = None) -> Path:
        h = self._resolve_harness(project)
        return h / "directives" / "wiki_concepts"

    def _wiki_paths(self, project: str | None = None) -> dict:
        h = self._resolve_harness(project)
        directives = h / "directives"
        design_concepts = directives / "design_wiki_concepts"
        wiki_concepts = directives / "wiki_concepts"

        if design_concepts.exists():
            return {"inbox": directives / "design_wiki.md", "concepts": design_concepts, "variant": "design_wiki"}
        return {"inbox": directives / "wiki_inbox.md", "concepts": wiki_concepts, "variant": "wiki"}

    def calculate_sha256(self, filepath: Path) -> str:
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(4096), b""):
                sha.update(block)
        return sha.hexdigest()

    def chunk_markdown(self, filepath: Path, rel_path: str) -> list[dict]:
        try:
            content = filepath.read_text()
        except Exception:
            return []
        if not content.strip():
            return []

        header_pattern = re.compile(r"^(#{1,4}\s+.*)$", re.MULTILINE)
        parts = header_pattern.split(content)
        chunks = []
        current_title = "Introduction"

        if parts[0].strip():
            chunks.append({"title": current_title, "text": parts[0].strip()})

        for i in range(1, len(parts), 2):
            header = parts[i].strip()
            section_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
            if section_body:
                chunks.append({"title": header.lstrip("#").strip(), "text": f"{header}\n\n{section_body}"})

        if not chunks:
            chunks.append({"title": "Full Document", "text": content.strip()})

        formatted = []
        for idx, chunk in enumerate(chunks):
            section_text = f"Document: {rel_path}\nSection: {chunk['title']}\n\n{chunk['text']}"
            links = list(set(re.findall(r"\[\[([^\]]+)\]\]", chunk["text"])))
            formatted.append({"chunk_id": f"{rel_path}#chunk_{idx}", "text": section_text, "title": chunk["title"], "raw_body": chunk["text"], "linked_concepts": links})

        return formatted

    def derive_importance(self, rel_path: str) -> str:
        lower = rel_path.lower()
        if "agents.md" in lower or "claude.md" in lower:
            return "critical"
        if "scratch" in lower:
            return "low"
        if "directives" in lower and "concepts" not in lower:
            return "high"
        return "medium"

    def index_all(self, force: bool = False) -> dict:
        harness = self._resolve_harness()
        directives = harness / "directives"
        if not directives.exists():
            return {"scanned": 0, "new": 0, "updated": 0, "unchanged": 0, "failed": 0}

        md_files = list(directives.rglob("*.md"))
        registry = {}
        reg_path = harness / "state" / "file_registry.json"
        if reg_path.exists():
            try:
                registry = json.loads(reg_path.read_text())
            except Exception:
                registry = {}

        stats = {"scanned": len(md_files), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}
        active_paths = set()
        new_registry = {}
        to_index = []

        for fp in md_files:
            path_str = str(fp)
            active_paths.add(path_str)
            try:
                cur_hash = self.calculate_sha256(fp)
                old_hash = registry.get(path_str)
                new_registry[path_str] = cur_hash
                if force or old_hash != cur_hash:
                    stats["new" if old_hash is None else "updated"] += 1
                    to_index.append((fp, path_str, old_hash, cur_hash))
                else:
                    stats["unchanged"] += 1
            except Exception:
                stats["failed"] += 1
                if path_str in registry:
                    new_registry[path_str] = registry[path_str]

        if to_index:
            model = self.model
            col = self.collection
            for fp, path_str, old_hash, _ in to_index:
                if old_hash is not None:
                    try:
                        existing = col.get(where={"source": path_str})
                        if existing and existing["ids"]:
                            col.delete(ids=existing["ids"])
                    except Exception:
                        pass

                try:
                    rel_path = str(fp.relative_to(harness.parent))
                except Exception:
                    rel_path = fp.name

                chunks = self.chunk_markdown(fp, rel_path)
                if not chunks:
                    continue

                mtime = os.path.getmtime(fp)
                imp = self.derive_importance(rel_path)

            ids = [c["chunk_id"] for c in chunks]
            texts = [c["text"] for c in chunks]
            metadatas = [{
                "source": path_str,
                "rel_path": rel_path,
                "title": c["title"],
                "content_hash": cur_hash,
                "linked_concepts": ",".join(c["linked_concepts"]),
                "created_at": str(mtime),
                "updated_at": str(mtime),
                "importance": imp,
            } for c in chunks]

            try:
                embeddings = [list(e) for e in model.embed(texts)]
                col.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
            except Exception as e:
                print(f"Index error: {e}", file=sys.stderr)
                stats["failed"] += 1

        deleted_paths = set(registry.keys()) - active_paths
        if deleted_paths:
            try:
                col = self.collection
                for path in deleted_paths:
                    existing = col.get(where={"source": path})
                    if existing and existing["ids"]:
                        col.delete(ids=existing["ids"])
            except Exception:
                pass

        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps(new_registry, indent=2))
        return stats

    def search(self, query: str, k: int = 3, hybrid: bool = True) -> list[dict]:
        col = self.collection
        if col.count() == 0:
            return []

        candidate_count = min(col.count(), max(20, k * 4))
        query_vec = [list(e) for e in self.model.embed([query])][0]

        results = col.query(query_embeddings=[query_vec], n_results=candidate_count)

        if not results or not results["ids"] or not results["ids"][0]:
            return []

        doc_texts = results["documents"][0]
        bm25_scores = self._compute_bm25(query, doc_texts) if hybrid else [0.0] * len(doc_texts)

        formatted = []
        seen_ids = set()
        for i in range(len(results["ids"][0])):
            doc_id = results["ids"][0][i]
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            meta = results["metadatas"][0][i]

            dense = 1 - results["distances"][0][i]
            sparse = bm25_scores[i]
            recency = self._recency_score(meta.get("created_at", str(time.time())))
            importance = self._importance_score(meta.get("importance", "medium"))

            final = (0.45 * dense) + (0.30 * sparse) + (0.15 * recency) + (0.10 * importance)

            formatted.append({
                "id": doc_id,
                "document": results["documents"][0][i],
                "metadata": meta,
                "score": final,
            })

        formatted.sort(key=lambda x: x["score"], reverse=True)
        return formatted[:k]

    def _compute_bm25(self, query: str, documents: list[str]) -> list[float]:
        query_terms = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 1]
        if not query_terms or not documents:
            return [0.0] * len(documents)

        doc_terms = [[t.lower() for t in re.findall(r"\w+", d)] for d in documents]
        doc_count = len(documents)
        df: dict[str, int] = {}
        for terms in doc_terms:
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        k1, b = 1.5, 0.75
        avg_dl = sum(len(t) for t in doc_terms) / max(doc_count, 1)

        scores = []
        for terms in doc_terms:
            score = 0.0
            doc_len = len(terms)
            tf = Counter(terms)
            for q in query_terms:
                if q in tf:
                    n = df.get(q, 0)
                    idf = math.log((doc_count - n + 0.5) / (n + 0.5) + 1.0)
                    freq = tf[q]
                    score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / max(avg_dl, 1)))
            scores.append(score)

        max_s = max(scores) if scores else 0
        return [s / max_s for s in scores] if max_s > 0 else scores

    def _recency_score(self, created_at_str: str) -> float:
        try:
            age_days = (time.time() - float(created_at_str)) / (3600 * 24)
            return math.exp(-0.05 * max(0.0, age_days))
        except Exception:
            return 1.0

    def _importance_score(self, imp: str) -> float:
        match str(imp).lower():
            case "critical": return 1.0
            case "high": return 0.7
            case "medium": return 0.5
            case "low": return 0.2
            case _: return 0.5

    def stats(self) -> dict:
        col = self.collection
        total_chunks = col.count()
        db_size = 0
        db_path = self._resolve_harness() / ".local_vector_db"
        if db_path.exists():
            for f in db_path.rglob("*"):
                if f.is_file():
                    db_size += f.stat().st_size

        return {
            "total_chunks": total_chunks,
            "db_size_mb": db_size / (1024 * 1024),
            "harness_path": str(self._resolve_harness()),
        }

    def init_project(self, name: str) -> dict:
        path = Path(name)
        if path.is_absolute():
            project_dir = path
        else:
            base = Path.cwd() if not self.project_path else self.project_path
            project_dir = base / name if not (base / name).exists() else base
        harness = project_dir / HARNESS_DIR

        created_dirs = []
        for d in DEFAULT_DIRS:
            p = harness / d
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                created_dirs.append(d)

        created_files = []

        for fname, content in [
            ("AGENTS.md", f"# Harness Framework — {name}\n\nMUST read at EVERY session start AND end.\n\n## Lifecycle Status\n| Phase | Status | Description |\n|---|---|---|\n| PHASE_ONE | `[x]` Completed | Knowledge Event Foundation |\n| PHASE_TWO | `[x]` Completed | Concept Registry & Promotion Engine |\n| PHASE_THREE | `[x]` Completed | WSL OpenCode Plugin & Declarative YAML Orchestrator |\n| PHASE_FOUR | `[/]` In Progress | Concept Identity, Evolution & Explainability |\n"),
            ("CLAUDE.md", "# CLAUDE.md\nRefer to [AGENTS.md](AGENTS.md) for workspace lifecycle details.\n"),
            ("directives/wiki_inbox.md", "# Wiki Inbox\n\nAppend raw lessons, API observations, and style guidelines here.\n"),
            ("directives/progress.md", f"# Project Progress Log — {name}\n\n- **{time.strftime('%Y-%m-%d')}:** Harness initialized.\n"),
            ("directives/session-handoff.md", "# Session Handoff\n\n## Next Action\nImplement Phase 4: Concept Identity Resolution, Knowledge Evolution, and Concept Explainability.\n"),
            ("state/workflow_state.json", json.dumps({"current_phase": "PHASE_FOUR", "completed_steps": ["PHASE_ONE", "PHASE_TWO", "PHASE_THREE"], "history": []}, indent=2)),
            ("state/feature_list.json", json.dumps({
                "phases": [
                    {"id": "PHASE_ONE", "name": "Knowledge Event Foundation", "completed": True},
                    {"id": "PHASE_TWO", "name": "Concept Registry & Promotion Engine", "completed": True},
                    {"id": "PHASE_THREE", "name": "WSL OpenCode Plugin & Declarative YAML Orchestrator", "completed": True},
                    {"id": "PHASE_FOUR", "name": "Concept Identity, Evolution & Explainability", "completed": False}
                ]
            }, indent=2)),
        ]:
            fp = harness / fname
            if not fp.exists():
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
                created_files.append(fname)

        return {"status": "success", "message": f"Harness initialized in {project_dir}", "created_directories": created_dirs, "created_files": created_files}

    def restore_session_state(self, project: str | None = None) -> dict:
        h = self._resolve_harness(project)
        directives = h / "directives"
        state = h / "state"

        progress = directives / "progress.md"
        handoff = directives / "session-handoff.md"
        goals = state / "current-goals.md"
        issues = state / "open-issues.md"
        decisions = state / "active-decisions.md"

        active_goals = []
        blockers = []
        discoveries = []
        full_content = ""

        for fp, target_list, attr in [(goals, active_goals, "goals"), (handoff, active_goals, "handoff"), (issues, blockers, "issues"), (decisions, discoveries, "decisions"), (progress, None, "progress")]:
            if fp.exists():
                text = fp.read_text()
                full_content += text + "\n"
                if attr == "handoff":
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

        return {"status": "success", "active_goals": active_goals[:5], "blockers": blockers[:5], "recent_discoveries": discoveries[:5], "recommended_files": rec_files, "query_context": keywords[:60]}

    def _load_registry(self, project: str | None = None) -> dict:
        p = self._registry_path(project)
        lock_path = p.with_suffix(".lock")
        with FileLock(lock_path):
            if p.exists():
                try:
                    return json.loads(p.read_text())
                except Exception:
                    return {}
            return {}

    def _save_registry(self, registry: dict, project: str | None = None):
        p = self._registry_path(project)
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_suffix(".lock")
        with FileLock(lock_path):
            p.write_text(json.dumps(registry, indent=2))

    def _load_events(self, project: str | None = None) -> list[dict]:
        p = self._events_path(project)
        lock_path = p.with_suffix(".lock")
        with FileLock(lock_path):
            if not p.exists():
                return []
            events = []
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            except Exception:
                return []
            return events

    def _append_event(self, event: dict | KnowledgeEvent, project: str | None = None):
        if isinstance(event, dict):
            # Enforce validation
            event = KnowledgeEvent(**event)

        p = self._events_path(project)
        p.parent.mkdir(parents=True, exist_ok=True)
        lock_path = p.with_suffix(".lock")
        with FileLock(lock_path):
            with open(p, "a") as f:
                f.write(event.model_dump_json() + "\n")

    def _resolve_concept(self, term: str, registry: dict) -> tuple[str, dict]:
        term_clean = re.sub(r"[^\w\s-]", "", term).strip().lower()
        if not term_clean:
            term_clean = term.strip().lower()

        for cid, data in registry.items():
            canon = data.get("canonical_name", "").lower()
            aliases = [a.lower() for a in data.get("aliases", [])]
            if term_clean == canon or term_clean in aliases:
                return cid, data

        for cid, data in registry.items():
            canon = data.get("canonical_name", "").lower()
            aliases = [a.lower() for a in data.get("aliases", [])]
            candidates = [canon] + aliases
            for cand in candidates:
                if difflib.SequenceMatcher(None, term_clean, cand).ratio() >= 0.85:
                    if term not in data.get("aliases", []):
                        data.setdefault("aliases", []).append(term)
                    return cid, data

        next_num = len(registry) + 1
        new_id = f"concept_{next_num:03d}"
        canon_name = re.sub(r"[^a-zA-Z0-9\s-]", "", term).strip().replace(" ", "-").lower() or f"concept-{next_num}"
        new_data = ConceptData(
            concept_id=new_id,
            canonical_name=canon_name,
            aliases=[term]
        ).model_dump()
        registry[new_id] = new_data
        return new_id, new_data

    def reflect_session(self, project: str | None = None, conversation_text: str = "", session_id: str = "", telemetry: dict | None = None) -> dict:
        if not session_id:
            session_id = f"session_{time.strftime('%Y%m%d_%H%M%S')}"

        knowledge_events = []
        harness = self._resolve_harness(project)
        concepts_dir = self._concepts_dir(project)

        modified_files = list(concepts_dir.rglob("*.md")) if concepts_dir.exists() else []
        for fp in modified_files:
            concept = fp.stem.replace("_", " ").replace("-", " ").title()
            knowledge_events.append({"type": "observation", "concept": concept, "evidence": f"Modified: {fp.name}", "confidence": 1, "source": "diff"})

        text_clean = conversation_text.strip()
        if telemetry:
            duration = telemetry.get("duration_sec", 0)
            tool_calls = telemetry.get("total_tool_calls", 0)
            knowledge_events.append({
                "type": "telemetry",
                "concept": "Session Metrics",
                "evidence": f"Session duration: {duration}s, Tool calls: {tool_calls}",
                "confidence": 1,
                "source": "orchestrator"
            })
        if text_clean.startswith("{"):
            try:
                data = json.loads(text_clean)
                if "knowledge_events" in data:
                    knowledge_events.extend(data["knowledge_events"])
            except Exception:
                pass
        else:
            for line in conversation_text.splitlines():
                lower = line.strip().lower()
                if lower.startswith("hypothesis:") or lower.startswith("hyp:"):
                    knowledge_events.append({"type": "hypothesis", "concept": lower.split(":", 1)[1].strip()[:80], "evidence": line.strip(), "confidence": 1, "source": "chat"})
                elif lower.startswith("experiment:") or lower.startswith("exp:"):
                    knowledge_events.append({"type": "experiment", "concept": lower.split(":", 1)[1].strip()[:80], "evidence": line.strip(), "confidence": 1, "source": "chat"})
                elif lower.startswith("validation:") or lower.startswith("val:"):
                    knowledge_events.append({"type": "validation", "concept": lower.split(":", 1)[1].strip()[:80], "evidence": line.strip(), "confidence": 1, "source": "chat"})
                elif lower.startswith("failure:") or lower.startswith("fail:"):
                    knowledge_events.append({"type": "failure", "concept": lower.split(":", 1)[1].strip()[:80], "evidence": line.strip(), "confidence": 1, "source": "chat"})
                elif lower.startswith("decision:") or lower.startswith("dec:"):
                    knowledge_events.append({"type": "decision", "concept": lower.split(":", 1)[1].strip()[:80], "evidence": line.strip(), "confidence": 1, "source": "chat"})

        seen = set()
        canonical_events = []
        for ev in knowledge_events:
            e_type = ev.get("type", "observation")
            concept_str = ev.get("concept", "General Learning")
            evidence = ev.get("evidence", "")
            key = (e_type, concept_str, evidence.lower())
            if key in seen:
                continue
            seen.add(key)

            event_id = str(uuid.uuid4())
            canonical_event = {
                "event_id": event_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "project": project or "default",
                "session_id": session_id,
                "event_type": e_type,
                "concept_candidates": [concept_str],
                "summary": f"{e_type.title()}: {concept_str}",
                "evidence": evidence,
                "confidence": ev.get("confidence", 1),
                "source": ev.get("source", "chat"),
                "schema_version": 1,
            }
            print("DEBUG CANONICAL EVENT:", canonical_event)
            canonical_events.append(canonical_event)
            self._append_event(canonical_event, project)

        sessions_dir = self._sessions_dir(project)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        date_str = time.strftime("%Y-%m-%d")
        report_file = sessions_dir / f"{date_str}.md"
        if report_file.exists():
            report_file = sessions_dir / f"{date_str}_{int(time.time() % 100000)}.md"

        yaml_events = [{"type": e["event_type"], "concept": e["concept_candidates"][0], "evidence": e["evidence"], "confidence": e["confidence"]} for e in canonical_events]
        yaml_content = json.dumps({"knowledge_events": yaml_events}, indent=2)

        report = f"""---
date: {date_str}
project: {project or 'default'}
---
# Session Learning Report — {date_str}

## Knowledge Events
```json
{yaml_content}
```
"""
        report_file.write_text(report)

        return {"status": "success", "report_path": str(report_file), "knowledge_events": yaml_events, "canonical_events": canonical_events}

    def evaluate_concept_status(self, cdata: dict, e_type: str, session_id: str) -> dict:
        """Evaluate and promote concept status based on session and evidence counts."""
        confidence = cdata.get("confidence", 1)
        
        if session_id not in cdata.setdefault("sessions", []):
            cdata["sessions"].append(session_id)
            cdata["session_count"] = len(cdata["sessions"])
        
        if e_type == "validation":
            confidence = min(5, confidence + 1)
        elif e_type == "failure":
            confidence = max(1, confidence - 1)
        cdata["confidence"] = confidence

        current_status = cdata.get("status", "candidate")

        if e_type == "deprecation":
            new_status = "deprecated"
        elif cdata.get("session_count", 0) >= 5 and cdata["confidence"] >= 4:
            new_status = "canonical"
        elif cdata.get("evidence_count", 0) >= 3 or current_status == "validated":
            new_status = "validated"
        elif cdata.get("session_count", 0) >= 2:
            new_status = "emerging"
        else:
            new_status = "candidate"

        cdata["status"] = new_status
        return cdata

    def materialize_concepts(self, project: str | None = None) -> dict:
        harness = self._resolve_harness(project)
        sessions_dir = self._sessions_dir(project)
        if not sessions_dir.exists():
            return {"status": "success", "message": "No session reports found.", "materialized": []}

        concepts_dir = self._concepts_dir(project)
        concepts_dir.mkdir(parents=True, exist_ok=True)

        session_files = sorted(sessions_dir.glob("*.md"))
        if not session_files:
            return {"status": "success", "message": "No session reports found.", "materialized": []}

        latest = session_files[-1]
        content = latest.read_text()

        knowledge_events = []
        json_match = re.search(r"```json\n(.*?)\n```", content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if "knowledge_events" in data:
                    knowledge_events = data["knowledge_events"]
            except Exception:
                pass

        if not knowledge_events:
            return {"status": "success", "message": "No knowledge events found.", "materialized": []}

        registry = self._load_registry(project)
        materialized_log = []

        for event in knowledge_events:
            concept = event.get("concept", "General Learning")
            e_type = event.get("type", "observation").lower()
            evidence = event.get("evidence", "")

            cid, cdata = self._resolve_concept(concept, registry)

            if evidence:
                cdata["evidence_count"] = cdata.get("evidence_count", 0) + 1

            cdata = self.evaluate_concept_status(cdata, e_type, session_id=latest.stem)
            new_status = cdata["status"]
            registry[cid] = cdata

            concept_file = concepts_dir / f"{cid}.md"

            if new_status in ("validated", "canonical"):
                existing_body = ""
                is_new = not concept_file.exists()
                if not is_new:
                    try:
                        text = concept_file.read_text()
                        fm = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", text, re.DOTALL)
                        existing_body = fm.group(1).strip() if fm else text.strip()
                    except Exception:
                        pass

                learning = f"- **{e_type.title()}**: {evidence}" if evidence else ""
                if is_new:
                    body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept is a validated organizational knowledge node.\n\n## Learnings\n{learning}\n"
                else:
                    body = existing_body
                    if learning:
                        body += f"\n\n## Learnings ({time.strftime('%Y-%m-%d')})\n{learning}\n"

                concept_content = f"""---
concept_id: {cid}
canonical_name: {cdata['canonical_name']}
status: {new_status}
confidence: {cdata['confidence']}
evidence_count: {cdata['evidence_count']}
session_count: {cdata['session_count']}
aliases: {json.dumps(cdata.get('aliases', []))}
---
{body}"""

                concept_file.write_text(concept_content.strip() + "\n")
                materialized_log.append(f"{cid} ({cdata['canonical_name']}) = {new_status}")

            elif new_status == "deprecated":
                if concept_file.exists():
                    concept_file.unlink()
                materialized_log.append(f"Deprecated: {cid}")

            else:
                materialized_log.append(f"{cid} ({cdata['canonical_name']}) = {new_status} (not materialized)")

        self._save_registry(registry, project)
        return {"status": "success", "materialized": materialized_log}

    def update_graph(self, project: str | None = None) -> dict:
        concepts_dir = self._concepts_dir(project)
        if not concepts_dir.exists():
            return {"status": "success", "message": "No concepts directory.", "links_updated": 0}

        concept_files = list(concepts_dir.glob("concept_*.md"))
        if not concept_files:
            return {"status": "success", "message": "No concept files found.", "links_updated": 0}

        registry = self._load_registry(project)
        concepts = {}
        for f in concept_files:
            cid = f.stem
            if cid not in registry:
                continue
            text = f.read_text()
            links = set(re.findall(r"\[\[(concept_\d+)(?:\|[^\]]*)?\]\]", text))
            concepts[cid] = {"path": f, "name": registry[cid]["canonical_name"].replace("-", " ").title(), "text": text, "links": links}

        connections = {cid: set(data["links"]) for cid, data in concepts.items()}

        for cid_a, data_a in concepts.items():
            fm = re.match(r"^---\s*\n.*?\n---\s*\n(.*)$", data_a["text"], re.DOTALL)
            body = fm.group(1).lower() if fm else data_a["text"].lower()
            for cid_b, data_b in concepts.items():
                if cid_a == cid_b:
                    continue
                bdata = registry[cid_b]
                canon = bdata["canonical_name"].lower()
                if re.search(rf"\b{re.escape(cid_b)}\b", body) or re.search(rf"\b{re.escape(canon)}\b", body) or re.search(rf"\b{re.escape(canon.replace('-', ' '))}\b", body):
                    connections[cid_a].add(cid_b)
                else:
                    for alias in bdata.get("aliases", []):
                        if re.search(rf"\b{re.escape(alias.lower())}\b", body):
                            connections[cid_a].add(cid_b)
                            break

        reciprocal = {c: set(conn) for c, conn in connections.items()}
        for cid_a, conns in connections.items():
            for cid_b in conns:
                if cid_b in reciprocal:
                    reciprocal[cid_b].add(cid_a)

        links_added = 0
        for cid, data in concepts.items():
            fp = data["path"]
            text = data["text"]
            targets = {l for l in reciprocal[cid] if l in concepts and l != cid}
            if not targets:
                continue

            fm = re.match(r"^(---\s*\n.*?\n---\s*\n)(.*)$", text, re.DOTALL)
            if not fm:
                continue

            header = fm.group(1)
            body = re.split(r"\n##\s+Related", fm.group(2), flags=re.IGNORECASE)[0].strip()

            related_lines = []
            for tc in sorted(targets):
                tname = registry[tc]["canonical_name"].replace("-", " ").title()
                related_lines.append(f"- [[{tc}|{tname}]] — {tname}")

            new_text = header + body + "\n\n## Related Knowledge\n" + "\n".join(related_lines) + "\n"
            if new_text != text:
                fp.write_text(new_text)
                links_added += len(targets) - len(data["links"])

        return {"status": "success", "links_updated": links_added, "files_scanned": len(concept_files)}

    def get_events(self, project: str | None = None, concept: str = "", event_type: str = "", session_id: str = "") -> list[dict]:
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

    def session_commit(self, project: str | None = None, conversation_text: str = "", session_id: str = "", telemetry: dict | None = None) -> dict:
        res = self.reflect_session(project, conversation_text, session_id=session_id, telemetry=telemetry)
        if res["status"] == "error":
            return res

        mat_res = self.materialize_concepts(project)
        mat_log = mat_res.get("materialized", [])

        graph_res = self.update_graph(project)

        idx_res = {"new": 0, "updated": 0, "scanned": 0, "unchanged": 0, "failed": 0}
        try:
            idx_res = self.index_all()
        except Exception:
            pass

        return {
            "status": "success",
            "report_path": res["report_path"],
            "knowledge_events": res["knowledge_events"],
            "materialized_log": mat_log,
            "links_updated": graph_res.get("links_updated", 0),
            "index_stats": idx_res,
        }

    def consolidate(self, project: str | None = None) -> dict:
        concepts_dir = self._concepts_dir(project)
        if not concepts_dir.exists():
            return {"status": "error", "message": "No concepts directory found."}

        md_files = list(concepts_dir.rglob("*.md"))
        if len(md_files) < 2:
            return {"status": "success", "message": "Fewer than 2 files. No consolidation needed.", "merged": []}

        contents = {}
        for f in md_files:
            try:
                contents[f] = f.read_text()
            except Exception:
                pass

        merged = []
        already_merged = set()
        for i in range(len(md_files)):
            f1 = md_files[i]
            if f1 in already_merged or f1 not in contents:
                continue
            for j in range(i + 1, len(md_files)):
                f2 = md_files[j]
                if f2 in already_merged or f2 not in contents:
                    continue

                w1 = set(re.findall(r"\w+", f1.stem.lower()))
                w2 = set(re.findall(r"\w+", f2.stem.lower()))
                common = w1 & w2
                if common and len(common) >= min(len(w1), len(w2)):
                    if len(f1.name) > len(f2.name):
                        f1, f2 = f2, f1
                    merged_content = f"{contents[f1].strip()}\n\n## Consolidated: {f2.stem.replace('_', ' ').title()}\n{contents[f2].strip()}"
                    f1.write_text(merged_content)
                    f2.unlink()
                    already_merged.add(f2)
                    contents[f1] = merged_content
                    merged.append(f"Merged {f2.name} -> {f1.name}")

        if merged:
            self.index_all(force=True)

        return {"status": "success", "message": f"Consolidated {len(merged)} files.", "merged": merged}

    def rebuild_registry(self, project: str | None = None) -> dict:
        """Rebuild concept registry and materialized files from the immutable events.jsonl log."""
        harness = self._resolve_harness(project)
        registry_path = self._registry_path(project)
        events_path = self._events_path(project)
        concepts_dir = self._concepts_dir(project)

        # 1. Clean Slate
        temp_registry = {}
        if concepts_dir.exists():
            for f in concepts_dir.glob("concept_*.md"):
                try:
                    f.unlink()
                except Exception:
                    pass

        # 2. Sequential Processing
        events = self._load_events(project)
        for event in events:
            concept_candidates = event.get("concept_candidates", [])
            primary_term = concept_candidates[0] if concept_candidates else event.get("concept", "General")
            
            cid, cdata = self._resolve_concept(primary_term, temp_registry)
            
            if event.get("evidence"):
                cdata["evidence_count"] = cdata.get("evidence_count", 0) + 1
                
            cdata = self.evaluate_concept_status(
                cdata=cdata,
                e_type=event.get("event_type", "observation"),
                session_id=event.get("session_id", "historical")
            )
            temp_registry[cid] = cdata

        # 3. Save snapshot
        self._save_registry(temp_registry, project)

        # 4. Re-materialize
        materialized_log = []
        concepts_dir.mkdir(parents=True, exist_ok=True)
        for cid, cdata in temp_registry.items():
            if cdata["status"] in ("validated", "canonical"):
                concept_file = concepts_dir / f"{cid}.md"
                body = f"# {cdata['canonical_name'].replace('-', ' ').title()}\n\nThis concept is a validated organizational knowledge node.\n\n## Learnings\n"
                
                concept_content = f"---\nconcept_id: {cid}\ncanonical_name: {cdata['canonical_name']}\nstatus: {cdata['status']}\nconfidence: {cdata['confidence']}\nevidence_count: {cdata['evidence_count']}\nsession_count: {cdata.get('session_count', 0)}\naliases: {json.dumps(cdata.get('aliases', []))}\n---\n{body}"
                concept_file.write_text(concept_content)
                materialized_log.append(cid)
        
        # 5. Re-index
        self.index_all(force=True)

        return {"status": "success", "message": "Registry rebuilt from events log.", "materialized": len(materialized_log)}

    def explain_concept(self, project: str | None = None, concept_id: str = "") -> dict:
        registry = self._load_registry(project)
        if concept_id not in registry:
            return {"status": "error", "message": f"Concept {concept_id} not found."}
        
        cdata = registry[concept_id]
        events = self.get_events(project, concept=cdata["canonical_name"])
        
        summary = {
            "concept": cdata,
            "total_events": len(events),
            "recent_evidence": [e.get("evidence") for e in events[-5:] if e.get("evidence")]
        }
        return {"status": "success", "explanation": summary}

    def merge_concepts(self, project: str | None = None, primary_id: str = "", secondary_id: str = "") -> dict:
        registry = self._load_registry(project)
        if primary_id not in registry or secondary_id not in registry:
            return {"status": "error", "message": "One or both concepts not found."}
        
        pdata = registry[primary_id]
        sdata = registry[secondary_id]
        
        new_aliases = set(pdata.get("aliases", []) + sdata.get("aliases", []) + [sdata.get("canonical_name")])
        pdata["aliases"] = list(new_aliases)
        
        pdata["evidence_count"] = pdata.get("evidence_count", 0) + sdata.get("evidence_count", 0)
        pdata["sessions"] = list(set(pdata.get("sessions", []) + sdata.get("sessions", [])))
        pdata["session_count"] = len(pdata["sessions"])
        
        del registry[secondary_id]
        self._save_registry(registry, project)
        
        pdata = self.evaluate_concept_status(pdata, "merge", "system")
        registry[primary_id] = pdata
        self._save_registry(registry, project)
        
        concepts_dir = self._concepts_dir(project)
        sf = concepts_dir / f"{secondary_id}.md"
        if sf.exists():
            sf.unlink()
            
        return {"status": "success", "message": f"Merged {secondary_id} into {primary_id}", "concept": pdata}
