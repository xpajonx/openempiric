from __future__ import annotations
import json
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

LATEST_SCHEMA_VERSION = 1


class EventMigrator:
    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine
        self._upcasters: dict[tuple[int, int], Callable[[dict], dict]] = {}
        self._register_default_migrations()

    def register_upcaster(self, from_version: int, to_version: int, upcaster: Callable[[dict], dict]):
        """Register an explicit from_version -> to_version upcaster function."""
        self._upcasters[(from_version, to_version)] = upcaster

    def _register_default_migrations(self):
        # Default registry is empty for now (LATEST_SCHEMA_VERSION = 1)
        pass

    def upcast(self, event: dict) -> dict:
        """Upcast an event dict sequentially from its schema_version to LATEST_SCHEMA_VERSION."""
        curr_ver = event.get("schema_version", 1)
        # Sequentially find and apply explicit step-by-step migrations
        while curr_ver < LATEST_SCHEMA_VERSION:
            next_ver = curr_ver + 1
            key = (curr_ver, next_ver)
            if key in self._upcasters:
                upcaster = self._upcasters[key]
                event = upcaster(event)
                event["schema_version"] = next_ver
                curr_ver = next_ver
            else:
                # If no registered upcaster exists for the next step, bump version to avoid infinite loop.
                event["schema_version"] = next_ver
                curr_ver = next_ver
        return event

    def migrate_file(self, project: str | None = None) -> dict:
        p = self.engine._events_path(project)
        lock_path = p.with_suffix(".lock")
        sfs = self.engine._sfs(project)

        from oem_knowledge.engine import FileLock
        with FileLock(lock_path):
            if not sfs.exists(p):
                return {"status": "success", "migrated_count": 0}

            lines = sfs.read_text(p).splitlines()
            migrated_events = []
            migrated_count = 0

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                ev_dict = json.loads(line)
                old_ver = ev_dict.get("schema_version", 1)
                new_dict = self.upcast(ev_dict)
                if new_dict.get("schema_version", 1) > old_ver:
                    migrated_count += 1
                migrated_events.append(new_dict)

            if migrated_count > 0:
                new_content = "".join(json.dumps(ev) + "\n" for ev in migrated_events)
                sfs.write_text(p, new_content, force_allow_truncation=True)

            return {
                "status": "success",
                "migrated_count": migrated_count,
                "total_count": len(migrated_events),
            }

    def get_schema_status(self, project: str | None = None) -> dict:
        """Scans event log file and returns a summary of schema versions."""
        p = self.engine._events_path(project)
        sfs = self.engine._sfs(project)
        if not sfs.exists(p):
            return {
                "status": "up_to_date",
                "current_versions": [],
                "latest_version": LATEST_SCHEMA_VERSION,
                "message": f"no events file, target v{LATEST_SCHEMA_VERSION}",
            }

        versions = set()
        try:
            for line in sfs.read_text(p).splitlines():
                line = line.strip()
                if line:
                    ev = json.loads(line)
                    versions.add(ev.get("schema_version", 1))
        except Exception as e:
            return {
                "status": "error",
                "message": f"error reading events log: {e}",
            }

        if not versions:
            return {
                "status": "up_to_date",
                "current_versions": [],
                "latest_version": LATEST_SCHEMA_VERSION,
                "message": f"empty log, target v{LATEST_SCHEMA_VERSION}",
            }

        versions_sorted = sorted(list(versions))
        if versions_sorted == [LATEST_SCHEMA_VERSION]:
            return {
                "status": "up_to_date",
                "current_versions": versions_sorted,
                "latest_version": LATEST_SCHEMA_VERSION,
                "message": f"all events at latest v{LATEST_SCHEMA_VERSION}",
            }

        return {
            "status": "outdated",
            "current_versions": versions_sorted,
            "latest_version": LATEST_SCHEMA_VERSION,
            "message": f"mixed/outdated schema versions: {versions_sorted}, target v{LATEST_SCHEMA_VERSION}",
        }
