"""Commit pipeline with write-ahead intent logging for session-end operations.

Provides crash recovery: if a commit fails mid-pipeline, the intent log
records which phase was in progress so the next session can resume or rollback.
"""

import json
import os
import time
from pathlib import Path
from typing import Any


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def prefix_checksum(file_path: Path, offset: int) -> str | None:
    """SHA-256 of the first `offset` bytes of a file, or None when unreadable."""
    try:
        with open(file_path, "rb") as f:
            return _sha256_bytes(f.read(offset))
    except OSError:
        return None


class CommitRollbackError(Exception):
    """Raised when a pipeline phase fails, triggering rollback."""
    def __init__(self, phase: str, original_error: Exception):
        self.phase = phase
        self.original_error = original_error
        super().__init__(f"Pipeline phase '{phase}' failed: {original_error}")


class StagingArea:
    """Manages staged writes with atomic commit and rollback.
    
    For append-only files like events.jsonl, this tracks byte offsets
    rather than using atomic renames.
    """
    
    def __init__(self, staging_dir: Path):
        self.staging_dir = staging_dir
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._phase_results: dict[str, Any] = {}
        self._byte_offsets: dict[str, int] = {}
    
    def commit_phase(self, phase: str, result: Any) -> None:
        self._phase_results[phase] = result
    
    def record_byte_offset(self, phase: str, file_path: Path) -> None:
        """Record current byte length of an append-only file before writing."""
        if file_path.exists():
            self._byte_offsets[phase] = file_path.stat().st_size
        else:
            self._byte_offsets[phase] = 0
    
    def get_byte_offset(self, phase: str) -> int | None:
        return self._byte_offsets.get(phase)
    
    def rollback_to(self, failed_phase: str) -> None:
        """Remove staged results for the failed phase and any subsequent phases.
        
        For append-only files, the caller must manually truncate to recorded offsets.
        """
        phases_order = [
            "reflect", "validate", "append_events", "write_report",
            "materialize", "index", "cleanup", "dream"
        ]
        if failed_phase not in phases_order:
            return
        idx = phases_order.index(failed_phase)
        for phase in phases_order[idx:]:
            self._phase_results.pop(phase, None)
    
    def get_results(self) -> dict[str, Any]:
        return dict(self._phase_results)


class CommitIntentLog:
    """Write-ahead log recording which pipeline phase is currently active.
    
    Survives crashes: on next pipeline start, any uncompleted intent
    can be detected and the pipeline resumed or rolled back.
    """
    
    def __init__(self, intent_path: Path):
        self.intent_path = intent_path
    
    def write_intent(self, phase: str, **extra: Any) -> None:
        self.intent_path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read_intent() or {}
        intent = {
            "intent_id": existing.get("intent_id"),
            "phase": phase,
            "timestamp": time.time(),
            **extra,
        }
        if not intent["intent_id"]:
            import uuid
            intent["intent_id"] = uuid.uuid4().hex[:12]
        import uuid
        tmp_path = self.intent_path.with_name(f"intent.{uuid.uuid4().hex}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(intent, f)
        os.replace(tmp_path, self.intent_path)
    
    def read_intent(self) -> dict | None:
        if not self.intent_path.exists():
            return None
        try:
            with open(self.intent_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    
    def clear_intent(self) -> None:
        if self.intent_path.exists():
            self.intent_path.unlink()

    def quarantine(self, reason: str) -> Path | None:
        """Rename the intent file to a quarantine name; returns the new path."""
        if not self.intent_path.exists():
            return None
        ts = time.strftime("%Y%m%d-%H%M%S")
        quarantine_path = self.intent_path.with_name(f"intent.quarantine-{ts}.json")
        try:
            os.replace(self.intent_path, quarantine_path)
            return quarantine_path
        except OSError:
            return None

    def has_uncompleted_intent(self) -> bool:
        intent = self.read_intent()
        if intent is None:
            return False
        return intent.get("phase") != "complete"


class CommitPipeline:
    """Orchestrates session-end commit as a sequence of phases with crash recovery.
    
    Usage:
        pipeline = CommitPipeline(oem_dir)
        
        # Check for crash recovery
        if pipeline.intent_log.has_uncompleted_intent():
            pipeline.recover_from_crash(events_path)
        
        # Run phases
        pipeline.run_phase("append_events", staging, events_path)
        pipeline.run_phase("index", staging)
        # ... etc
    """
    
    phases = ["reflect", "validate", "append_events", "write_report", 
              "materialize", "index", "cleanup", "dream"]
    
    def __init__(self, oem_dir: Path):
        self.oem_dir = oem_dir
        self.staging_dir = oem_dir / ".staging"
        self.intent_log = CommitIntentLog(self.staging_dir / "intent.json")
        self.staging = StagingArea(self.staging_dir)
        self.timings: dict[str, float] = {}
    
    def start_phase(self, phase: str) -> None:
        """Record intent before executing a phase."""
        if phase not in self.phases:
            raise ValueError(f"Unknown phase: {phase}")
        self.intent_log.write_intent(phase)
    
    def complete_phase(self, phase: str, result: Any = None) -> None:
        """Mark a phase as complete and record its result."""
        self.staging.commit_phase(phase, result)
    
    def record_appended_bytes(self, events_path: Path) -> None:
        """Record byte offset before appending to events.jsonl."""
        self.staging.record_byte_offset("append_events", events_path)
    
    def rollback_events(self, events_path: Path, offset: int | None = None) -> None:
        """Truncate events.jsonl to pre-append byte offset on failure."""
        if offset is None:
            offset = self.staging.get_byte_offset("append_events")
        if offset is not None and events_path.exists():
            with open(events_path, "r+b") as f:
                f.truncate(offset)
    
    def complete_pipeline(self) -> None:
        """Mark the entire pipeline as complete."""
        self.intent_log.write_intent("complete")
        self.intent_log.clear_intent()
    
    def recover_from_crash(self, events_path: Path | None = None) -> dict:
        """Check for an uncompleted intent and attempt bounded recovery.

        Rules:
        - No intent file -> noop.
        - Malformed intent file -> quarantined (malformed_intent).
        - phase 'complete' -> cleared (cleared_stale).
        - phase 'append_events': compare events file size against the recorded
          byte offset plus expected_bytes. Size == offset -> no_partial_write.
          Size == offset + expected_bytes -> events_committed (keep events).
          Any other size: verify the prefix checksum; match -> roll back by
          truncation; mismatch -> quarantine events + intent.
        - phase 'failed' -> quarantined (failed_intent).
        - other phases -> cleared (cleared_intent_will_retry).

        Returns {"recovered": bool, "action": str, "last_phase": str, "quarantine_path": str | None, "warning": str | None}.
        """
        intent = self.intent_log.read_intent()
        if intent is None:
            if self.intent_log.intent_path.exists():
                qpath = self.intent_log.quarantine("malformed_intent")
                return {
                    "recovered": False, "action": "quarantined_malformed_intent",
                    "last_phase": "unknown", "quarantine_path": str(qpath) if qpath else None,
                    "warning": "Malformed commit intent quarantined.",
                }
            return {"recovered": False, "action": "no_intent", "last_phase": "", "quarantine_path": None, "warning": None}

        phase = intent.get("phase", "unknown")
        if phase == "complete":
            self.intent_log.clear_intent()
            return {"recovered": True, "action": "cleared_stale", "last_phase": "complete", "quarantine_path": None, "warning": None}

        if phase == "append_events" and events_path is not None:
            offset = intent.get("byte_offset")
            expected = intent.get("expected_bytes")
            if offset is None:
                offset = self.staging.get_byte_offset("append_events")
            try:
                actual_size = events_path.stat().st_size if events_path.exists() else 0
            except OSError:
                actual_size = 0
            if offset is not None and actual_size == offset and expected is not None and expected == 0:
                self.intent_log.clear_intent()
                return {"recovered": True, "action": "no_partial_write", "last_phase": "append_events", "quarantine_path": None, "warning": None}
            if offset is not None and expected is not None and actual_size == offset + expected:
                recorded = intent.get("prefix_checksum")
                actual = prefix_checksum(events_path, offset) if events_path.exists() else None
                if recorded is None or actual == recorded:
                    self.intent_log.clear_intent()
                    return {"recovered": True, "action": "events_committed", "last_phase": "append_events", "quarantine_path": None, "warning": None}
                qpath = self.intent_log.quarantine("checksum_mismatch")
                return {
                    "recovered": False, "action": "quarantined_checksum_mismatch",
                    "last_phase": "append_events", "quarantine_path": str(qpath) if qpath else None,
                    "warning": "Append prefix checksum mismatch; events and intent quarantined for manual review.",
                }
            if offset is not None:
                recorded = intent.get("prefix_checksum")
                actual = prefix_checksum(events_path, offset) if events_path.exists() else None
                if recorded is None or actual == recorded:
                    self.rollback_events(events_path, offset)
                    self.intent_log.clear_intent()
                    return {"recovered": True, "action": "rolled_back_events", "last_phase": "append_events", "quarantine_path": None, "warning": None}
                qpath = self.intent_log.quarantine("checksum_mismatch")
                return {
                    "recovered": False, "action": "quarantined_checksum_mismatch",
                    "last_phase": "append_events", "quarantine_path": str(qpath) if qpath else None,
                    "warning": "Append prefix checksum mismatch; events and intent quarantined for manual review.",
                }
            self.intent_log.clear_intent()
            return {"recovered": True, "action": "cleared_intent_will_retry", "last_phase": phase, "quarantine_path": None, "warning": None}

        if phase == "failed":
            qpath = self.intent_log.quarantine("failed_intent")
            return {
                "recovered": False, "action": "quarantined_failed_intent",
                "last_phase": intent.get("last_phase", "unknown"), "quarantine_path": str(qpath) if qpath else None,
                "warning": "Failed commit intent quarantined.",
            }

        self.intent_log.clear_intent()
        return {"recovered": False, "action": "cleared_intent_will_retry", "last_phase": phase, "quarantine_path": None, "warning": None}
    
    def get_phase_timings(self) -> dict[str, float]:
        return dict(self.timings)
