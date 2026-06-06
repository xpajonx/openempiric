from __future__ import annotations
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from oem_knowledge.vector_store import VectorStore

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class SearchService:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine
        self._vector_store = None

    @property
    def model(self):
        return self.engine.model

    @property
    def vector_store(self) -> VectorStore:
        if self._vector_store is None:
            db_dir = self.engine._resolve_harness() / ".local_vector_db"
            db_dir.mkdir(parents=True, exist_ok=True)
            self._migrate_chroma_db_if_needed(db_dir)
            db_file = db_dir / "vectors.db"
            try:
                store = VectorStore(db_file)
                # Test query to ensure the DB is not corrupted
                store.conn.execute("SELECT COUNT(*) FROM chunks")
                self._vector_store = store
            except Exception:
                import logging
                logging.warning(
                    "[OEM] Local SQLite database is corrupted or incompatible. Recreating clean database..."
                )
                try:
                    if db_file.exists():
                        db_file.unlink()
                except Exception:
                    pass
                self._vector_store = VectorStore(db_file)
        return self._vector_store

    def _migrate_chroma_db_if_needed(self, db_dir: Path):
        db_file = db_dir / "vectors.db"
        if db_file.exists():
            return

        # If there are files in db_dir but no vectors.db, it indicates existing ChromaDB data
        if db_dir.exists() and any(db_dir.iterdir()):
            backup_dir = db_dir / "chroma_backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for item in db_dir.iterdir():
                if item == backup_dir or item == db_file:
                    continue
                try:
                    import shutil
                    shutil.move(str(item), str(backup_dir / item.name))
                except Exception:
                    pass

    def get_retrieval_mode(self) -> str:
        config_file = self.engine._resolve_harness() / "config.json"
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
                return data.get("retrieval_mode", "bm25")
            except Exception:
                pass
        return "bm25"

    def set_retrieval_mode(self, mode: str):
        if mode not in ("bm25", "hybrid"):
            raise ValueError("Invalid retrieval mode. Use 'bm25' or 'hybrid'.")
        harness = self.engine._resolve_harness()
        config_file = harness / "config.json"
        data = {}
        if config_file.exists():
            try:
                data = json.loads(config_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        data["retrieval_mode"] = mode
        config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self.model is None:
            raise ImportError(
                "Embedding model is not loaded. Ensure oem[semantic] is installed and "
                "retrieval mode is configured properly."
            )
        return [[float(x) for x in e] for e in self.model.embed(texts)]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def calculate_sha256(self, filepath: Path) -> str:
        return self.engine.calculate_sha256(filepath)

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
                chunks.append(
                    {
                        "title": header.lstrip("#").strip(),
                        "text": f"{header}\n\n{section_body}",
                    }
                )

        if not chunks:
            chunks.append({"title": "Full Document", "text": content.strip()})

        formatted = []
        for idx, chunk in enumerate(chunks):
            section_text = (
                f"Document: {rel_path}\nSection: {chunk['title']}\n\n{chunk['text']}"
            )
            links = list(set(re.findall(r"\[\[([^\]]+)\]\]", chunk["text"])))
            formatted.append(
                {
                    "chunk_id": f"{rel_path}#chunk_{idx}",
                    "text": section_text,
                    "title": chunk["title"],
                    "raw_body": chunk["text"],
                    "linked_concepts": links,
                }
            )

        return formatted

    def derive_importance(self, rel_path: str) -> str:
        lower = rel_path.lower()
        if "agents.md" in lower or "claude.md" in lower:
            return "critical"
        if "scratch" in lower:
            return "low"
        if "/" not in rel_path and "\\" not in rel_path:
            return "high"
        return "medium"

    def index_all(self, force: bool = False, progress_callback=None) -> dict:
        harness = self.engine._resolve_harness()
        wiki_dir = harness / "wiki"
        
        md_files = list(wiki_dir.rglob("*.md")) if wiki_dir.exists() else []
        for f in harness.glob("*.md"):
            if f.is_file():
                md_files.append(f)

        registry = {}
        reg_path = harness / "state" / "file_registry.json"
        if reg_path.exists():
            try:
                registry = json.loads(reg_path.read_text())
            except (json.JSONDecodeError, OSError):
                registry = {}
        stats = {
            "scanned": len(md_files),
            "new": 0,
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
        }
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
            try:
                store = self.vector_store
            except Exception as e:
                import logging
                logging.warning("SQLite DB is unavailable for indexing: %s", e)
                stats["failed"] += len(to_index)
                store = None

            if store is not None:
                retrieval_mode = self.get_retrieval_mode()
                # Verify fastembed is installed for hybrid mode
                if retrieval_mode == "hybrid":
                    try:
                        from fastembed import TextEmbedding
                    except ImportError:
                        raise ImportError(
                            "Hybrid search requires fastembed. Please install it with 'uv tool install oem[semantic]' "
                            "or switch to BM25 search using 'oem config retrieval bm25'."
                        )

                for idx, (fp, path_str, old_hash, cur_hash) in enumerate(to_index):
                    if progress_callback is not None:
                        try:
                            progress_callback(idx + 1, len(to_index))
                        except Exception:
                            pass
                    if old_hash is not None:
                        try:
                            store.delete_by_source(path_str)
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

                    for c in chunks:
                        c_id = c["chunk_id"]
                        text = c["text"]
                        meta = {
                            "source": path_str,
                            "rel_path": rel_path,
                            "title": c["title"],
                            "content_hash": cur_hash,
                            "linked_concepts": ",".join(c["linked_concepts"]),
                            "created_at": str(mtime),
                            "updated_at": str(mtime),
                            "importance": imp,
                        }
                        
                        try:
                            embedding = self.embed([text])[0] if retrieval_mode == "hybrid" else None
                            store.upsert(c_id, text, meta, embedding)
                        except Exception as e:
                            print(f"Index error for {c_id}: {e}", file=sys.stderr)
                            stats["failed"] += 1

        deleted_paths = set(registry.keys()) - active_paths
        if deleted_paths:
            try:
                store = self.vector_store
                if store is not None:
                    for path in deleted_paths:
                        store.delete_by_source(path)
            except Exception:
                pass

        reg_path.parent.mkdir(parents=True, exist_ok=True)
        reg_path.write_text(json.dumps(new_registry, indent=2))
        return stats

    def search(self, query: str, k: int = 3, hybrid: bool = True) -> list[dict]:
        try:
            store = self.vector_store
            if store.count() == 0:
                harness = self.engine._resolve_harness()
                wiki_dir = harness / "wiki"
                has_md_files = (wiki_dir.exists() and any(wiki_dir.glob("*.md"))) or any(harness.glob("*.md"))
                if has_md_files:
                    import logging
                    logging.info("[OEM] Vector database is empty. Auto-indexing existing wiki concepts...")
                    self.index_all(force=True)
                else:
                    return []

            retrieval_mode = self.get_retrieval_mode()
            if retrieval_mode == "hybrid":
                # Ensure fastembed is installed
                try:
                    from fastembed import TextEmbedding
                except ImportError:
                    raise ImportError(
                        "Hybrid search requires fastembed. Please install it with 'uv tool install oem[semantic]' "
                        "or switch to BM25 search using 'oem config retrieval bm25'."
                    )

            chunks = store.all_chunks()
            if not chunks:
                return []

            doc_texts = [c["document"] for c in chunks]
            bm25_scores = self._compute_bm25(query, doc_texts)

            if retrieval_mode == "hybrid":
                query_vec = self.embed([query])[0]
                candidates = []
                for idx, chunk in enumerate(chunks):
                    dense = self.cosine_similarity(query_vec, chunk["embedding"]) if chunk["embedding"] else 0.0
                    sparse = bm25_scores[idx]
                    meta = chunk["metadata"]
                    recency = self._recency_score(meta.get("created_at", str(time.time())))
                    importance = self._importance_score(meta.get("importance", "medium"))
                    
                    final = (
                        (0.45 * dense)
                        + (0.30 * sparse)
                        + (0.15 * recency)
                        + (0.10 * importance)
                    )
                    candidates.append({
                        "id": chunk["id"],
                        "document": chunk["document"],
                        "metadata": meta,
                        "score": final
                    })
            else:
                candidates = []
                for idx, chunk in enumerate(chunks):
                    sparse = bm25_scores[idx]
                    meta = chunk["metadata"]
                    recency = self._recency_score(meta.get("created_at", str(time.time())))
                    importance = self._importance_score(meta.get("importance", "medium"))
                    
                    final = (
                        (0.75 * sparse)
                        + (0.15 * recency)
                        + (0.10 * importance)
                    )
                    candidates.append({
                        "id": chunk["id"],
                        "document": chunk["document"],
                        "metadata": meta,
                        "score": final
                    })

            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates[:k]
        except Exception as e:
            import logging
            logging.warning("Vector database search failed, falling back to registry-only: %s", e)
            return self._search_registry_fallback(query, k)

    def _search_registry_fallback(self, query: str, k: int = 3) -> list[dict]:
        """Fallback keyword search using the concept registry and wiki markdown files directly."""
        try:
            registry = self.engine._load_registry()
        except Exception:
            return []

        query = query.lower().strip()
        query_terms = [t for t in re.findall(r"\w+", query) if len(t) > 1]
        
        candidates = []
        concepts_dir = self.engine._concepts_dir()
        
        for cid, cdata in registry.items():
            canonical = cdata.get("canonical_name", cid).lower()
            aliases = [a.lower() for a in cdata.get("aliases", [])]
            
            # Read wiki content
            wiki_path = concepts_dir / f"{cid}.md"
            wiki_text = ""
            if wiki_path.exists():
                try:
                    wiki_text = wiki_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            
            # Compute score
            score = 0.0
            if canonical == query:
                score = 1.0
            elif query in aliases:
                score = 0.85
            else:
                # Calculate basic term overlap and similarity
                text_to_search = f"{canonical} {' '.join(aliases)} {wiki_text}".lower()
                matched_terms = 0
                for term in query_terms:
                    if term in text_to_search:
                        matched_terms += 1
                if query_terms:
                    score = 0.5 * (matched_terms / len(query_terms))
                
                # Check fuzzy match on canonical name or aliases
                max_sim = 0.0
                for term in [canonical] + aliases:
                    sim = self._string_similarity(query, term)
                    if sim > max_sim:
                        max_sim = sim
                if max_sim >= 0.80:
                    score = max(score, 0.50 + 0.35 * max_sim)

            if score > 0.1 or (not query_terms and not query):
                document = wiki_text if wiki_text else f"Concept: {cdata.get('canonical_name', cid)}\nDescription: {cdata.get('description', '')}"
                
                try:
                    h = self.engine._resolve_harness()
                    rel_path = str(wiki_path.relative_to(h.parent))
                except Exception:
                    rel_path = f".oem/wiki/{cid}.md"

                imp = self.derive_importance(rel_path)
                importance_val = self._importance_score(imp)
                final_score = 0.8 * score + 0.2 * importance_val
                
                candidates.append({
                    "id": f"{cid}#fallback",
                    "document": document,
                    "metadata": {
                        "source": str(wiki_path),
                        "rel_path": rel_path,
                        "title": cdata.get("canonical_name", cid),
                        "content_hash": "",
                        "linked_concepts": "",
                        "importance": imp,
                    },
                    "score": final_score
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:k]

    def _string_similarity(self, s1: str, s2: str) -> float:
        import difflib
        return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

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
                    score += (
                        idf
                        * (freq * (k1 + 1))
                        / (freq + k1 * (1 - b + b * doc_len / max(avg_dl, 1)))
                    )
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
            case "critical":
                return 1.0
            case "high":
                return 0.7
            case "medium":
                return 0.5
            case "low":
                return 0.2
            case _:
                return 0.5

    def stats(self) -> dict:
        try:
            store = self.vector_store
            total_chunks = store.count()
        except Exception:
            total_chunks = 0
        db_size = 0
        db_path = self.engine._resolve_harness() / ".local_vector_db"
        if db_path.exists():
            for f in db_path.rglob("*"):
                if f.is_file():
                    db_size += f.stat().st_size

        return {
            "total_chunks": total_chunks,
            "db_size_mb": db_size / (1024 * 1024),
            "harness_path": str(self.engine._resolve_harness()),
        }
