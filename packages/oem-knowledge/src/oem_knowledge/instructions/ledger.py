from __future__ import annotations
import sqlite3
import json
import logging
import hashlib
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

def get_db_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.Error as e:
        logger.warning("Failed to configure database pragmas: %s", e)
    ensure_schema(conn)
    return conn

def ensure_schema(conn: sqlite3.Connection):
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instruction_sources (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                hash TEXT NOT NULL,
                mtime REAL NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS directives (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                line_start INTEGER NOT NULL,
                line_end INTEGER NOT NULL,
                title TEXT,
                scope TEXT,
                triggers_json TEXT,
                priority TEXT,
                rule TEXT NOT NULL,
                forbidden_actions_json TEXT,
                related_concepts_json TEXT,
                related_skills_json TEXT,
                related_workflows_json TEXT,
                status TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_directive_matches (
                session_id TEXT NOT NULL,
                directive_id TEXT NOT NULL,
                task_hash TEXT NOT NULL,
                match_score REAL NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, directive_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_directive_applications (
                session_id TEXT NOT NULL,
                directive_id TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, directive_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instruction_update_candidates (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                target_path TEXT NOT NULL,
                proposed_patch TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

def index_source_file(conn: sqlite3.Connection, path: str, content: str, file_hash: str, mtime: float, size_bytes: int) -> int:
    from oem_knowledge.instructions.parser import parse_directives
    
    # 1. Parse content
    directives = parse_directives(path, content, file_hash)
    now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # 2. Write transactions
    with conn:
        # Delete old directives for this file
        conn.execute("DELETE FROM directives WHERE source_path = ?", (path,))
        
        # Insert new directives
        for d in directives:
            conn.execute("""
                INSERT OR REPLACE INTO directives (
                    id, source_id, source_path, source_hash, line_start, line_end,
                    title, scope, triggers_json, priority, rule, forbidden_actions_json,
                    related_concepts_json, related_skills_json, related_workflows_json,
                    status, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                d["id"], path, d["source_path"], d["source_hash"], d["line_start"], d["line_end"],
                d["title"], d["scope"], json.dumps(d["triggers"]), d["priority"], d["rule"],
                json.dumps(d["forbidden_actions"]), json.dumps(d["related_concepts"]),
                json.dumps(d["related_skills"]), json.dumps(d["related_workflows"]),
                d["status"], now_str
            ))
            
        # Update instruction_sources
        source_id = "src_" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        conn.execute("""
            INSERT OR REPLACE INTO instruction_sources (
                id, path, hash, mtime, size_bytes, status, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_id, path, file_hash, mtime, size_bytes, "active", now_str))
        
    return len(directives)

def get_active_directives(conn: sqlite3.Connection) -> list[dict]:
    cursor = conn.execute("SELECT * FROM directives WHERE status = 'active'")
    rows = cursor.fetchall()
    results = []
    for r in rows:
        results.append(dict(r))
    return results

def get_stale_sources(conn: sqlite3.Connection, discovered_sources: list[dict]) -> list[str]:
    # Discovered sources list has: [{"path": rel_path, "hash": file_hash}]
    # Query all active database sources
    cursor = conn.execute("SELECT path, hash FROM instruction_sources WHERE status = 'active'")
    db_sources = {r["path"]: r["hash"] for r in cursor.fetchall()}
    
    stale = []
    for ds in discovered_sources:
        path = ds["path"]
        curr_hash = ds["hash"]
        if path not in db_sources or db_sources[path] != curr_hash:
            stale.append(path)
            
    return stale

def detect_conflicting_directives(conn: sqlite3.Connection) -> list[dict]:
    directives = get_active_directives(conn)
    conflicts = []
    
    positive_words = {"must", "always", "required"}
    negative_words = {"never", "do not", "forbidden"}
    
    for i in range(len(directives)):
        d1 = directives[i]
        tr1 = set(json.loads(d1.get("triggers_json") or "[]"))
        rule1_lower = d1["rule"].lower()
        
        is_pos1 = any(pw in rule1_lower for pw in positive_words)
        is_neg1 = any(nw in rule1_lower for nw in negative_words)
        
        for j in range(i + 1, len(directives)):
            d2 = directives[j]
            tr2 = set(json.loads(d2.get("triggers_json") or "[]"))
            rule2_lower = d2["rule"].lower()
            
            is_pos2 = any(pw in rule2_lower for pw in positive_words)
            is_neg2 = any(nw in rule2_lower for nw in negative_words)
            
            # Check overlap on triggers or same scope
            has_trigger_overlap = len(tr1.intersection(tr2)) > 0
            has_same_scope = (d1["scope"] == d2["scope"] and d1["scope"] not in ("general", ""))
            
            if has_trigger_overlap or has_same_scope:
                # Modality conflict: D1 is positive and D2 is negative, or vice-versa
                if (is_pos1 and is_neg2) or (is_neg1 and is_pos2):
                    conflicts.append({
                        "directive_1": d1,
                        "directive_2": d2,
                        "reason": f"Directives have trigger/scope overlap but opposing modalities (positive vs negative rules)."
                    })
                    
    return conflicts
