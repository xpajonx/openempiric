"""
oem_knowledge.fs — Filesystem primitives

Provides FileLock and SecureFileSystem, used by StateService and the engine
for concurrent-safe, path-constrained file I/O.

Services MUST import from here, never from oem_knowledge.engine.
"""
from __future__ import annotations

import time
from pathlib import Path


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
