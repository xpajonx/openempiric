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

    def count(self) -> int:
        cursor = self.conn.execute("SELECT COUNT(*) FROM chunks")
        return cursor.fetchone()[0]

    def close(self):
        self.conn.close()
