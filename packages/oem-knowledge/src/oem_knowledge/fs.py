"""
oem_knowledge.fs — Filesystem primitives

Provides FileLock and SecureFileSystem, used by StateService and the engine
for concurrent-safe, path-constrained file I/O.

Services MUST import from here, never from oem_knowledge.engine.
"""
import os
import sys
import time
import json
import uuid
import socket
import logging
import errno
from pathlib import Path

logger = logging.getLogger(__name__)


class LockTimeoutError(TimeoutError):
    """Raised when an OEM file lock cannot be acquired within timeout."""


class StaleLockError(RuntimeError):
    """Raised when an OEM stale lock cannot be recovered safely."""


class FileLock:
    def __init__(
        self,
        lock_path: Path,
        timeout: float = 10.0,
        stale_timeout: float = 300.0,
        poll_interval: float = 0.1,
    ):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.stale_timeout = stale_timeout
        self.poll_interval = poll_interval
        self.owner_id = str(uuid.uuid4())
        self.acquired = False

    def __enter__(self):
        start_time = time.time()
        hostname = socket.gethostname()
        pid = os.getpid()

        while True:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic creation of the lock file
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        metadata = {
                            "pid": pid,
                            "hostname": hostname,
                            "created_at": time.time(),
                            "owner_id": self.owner_id,
                        }
                        json.dump(metadata, f)
                        f.flush()
                        os.fsync(f.fileno())
                except Exception:
                    # Clean up the file descriptor and lock file if write fails
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    if self.lock_path.exists():
                        try:
                            self.lock_path.unlink()
                        except OSError:
                            pass
                    raise
                
                self.acquired = True
                return self
            except FileExistsError:
                # Check if the existing lock is stale and can be recovered
                if self._check_and_recover_stale_lock():
                    continue

                if time.time() - start_time >= self.timeout:
                    logger.warning("Timed out acquiring OEM lock %s after %.2fs", self.lock_path, self.timeout)
                    raise LockTimeoutError(f"Timed out acquiring OEM lock: {self.lock_path}")
                
                time.sleep(self.poll_interval)

    def _check_and_recover_stale_lock(self) -> bool:
        """Inspect the lock file and remove it if it is stale.
        
        Returns:
            True if a stale lock was recovered (deleted), False otherwise.
        """
        try:
            if not self.lock_path.exists():
                return False

            try:
                content = self.lock_path.read_text(encoding="utf-8").strip()
                if not content:
                    raise ValueError("Empty lock file")
                data = json.loads(content)
                lock_pid = data.get("pid")
                lock_host = data.get("hostname")
                created_at = data.get("created_at", 0.0)
            except Exception as e:
                # Metadata is unreadable. Only remove if it is older than stale_timeout
                stat = self.lock_path.stat()
                age = time.time() - stat.st_mtime
                if age > self.stale_timeout:
                    logger.warning(
                        "Removing stale OEM lock %s with unreadable metadata (age=%.2fs): %s",
                        self.lock_path, age, e
                    )
                    try:
                        self.lock_path.unlink(missing_ok=True)
                        return True
                    except OSError:
                        pass
                return False

            # Check if process is running on the same host
            current_host = socket.gethostname()
            if lock_host == current_host:
                if lock_pid:
                    try:
                        os.kill(lock_pid, 0)
                    except OSError as err:
                        if err.errno == errno.ESRCH or isinstance(err, ProcessLookupError):
                            age = time.time() - created_at
                            logger.warning(
                                "Removing stale OEM lock %s held by dead pid=%d age=%.2fs",
                                self.lock_path, lock_pid, age
                            )
                            try:
                                self.lock_path.unlink(missing_ok=True)
                                return True
                            except OSError:
                                pass
                        elif err.errno == errno.EPERM or isinstance(err, PermissionError):
                            # Process exists but we don't have permission to signal it -> process is alive.
                            pass
                        else:
                            # Other OSError -> only remove if age exceeds stale_timeout
                            age = time.time() - created_at
                            if age > self.stale_timeout:
                                logger.warning(
                                    "Removing stale OEM lock %s after unknown signaling error %s (age=%.2fs)",
                                    self.lock_path, err, age
                                )
                                try:
                                    self.lock_path.unlink(missing_ok=True)
                                    return True
                                except OSError:
                                    pass
            else:
                # Process is on another host. Recover lock if older than stale_timeout
                age = time.time() - created_at
                if age > self.stale_timeout:
                    logger.warning(
                        "Removing stale OEM lock %s from different host %s (age=%.2fs)",
                        self.lock_path, lock_host, age
                    )
                    try:
                        self.lock_path.unlink(missing_ok=True)
                        return True
                    except OSError:
                        pass
        except Exception as e:
            logger.debug("Error during stale lock check for %s: %s", self.lock_path, e)
        return False

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            try:
                should_unlink = False
                try:
                    if self.lock_path.exists():
                        content = self.lock_path.read_text(encoding="utf-8").strip()
                        if content:
                            data = json.loads(content)
                            # Only delete if it matches our unique owner_id
                            if data.get("owner_id") == self.owner_id:
                                should_unlink = True
                except Exception:
                    # Fallback to unlink if reading fails but file exists to avoid deadlocking,
                    # but only if we were the ones who acquired it.
                    should_unlink = True

                if should_unlink and self.lock_path.exists():
                    self.lock_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Failed to release OEM lock %s: %s", self.lock_path, e)


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
