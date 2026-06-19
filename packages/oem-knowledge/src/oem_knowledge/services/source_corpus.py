from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is available in the runtime today
    yaml = None

try:
    import pathspec
except ImportError:  # pragma: no cover - allow fallback matching in lightweight installs
    pathspec = None

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_CONFIG: dict[str, Any] = {
    "version": 1,
    "chunk_lines": 120,
    "chunk_overlap_lines": 20,
    "max_file_size_bytes": 524288,
    "max_read_lines": 400,
    "max_read_characters": 24000,
    "exclude_globs": [],
}

MANDATORY_EXCLUDED_DIRS = {
    ".git",
    ".oem",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".cache",
}
MANDATORY_EXCLUDED_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".ico",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".jar",
    ".bin",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".pyc",
    ".pyo",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
}
LOCKFILE_NAMES = {
    "uv.lock",
    "poetry.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "Pipfile.lock",
}
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "shell",
    ".ps1": "powershell",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".css": "css",
    ".html": "html",
    ".sql": "sql",
}


def _estimate_tokens(characters: int) -> int:
    if characters <= 0:
        return 0
    return max(1, math.ceil(characters / 4))


def _deep_copy_defaults() -> dict[str, Any]:
    return {
        "version": DEFAULT_SOURCE_CONFIG["version"],
        "chunk_lines": DEFAULT_SOURCE_CONFIG["chunk_lines"],
        "chunk_overlap_lines": DEFAULT_SOURCE_CONFIG["chunk_overlap_lines"],
        "max_file_size_bytes": DEFAULT_SOURCE_CONFIG["max_file_size_bytes"],
        "max_read_lines": DEFAULT_SOURCE_CONFIG["max_read_lines"],
        "max_read_characters": DEFAULT_SOURCE_CONFIG["max_read_characters"],
        "exclude_globs": list(DEFAULT_SOURCE_CONFIG["exclude_globs"]),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_rel_path(path: str | Path) -> str:
    return str(PurePosixPath(str(path).replace("\\", "/")))


def _path_tokens(rel_path: str) -> set[str]:
    pieces = re.split(r"[^A-Za-z0-9_]+", rel_path.lower())
    tokens = {piece for piece in pieces if len(piece) > 1}
    expanded: set[str] = set()
    for token in tokens:
        expanded.add(token)
        expanded.update(part for part in token.split("_") if len(part) > 1)
    return expanded


def _extract_symbols(text: str) -> list[str]:
    symbols = []
    seen = set()
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
        symbol = match.group(0)
        lowered = symbol.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        symbols.append(symbol)
        if len(symbols) >= 128:
            break
    return symbols


def _guess_language(path: Path) -> str:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "text")


def _guess_file_type(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in LOCKFILE_NAMES or name.endswith(".lock"):
        return "lockfile"
    if suffix in {".md", ".rst", ".txt"}:
        return "document"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "config"
    if suffix in {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".css", ".html", ".sql", ".sh", ".ps1"}:
        return "code"
    return "text"


class _IgnoreMatcher:
    def __init__(self, patterns: list[str]):
        self.patterns = self._clean_patterns(patterns)
        self.spec = None
        if pathspec is not None and self.patterns:
            try:
                self.spec = pathspec.PathSpec.from_lines("gitwildmatch", self.patterns)
            except Exception:
                self.spec = None

    def _clean_patterns(self, patterns: list[str]) -> list[str]:
        cleaned = []
        for raw in patterns:
            line = str(raw).strip()
            if not line or line.startswith("#"):
                continue
            cleaned.append(line.replace("\\", "/"))
        return cleaned

    def matches(self, rel_path: str, is_dir: bool = False) -> bool:
        normalized = rel_path.replace("\\", "/")
        candidate = normalized + ("/" if is_dir and not normalized.endswith("/") else "")
        if self.spec is not None:
            return self.spec.match_file(candidate)

        ignored = False
        basename = PurePosixPath(normalized).name
        for pattern in self.patterns:
            negate = pattern.startswith("!")
            pat = pattern[1:] if negate else pattern
            pat = pat.lstrip("/")
            matched = False
            if pat.endswith("/"):
                base = pat.rstrip("/")
                matched = normalized == base or normalized.startswith(base + "/")
            elif "/" not in pat:
                matched = fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(normalized, pat)
            else:
                matched = fnmatch.fnmatch(normalized, pat)
            if matched:
                ignored = not negate
        return ignored


class _SourceIndexStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_chunks (
                    id TEXT PRIMARY KEY,
                    rel_path TEXT NOT NULL,
                    document TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    path_text TEXT NOT NULL,
                    symbols_text TEXT NOT NULL,
                    start_line INTEGER,
                    end_line INTEGER,
                    metadata TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_files (
                    rel_path TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    line_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    metadata TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_chunks_rel_path ON source_chunks(rel_path)"
            )

    def replace_file(self, file_record: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
        rel_path = file_record["rel_path"]
        with self.conn:
            self.conn.execute("DELETE FROM source_chunks WHERE rel_path = ?", (rel_path,))
            self.conn.execute(
                """
                INSERT OR REPLACE INTO source_files (
                    rel_path, status, size_bytes, line_count, chunk_count, content_hash, mtime_ns, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rel_path,
                    file_record["status"],
                    int(file_record["size_bytes"]),
                    int(file_record["line_count"]),
                    int(file_record["chunk_count"]),
                    file_record["content_hash"],
                    int(file_record["mtime_ns"]),
                    json.dumps(file_record["metadata"]),
                ),
            )
            if chunks:
                self.conn.executemany(
                    """
                    INSERT OR REPLACE INTO source_chunks (
                        id, rel_path, document, snippet, path_text, symbols_text, start_line, end_line, metadata, content_hash, mtime_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chunk["id"],
                            rel_path,
                            chunk["document"],
                            chunk["snippet"],
                            chunk["path_text"],
                            chunk["symbols_text"],
                            chunk.get("start_line"),
                            chunk.get("end_line"),
                            json.dumps(chunk["metadata"]),
                            chunk["content_hash"],
                            int(chunk["mtime_ns"]),
                        )
                        for chunk in chunks
                    ],
                )

    def remove_files(self, rel_paths: list[str]) -> None:
        if not rel_paths:
            return
        with self.conn:
            self.conn.executemany("DELETE FROM source_chunks WHERE rel_path = ?", [(item,) for item in rel_paths])
            self.conn.executemany("DELETE FROM source_files WHERE rel_path = ?", [(item,) for item in rel_paths])

    def iter_chunks(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, rel_path, document, snippet, path_text, symbols_text, start_line, end_line, metadata, content_hash, mtime_ns FROM source_chunks"
        ).fetchall()
        results = []
        for row in rows:
            results.append(
                {
                    "id": row["id"],
                    "rel_path": row["rel_path"],
                    "document": row["document"],
                    "snippet": row["snippet"],
                    "path_text": row["path_text"],
                    "symbols_text": row["symbols_text"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "metadata": json.loads(row["metadata"]),
                    "content_hash": row["content_hash"],
                    "mtime_ns": row["mtime_ns"],
                }
            )
        return results

    def close(self) -> None:
        if self.conn is None:
            return
        self.conn.close()
        self.conn = None


class SourceCorpusService:
    def __init__(self, engine: "KnowledgeEngine"):
        self.engine = engine
        self._store: _SourceIndexStore | None = None

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    def _project_root(self) -> Path:
        return Path(self.engine.project_path or ".").resolve()

    def _memory_root(self) -> Path:
        return self._project_root() / ".oem"

    def _layout(self):
        return self.engine.layout(str(self._project_root()))

    def _store_for_write(self) -> _SourceIndexStore:
        if self._store is None:
            db_path = self._layout().source_index_db_path
            self._store = _SourceIndexStore(db_path)
        return self._store

    def _manifest_path(self) -> Path:
        return self._memory_root() / "source_manifest.json"

    def _config_path(self) -> Path:
        return self._memory_root() / "source_index_config.yml"

    def _db_path(self) -> Path:
        return self._memory_root() / "indexes" / "source_index.sqlite"

    def _load_config(self) -> dict[str, Any]:
        config = _deep_copy_defaults()
        config_path = self._config_path()
        if not config_path.exists() or yaml is None:
            return config
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("Failed to read source config %s: %s", config_path, exc)
            return config
        if not isinstance(loaded, dict):
            return config
        for key in (
            "chunk_lines",
            "chunk_overlap_lines",
            "max_file_size_bytes",
            "max_read_lines",
            "max_read_characters",
        ):
            if key in loaded:
                try:
                    config[key] = int(loaded[key])
                except Exception:
                    pass
        if isinstance(loaded.get("exclude_globs"), list):
            config["exclude_globs"] = [str(item) for item in loaded["exclude_globs"] if str(item).strip()]
        return config

    def _ensure_config_written(self, config: dict[str, Any]) -> None:
        config_path = self._config_path()
        if config_path.exists() or yaml is None:
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    def _load_manifest(self) -> dict[str, Any]:
        manifest_path = self._manifest_path()
        if not manifest_path.exists():
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning("Failed to read source manifest %s: %s", manifest_path, exc)
            return {}

    def _read_ignore_file(self, path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except Exception as exc:
            logger.warning("Failed to read ignore file %s: %s", path, exc)
            return []

    def _build_ignore_matcher(self, config: dict[str, Any]) -> _IgnoreMatcher:
        project_root = self._project_root()
        patterns = []
        patterns.extend(self._read_ignore_file(project_root / ".gitignore"))
        patterns.extend(self._read_ignore_file(project_root / ".oemignore"))
        patterns.extend(config.get("exclude_globs", []))
        return _IgnoreMatcher(patterns)

    def _mandatory_exclusion_reason(self, rel_path: str, is_dir: bool) -> str | None:
        pure = PurePosixPath(rel_path)
        parts = pure.parts
        if any(part in MANDATORY_EXCLUDED_DIRS for part in parts):
            return "mandatory_excluded_directory"
        name = pure.name.lower()
        if is_dir:
            return None
        if name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            return "secret_env_file"
        if name.endswith(".log"):
            return "log_file"
        if pure.suffix.lower() in MANDATORY_EXCLUDED_EXTENSIONS:
            return "unsupported_binary_or_media"
        return None

    def _discover_files(self, config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        project_root = self._project_root()
        ignore_matcher = self._build_ignore_matcher(config)
        discovered: list[dict[str, Any]] = []
        counters = {
            "excluded": 0,
            "lockfile_metadata_only": 0,
            "skipped_large_file": 0,
        }
        for current_root, dirnames, filenames in os.walk(project_root):
            current_path = Path(current_root)
            rel_dir = "" if current_path == project_root else _normalize_rel_path(current_path.relative_to(project_root))

            kept_dirs = []
            for dirname in dirnames:
                rel_path = dirname if not rel_dir else f"{rel_dir}/{dirname}"
                if self._mandatory_exclusion_reason(rel_path, is_dir=True) is not None:
                    counters["excluded"] += 1
                    continue
                if ignore_matcher.matches(rel_path, is_dir=True):
                    counters["excluded"] += 1
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs

            for filename in filenames:
                abs_path = current_path / filename
                rel_path = filename if not rel_dir else f"{rel_dir}/{filename}"
                reason = self._mandatory_exclusion_reason(rel_path, is_dir=False)
                if reason is not None:
                    counters["excluded"] += 1
                    continue
                if ignore_matcher.matches(rel_path, is_dir=False):
                    counters["excluded"] += 1
                    continue

                stat = abs_path.stat()
                size_bytes = int(stat.st_size)
                mtime_ns = int(stat.st_mtime_ns)
                content_hash = _sha256_file(abs_path)
                language = _guess_language(abs_path)
                file_type = _guess_file_type(abs_path)
                name = abs_path.name

                mode = "index"
                reason = "eligible"
                if name in LOCKFILE_NAMES or name.endswith(".lock"):
                    mode = "metadata_only"
                    reason = "lockfile_metadata_only"
                    counters["lockfile_metadata_only"] += 1
                elif size_bytes > int(config["max_file_size_bytes"]):
                    mode = "metadata_only"
                    reason = "skipped_large_file"
                    counters["skipped_large_file"] += 1
                elif not self._is_text_like(abs_path):
                    counters["excluded"] += 1
                    continue

                discovered.append(
                    {
                        "absolute_path": abs_path,
                        "rel_path": rel_path,
                        "size_bytes": size_bytes,
                        "mtime_ns": mtime_ns,
                        "content_hash": content_hash,
                        "language": language,
                        "file_type": file_type,
                        "mode": mode,
                        "reason": reason,
                    }
                )
        discovered.sort(key=lambda item: item["rel_path"])
        return discovered, counters

    def _is_text_like(self, path: Path) -> bool:
        try:
            sample = path.read_bytes()[:4096]
        except Exception:
            return False
        if b"\x00" in sample:
            return False
        try:
            sample.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    def _chunk_file(self, file_info: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        abs_path = file_info["absolute_path"]
        rel_path = file_info["rel_path"]
        content = abs_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        line_count = len(lines)
        metadata = {
            "corpus": "source",
            "memory_ingestion_eligible": False,
            "source_retrieval_eligible": True,
            "source": rel_path,
            "rel_path": rel_path,
            "language": file_info["language"],
            "file_type": file_info["file_type"],
            "indexing_reason": file_info["reason"],
        }

        if file_info["mode"] == "metadata_only":
            chunk = {
                "id": f"{rel_path}#metadata",
                "document": f"Path: {rel_path}\nIndexing mode: metadata_only\nReason: {file_info['reason']}",
                "snippet": f"{rel_path} ({file_info['reason']})",
                "path_text": " ".join(sorted(_path_tokens(rel_path))),
                "symbols_text": " ".join(sorted(_path_tokens(rel_path))),
                "start_line": None,
                "end_line": None,
                "metadata": {**metadata, "indexing_mode": "metadata_only"},
                "content_hash": file_info["content_hash"],
                "mtime_ns": file_info["mtime_ns"],
            }
            record = {
                "rel_path": rel_path,
                "status": "metadata_only",
                "size_bytes": file_info["size_bytes"],
                "line_count": line_count,
                "chunk_count": 1,
                "content_hash": file_info["content_hash"],
                "mtime_ns": file_info["mtime_ns"],
                "metadata": {
                    **metadata,
                    "indexing_mode": "metadata_only",
                    "reason": file_info["reason"],
                    "size_bytes": file_info["size_bytes"],
                    "line_count": line_count,
                },
            }
            return record, [chunk], 0

        chunk_lines = max(20, int(config["chunk_lines"]))
        overlap = min(chunk_lines - 1, max(0, int(config["chunk_overlap_lines"])))
        chunks = []
        char_count = 0
        start_index = 0
        while start_index < line_count or (line_count == 0 and start_index == 0):
            end_index = min(line_count, start_index + chunk_lines)
            chunk_body_lines = lines[start_index:end_index]
            if line_count == 0:
                chunk_body_lines = []
            body = "\n".join(chunk_body_lines)
            start_line = start_index + 1
            end_line = end_index if end_index > 0 else 1
            doc = f"Path: {rel_path}\nLines: {start_line}-{end_line}\n\n{body}"
            snippet = body[:500] or f"{rel_path} (empty file)"
            symbols = _extract_symbols(body or rel_path)
            chunk_meta = {
                **metadata,
                "start_line": start_line,
                "end_line": end_line,
                "indexing_mode": "content",
            }
            chunks.append(
                {
                    "id": f"{rel_path}#L{start_line}-{end_line}",
                    "document": doc,
                    "snippet": snippet,
                    "path_text": " ".join(sorted(_path_tokens(rel_path))),
                    "symbols_text": " ".join(symbols),
                    "start_line": start_line,
                    "end_line": end_line,
                    "metadata": chunk_meta,
                    "content_hash": file_info["content_hash"],
                    "mtime_ns": file_info["mtime_ns"],
                }
            )
            char_count += len(body)
            if line_count == 0 or end_index >= line_count:
                break
            start_index = end_index - overlap

        record = {
            "rel_path": rel_path,
            "status": "indexed",
            "size_bytes": file_info["size_bytes"],
            "line_count": line_count,
            "chunk_count": len(chunks),
            "content_hash": file_info["content_hash"],
            "mtime_ns": file_info["mtime_ns"],
            "metadata": {
                **metadata,
                "indexing_mode": "content",
                "reason": file_info["reason"],
                "size_bytes": file_info["size_bytes"],
                "line_count": line_count,
            },
        }
        return record, chunks, char_count

    def index(self, force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        project_root = self._project_root()
        config = self._load_config()
        previous_manifest = self._load_manifest()
        previous_files = previous_manifest.get("files", {}) if isinstance(previous_manifest, dict) else {}
        previous_config = previous_manifest.get("config", {}) if isinstance(previous_manifest, dict) else {}
        if not force and previous_config and previous_config != config:
            force = True

        if not dry_run and not self._memory_root().exists():
            self.engine.init_project(str(project_root))
        if not dry_run:
            self._ensure_config_written(config)

        discovered, counters = self._discover_files(config)
        discovered_map = {item["rel_path"]: item for item in discovered}
        removed_files = sorted(set(previous_files) - set(discovered_map))

        stats = {
            "status": "success",
            "operation": "knowledge_source_index",
            "mode": "dry_run" if dry_run else "write",
            "scanned_files": len(discovered),
            "indexed_files": 0,
            "metadata_only_files": 0,
            "new_files": 0,
            "updated_files": 0,
            "unchanged_files": 0,
            "removed_files": len(removed_files),
            "skipped_large_files": counters["skipped_large_file"],
            "lockfile_metadata_only_files": counters["lockfile_metadata_only"],
            "excluded_files": counters["excluded"],
            "total_chunks": 0,
            "estimated_source_tokens": 0,
        }
        manifest_files: dict[str, Any] = {}
        total_chars = 0

        store = None if dry_run else self._store_for_write()
        for rel_path, file_info in discovered_map.items():
            record, chunks, indexed_chars = self._chunk_file(file_info, config)
            previous = previous_files.get(rel_path)
            is_unchanged = (
                not force
                and isinstance(previous, dict)
                and previous.get("content_hash") == record["content_hash"]
                and int(previous.get("mtime_ns", -1)) == int(record["mtime_ns"])
                and previous.get("status") == record["status"]
                and int(previous.get("chunk_count", -1)) == int(record["chunk_count"])
            )
            if is_unchanged:
                stats["unchanged_files"] += 1
            elif previous is None:
                stats["new_files"] += 1
            else:
                stats["updated_files"] += 1

            if record["status"] == "indexed":
                stats["indexed_files"] += 1
            else:
                stats["metadata_only_files"] += 1

            stats["total_chunks"] += record["chunk_count"]
            total_chars += indexed_chars
            manifest_files[rel_path] = {
                "status": record["status"],
                "content_hash": record["content_hash"],
                "mtime_ns": record["mtime_ns"],
                "chunk_count": record["chunk_count"],
                "size_bytes": record["size_bytes"],
                "line_count": record["line_count"],
                "metadata": record["metadata"],
            }
            if store is not None and not is_unchanged:
                store.replace_file(record, chunks)

        if store is not None and removed_files:
            store.remove_files(removed_files)

        stats["estimated_source_tokens"] = _estimate_tokens(total_chars)
        summary = {
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "project_root": str(project_root),
            "indexed_files": stats["indexed_files"],
            "metadata_only_files": stats["metadata_only_files"],
            "removed_files": stats["removed_files"],
            "skipped_large_files": stats["skipped_large_files"],
            "lockfile_metadata_only_files": stats["lockfile_metadata_only_files"],
            "excluded_files": stats["excluded_files"],
            "total_chunks": stats["total_chunks"],
            "estimated_source_tokens": stats["estimated_source_tokens"],
            "max_file_size_bytes": int(config["max_file_size_bytes"]),
            "max_read_lines": int(config["max_read_lines"]),
            "max_read_characters": int(config["max_read_characters"]),
        }

        if not dry_run:
            manifest = {
                "version": "1.0.1",
                "corpus": "source",
                "config": config,
                "summary": summary,
                "files": manifest_files,
            }
            manifest_path = self._manifest_path()
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        stats["summary"] = summary
        stats["warnings"] = []
        if dry_run:
            stats["warnings"].append("dry_run_no_files_written")
        return stats

    def _trim_content(self, content: str, max_characters: int) -> tuple[str, bool]:
        if len(content) <= max_characters:
            return content, False
        return content[:max_characters], True

    def _resolve_source_path(self, path: str) -> tuple[Path, str]:
        project_root = self._project_root()
        requested = Path(path)
        candidate = requested if requested.is_absolute() else project_root / requested
        verified = self.engine._sfs(project_root).resolve_path(candidate)
        rel_path = _normalize_rel_path(verified.relative_to(project_root))
        return verified, rel_path

    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        project_root = self._project_root()
        config = self._load_config()
        max_lines = max(1, int(config["max_read_lines"]))
        max_chars = max(1000, int(config["max_read_characters"]))
        try:
            verified, rel_path = self._resolve_source_path(path)
        except PermissionError:
            return {
                "status": "error",
                "reason": "path_outside_project",
                "message": "Requested source path is outside the project boundary.",
                "suggestion": "Request a path inside the active project root.",
            }
        if not verified.exists() or not verified.is_file():
            return {
                "status": "error",
                "reason": "source_not_found",
                "message": "Requested source path was not found.",
                "suggestion": "Verify the relative path from the project root.",
            }
        try:
            content = verified.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "status": "error",
                "reason": "source_not_text",
                "message": "Requested source path is not readable as UTF-8 text.",
                "suggestion": "Use a text source path inside the indexed corpus.",
            }
        lines = content.splitlines()
        total_lines = len(lines)
        start = max(1, int(start_line or 1))
        requested_end = int(end_line) if end_line is not None else total_lines
        if requested_end < start:
            return {
                "status": "error",
                "reason": "invalid_line_range",
                "message": "end_line must be greater than or equal to start_line.",
                "suggestion": "Request a valid inclusive line range.",
            }
        capped_end = min(total_lines, requested_end, start + max_lines - 1)
        selected = "\n".join(lines[start - 1 : capped_end])
        trimmed, char_truncated = self._trim_content(selected, max_chars)
        line_truncated = capped_end < requested_end or (end_line is None and capped_end < total_lines)
        warnings = []
        reason = None
        if not self._db_path().exists() or not self._manifest_path().exists():
            warnings.append("source_index_missing")
            reason = "source_index_missing"
        manifest = self._load_manifest()
        file_entry = manifest.get("files", {}).get(rel_path) if manifest else None
        current_hash = _sha256_file(verified)
        if file_entry and file_entry.get("content_hash") != current_hash:
            warnings.append("source_index_stale")
            if reason is None:
                reason = "source_index_stale"
        if line_truncated or char_truncated:
            warnings.append("content_truncated")
            reason = "content_truncated"

        returned_end = start - 1 + len(trimmed.splitlines()) if trimmed else start - 1
        status = "warning" if warnings else "success"
        suggestion = None
        if "content_truncated" in warnings:
            suggestion = "Request a narrower line range."
        elif "source_index_missing" in warnings:
            suggestion = "Run `oem source index` to enable searchable source retrieval."

        return {
            "status": status,
            "reason": reason,
            "path": rel_path,
            "project_root": str(project_root),
            "line_range": {
                "start": start,
                "end": returned_end,
                "requested_end": requested_end,
                "total_lines": total_lines,
            },
            "content": trimmed,
            "warnings": warnings,
            "suggestion": suggestion,
        }

    def _bm25_scores(self, query: str, documents: list[str]) -> list[float]:
        query_terms = [term.lower() for term in re.findall(r"\w+", query) if len(term) > 1]
        if not query_terms or not documents:
            return [0.0] * len(documents)
        tokenized = [[term.lower() for term in re.findall(r"\w+", doc)] for doc in documents]
        doc_count = len(tokenized)
        df: dict[str, int] = {}
        for terms in tokenized:
            for term in set(terms):
                df[term] = df.get(term, 0) + 1
        avg_dl = sum(len(terms) for terms in tokenized) / max(doc_count, 1)
        k1 = 1.5
        b = 0.75
        scores = []
        for terms in tokenized:
            tf: dict[str, int] = {}
            for term in terms:
                tf[term] = tf.get(term, 0) + 1
            score = 0.0
            doc_len = len(terms)
            for query_term in query_terms:
                if query_term not in tf:
                    continue
                freq = tf[query_term]
                n = df.get(query_term, 0)
                idf = math.log((doc_count - n + 0.5) / (n + 0.5) + 1.0)
                score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / max(avg_dl, 1)))
            scores.append(score)
        max_score = max(scores) if scores else 0.0
        return [score / max_score if max_score > 0 else 0.0 for score in scores]

    def search(self, query: str, k: int = 5) -> dict[str, Any]:
        project_root = self._project_root()
        manifest = self._load_manifest()
        if not self._db_path().exists() or not self._manifest_path().exists():
            return {
                "status": "error",
                "reason": "source_index_missing",
                "message": "Source index is missing.",
                "suggestion": "Run `oem source index` before using source search.",
                "results": [],
            }
        store = _SourceIndexStore(self._db_path())
        try:
            rows = store.iter_chunks()
        finally:
            store.close()
        bm25 = self._bm25_scores(query, [row["document"] for row in rows])
        phrase = query.lower()
        query_terms = {term.lower() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query) if len(term) > 1}
        ranked = []
        for idx, row in enumerate(rows):
            rel_path = row["rel_path"]
            metadata = dict(row["metadata"])
            path_tokens = set(row["path_text"].split())
            symbol_tokens = {token.lower() for token in row["symbols_text"].split() if token}
            document_lower = row["document"].lower()
            rel_lower = rel_path.lower()
            path_boost = 0.0
            if phrase and phrase in rel_lower:
                path_boost += 0.75
            path_boost += 0.18 * sum(1 for term in query_terms if term in path_tokens)
            symbol_boost = 0.25 * sum(1 for term in query_terms if term in symbol_tokens)
            phrase_boost = 0.45 if phrase and phrase in document_lower else 0.0
            test_boost = 0.1 if "/tests/" in f"/{rel_lower}" or PurePosixPath(rel_lower).name.startswith("test_") else 0.0
            final_score = bm25[idx] + path_boost + symbol_boost + phrase_boost + test_boost
            file_entry = manifest.get("files", {}).get(rel_path, {}) if manifest else {}
            ranked.append(
                {
                    "id": row["id"],
                    "document": row["snippet"],
                    "metadata": {
                        **metadata,
                        "rel_path": rel_path,
                        "start_line": row["start_line"],
                        "end_line": row["end_line"],
                        "status": file_entry.get("status", metadata.get("indexing_mode", "indexed")),
                    },
                    "score": final_score,
                }
            )
        ranked.sort(key=lambda item: item["score"], reverse=True)
        return {
            "status": "success",
            "results": ranked[: max(1, int(k))],
            "warnings": [],
            "project_root": str(project_root),
        }

    def stats(self) -> dict[str, Any]:
        manifest = self._load_manifest()
        if not manifest:
            return {
                "status": "warning",
                "reason": "source_index_missing",
                "warnings": ["source_index_missing"],
                "summary": {
                    "estimated_source_tokens": 0,
                    "estimated_baseline_tokens": 0,
                    "estimated_retrieval_tokens": 0,
                    "estimated_savings": 0,
                    "indexed_files": 0,
                    "metadata_only_files": 0,
                    "skipped_large_files": 0,
                    "total_chunks": 0,
                },
            }
        summary = dict(manifest.get("summary", {}))
        baseline = int(summary.get("estimated_source_tokens", 0))
        total_chunks = int(summary.get("total_chunks", 0))
        retrieval = min(baseline, max(1, total_chunks) * 250) if baseline else 0
        summary["estimated_baseline_tokens"] = baseline
        summary["estimated_retrieval_tokens"] = retrieval
        summary["estimated_savings"] = max(0, baseline - retrieval)
        db_size_bytes = 0
        db_path = self._db_path()
        if db_path.exists():
            db_size_bytes = int(db_path.stat().st_size)
        summary["db_size_mb"] = round(db_size_bytes / (1024 * 1024), 4)
        return {
            "status": "success",
            "summary": summary,
            "warnings": [],
        }
