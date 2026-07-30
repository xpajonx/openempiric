"""Commit pipeline with write-ahead intent logging for session-end operations.

Provides crash recovery: if a commit fails mid-pipeline, the intent log
records which phase was in progress so the next session can resume or rollback.
"""

import json
import os
import time
from pathlib import Path
from typing import Any


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
        intent = {
            "phase": phase,
            "timestamp": time.time(),
            **extra,
        }
        with open(self.intent_path, "w") as f:
            json.dump(intent, f)
    
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
    
    def rollback_events(self, events_path: Path) -> None:
        """Truncate events.jsonl to pre-append byte offset on failure."""
        offset = self.staging.get_byte_offset("append_events")
        if offset is not None and events_path.exists():
            with open(events_path, "r+b") as f:
                f.truncate(offset)
    
    def complete_pipeline(self) -> None:
        """Mark the entire pipeline as complete."""
        self.intent_log.write_intent("complete")
        self.intent_log.clear_intent()
    
    def recover_from_crash(self, events_path: Path | None = None) -> dict:
        """Check for uncompleted intent and attempt recovery.
        
        Returns:
            dict with recovery info: {'recovered': bool, 'last_phase': str, 'action': str}
        """
        intent = self.intent_log.read_intent()
        if intent is None:
            return {"recovered": False, "action": "no_intent"}
        
        phase = intent.get("phase", "unknown")
        if phase == "complete":
            self.intent_log.clear_intent()
            return {"recovered": True, "last_phase": "complete", "action": "cleared_stale"}
        
        # If crashed during append_events, roll back to recorded offset
        if phase == "append_events" and events_path is not None:
            self.rollback_events(events_path)
            self.intent_log.clear_intent()
            return {"recovered": True, "last_phase": "append_events", "action": "rolled_back_events"}
        
        # For other phases, clear intent and let next session restart
        self.intent_log.clear_intent()
        return {"recovered": False, "last_phase": phase, "action": "cleared_intent_will_retry"}
    
    def get_phase_timings(self) -> dict[str, float]:
        return dict(self.timings)
