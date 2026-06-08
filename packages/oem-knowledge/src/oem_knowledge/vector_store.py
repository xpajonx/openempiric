from __future__ import annotations
import json
import sqlite3
from pathlib import Path


class VectorStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
        except Exception:
            pass
        self._ensure_schema()

    def _ensure_schema(self):
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    embedding TEXT
                )
            """)
            # Create an index on source if possible using sqlite json_extract
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(json_extract(metadata, '$.source'))"
                )
            except Exception:
                pass

    def upsert(self, doc_id: str, document: str, metadata: dict, embedding: list[float] | None):
        emb_json = json.dumps(embedding) if embedding is not None else None
        meta_json = json.dumps(metadata)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO chunks (id, document, metadata, embedding) VALUES (?, ?, ?, ?)",
                (doc_id, document, meta_json, emb_json)
            )

    def delete_by_ids(self, doc_ids: list[str]):
        if not doc_ids:
            return
        with self.conn:
            self.conn.executemany("DELETE FROM chunks WHERE id = ?", [(d,) for d in doc_ids])

    def delete_by_source(self, source_path: str):
        try:
            with self.conn:
                self.conn.execute(
                    "DELETE FROM chunks WHERE json_extract(metadata, '$.source') = ?",
                    (source_path,)
                )
        except Exception:
            # Fallback for systems without JSON1 extension
            with self.conn:
                cursor = self.conn.execute("SELECT id, metadata FROM chunks")
                to_delete = []
                for row in cursor:
                    try:
                        meta = json.loads(row["metadata"])
                        if meta.get("source") == source_path:
                            to_delete.append(row["id"])
                    except Exception:
                        pass
                if to_delete:
                    self.delete_by_ids(to_delete)

    def get_by_source(self, source_path: str) -> dict:
        ids = []
        try:
            cursor = self.conn.execute(
                "SELECT id FROM chunks WHERE json_extract(metadata, '$.source') = ?",
                (source_path,)
            )
            ids = [row[0] for row in cursor.fetchall()]
        except Exception:
            # Fallback for systems without JSON1 extension
            cursor = self.conn.execute("SELECT id, metadata FROM chunks")
            for row in cursor:
                try:
                    meta = json.loads(row["metadata"])
                    if meta.get("source") == source_path:
                        ids.append(row["id"])
                except Exception:
                    pass
        return {"ids": ids}

    def count_chunks_by_source(self, source_path: str) -> int:
        try:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE json_extract(metadata, '$.source') = ?",
                (source_path,)
            )
            return cursor.fetchone()[0]
        except Exception:
            # Fallback for systems without JSON1 extension
            try:
                cursor = self.conn.execute("SELECT metadata FROM chunks")
                count = 0
                for row in cursor.fetchall():
                    try:
                        meta = json.loads(row["metadata"])
                        if meta.get("source") == source_path:
                            count += 1
                    except Exception:
                        pass
                return count
            except Exception:
                return 0

    def all_chunks(self) -> list[dict]:
        cursor = self.conn.execute("SELECT id, document, metadata, embedding FROM chunks")
        chunks = []
        for row in cursor.fetchall():
            try:
                embedding = json.loads(row["embedding"]) if row["embedding"] else None
            except Exception:
                embedding = None
            try:
                metadata = json.loads(row["metadata"])
            except Exception:
                metadata = {}
            chunks.append({
                "id": row["id"],
                "document": row["document"],
                "metadata": metadata,
                "embedding": embedding
            })
        return chunks

    def upsert_batch(self, chunks: list[tuple[str, str, dict, list[float] | None]]):
        if not chunks:
            return
        data = []
        for doc_id, document, metadata, embedding in chunks:
            emb_json = json.dumps(embedding) if embedding is not None else None
            meta_json = json.dumps(metadata)
            data.append((doc_id, document, meta_json, emb_json))
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO chunks (id, document, metadata, embedding) VALUES (?, ?, ?, ?)",
                data
            )

    def delete_by_sources(self, source_paths: list[str]):
        if not source_paths:
            return
        try:
            with self.conn:
                self.conn.executemany(
                    "DELETE FROM chunks WHERE json_extract(metadata, '$.source') = ?",
                    [(path,) for path in source_paths]
                )
        except Exception:
            # Fallback for systems without JSON1 extension
            with self.conn:
                cursor = self.conn.execute("SELECT id, metadata FROM chunks")
                to_delete = []
                source_set = set(source_paths)
                for row in cursor:
                    try:
                        meta = json.loads(row["metadata"])
                        if meta.get("source") in source_set:
                            to_delete.append(row["id"])
                    except Exception:
                        pass
                if to_delete:
                    self.conn.executemany("DELETE FROM chunks WHERE id = ?", [(d,) for d in to_delete])

    def count_chunks_by_source_batch(self) -> dict[str, int]:
        counts = {}
        try:
            cursor = self.conn.execute(
                "SELECT json_extract(metadata, '$.source'), COUNT(*) FROM chunks GROUP BY json_extract(metadata, '$.source')"
            )
            for row in cursor.fetchall():
                if row[0]:
                    counts[row[0]] = row[1]
        except Exception:
            # Fallback for systems without JSON1 extension
            try:
                cursor = self.conn.execute("SELECT metadata FROM chunks")
                for row in cursor.fetchall():
                    try:
                        meta = json.loads(row["metadata"])
                        source = meta.get("source")
                        if source:
                            counts[source] = counts.get(source, 0) + 1
                    except Exception:
                        pass
            except Exception:
                pass
        return counts

    def count(self) -> int:
        cursor = self.conn.execute("SELECT COUNT(*) FROM chunks")
        return cursor.fetchone()[0]

    def close(self):
        self.conn.close()
