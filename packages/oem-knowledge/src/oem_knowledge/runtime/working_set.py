from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Any, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class WorkingSet(BaseModel):
    schema_version: int = 1
    updated_at: str = Field(default_factory=lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    workspace_root: str

    goal: Optional[str] = None
    current_problem: Optional[str] = None
    current_hypothesis: Optional[str] = None
    next_action: Optional[str] = None

    active_work_item: Optional[str] = None
    active_topic: Optional[str] = None
    active_task: Optional[str] = None

    active_files: List[str] = Field(default_factory=list)
    active_concepts: List[str] = Field(default_factory=list)
    active_memory_ids: List[str] = Field(default_factory=list)

    blocked_by: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)

    confidence: str = "unknown"
    checkpoint_reason: Optional[str] = None


class WorkingSetService:
    def __init__(self, engine: Any):
        self.engine = engine

    def _get_path(self) -> Path:
        return self.engine.layout().working_set_path

    def load(self) -> WorkingSet | None:
        path = self._get_path()
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            
            # Simple migration logic: if schema_version is missing or older (e.g. 0 or version=1), migrate to 1.
            # (Note: we also check 'version' in case of legacy files from developer experiments)
            if "schema_version" not in data:
                if "version" in data:
                    data["schema_version"] = data.pop("version")
                else:
                    data["schema_version"] = 1
            
            return WorkingSet(**data)
        except Exception as e:
            logger.warning("Failed to load working set from %s: %s", path, e)
            return None

    def save(self, ws: WorkingSet) -> None:
        from oem_knowledge.fs import FileLock
        path = self._get_path()
        lock_path = path.with_suffix(".lock")
        
        # Conditional write: only save if contents changed (excluding updated_at)
        existing = self.load()
        if existing is not None:
            fields = ws.__class__.model_fields.keys() if hasattr(ws.__class__, "model_fields") else ws.__fields__.keys()
            changed = False
            for f in fields:
                if f == "updated_at":
                    continue
                if getattr(ws, f) != getattr(existing, f):
                    changed = True
                    break
            if not changed:
                return

        path.parent.mkdir(parents=True, exist_ok=True)
        ws.updated_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        
        with FileLock(lock_path):
            tmp = path.with_name(f".{path.name}.{int(time.time() * 1000000)}.tmp")
            try:
                data = ws.model_dump() if hasattr(ws, "model_dump") else ws.dict()
                tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                tmp.replace(path)
            except Exception:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                raise

    def update(self, **kwargs) -> WorkingSet:
        ws = self.load()
        if ws is None:
            workspace_root = str(self.engine.layout().root.parent.resolve())
            ws = WorkingSet(workspace_root=workspace_root)
            
        for k, v in kwargs.items():
            if hasattr(ws, k):
                val = v
                if k == "active_files" and isinstance(v, list):
                    val = v[:20]
                elif k == "active_concepts" and isinstance(v, list):
                    val = v[:30]
                elif k == "active_memory_ids" and isinstance(v, list):
                    val = v[:50]
                setattr(ws, k, val)
        self.save(ws)
        return ws

    def merge(self, **kwargs) -> WorkingSet:
        ws = self.load()
        if ws is None:
            workspace_root = str(self.engine.layout().root.parent.resolve())
            ws = WorkingSet(workspace_root=workspace_root)
            
        for k, v in kwargs.items():
            if not hasattr(ws, k):
                continue
            
            current_val = getattr(ws, k)
            if isinstance(current_val, list):
                if v is not None:
                    new_list = v if isinstance(v, list) else [v]
                    merged = list(current_val)
                    for item in new_list:
                        if item not in merged:
                            merged.append(item)
                    
                    # Apply limits
                    if k == "active_files":
                        merged = merged[:20]
                    elif k == "active_concepts":
                        merged = merged[:30]
                    elif k == "active_memory_ids":
                        merged = merged[:50]
                        
                    setattr(ws, k, merged)
            else:
                if v is not None:
                    setattr(ws, k, v)
        self.save(ws)
        return ws


def load_working_set(project: str | Path | None = None) -> WorkingSet | None:
    from oem_knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine(project)
    service = WorkingSetService(engine)
    return service.load()


def save_working_set(working_set: WorkingSet, project: str | Path | None = None) -> None:
    from oem_knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine(project)
    service = WorkingSetService(engine)
    service.save(working_set)


def update_working_set(project: str | Path | None = None, **kwargs) -> WorkingSet:
    from oem_knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine(project)
    service = WorkingSetService(engine)
    return service.update(**kwargs)


def merge_working_set(project: str | Path | None = None, **kwargs) -> WorkingSet:
    from oem_knowledge.engine import KnowledgeEngine
    engine = KnowledgeEngine(project)
    service = WorkingSetService(engine)
    return service.merge(**kwargs)


def get_resume_status(project: str | Path | None = None) -> dict:
    from oem_knowledge.engine import KnowledgeEngine
    from datetime import datetime, timezone
    
    engine = KnowledgeEngine(project)
    harness = engine._resolve_harness(project)
    ws_path = harness / "state" / "working_set.json"
    
    ws_exists = ws_path.exists()
    ws_corrupt = False
    ws = None
    
    if ws_exists:
        try:
            ws = load_working_set(project)
            if ws is None:
                ws_corrupt = True
        except Exception:
            ws_corrupt = True
            
    # Timestamps
    ws_ts = 0.0
    ws_age = None
    if ws is not None and not ws_corrupt:
        if ws.updated_at:
            try:
                iso_str = ws.updated_at
                if iso_str.endswith("Z"):
                    iso_str = iso_str[:-1] + "+00:00"
                dt = datetime.fromisoformat(iso_str)
                ws_ts = dt.timestamp()
                now_dt = datetime.now(timezone.utc)
                ws_age = max(0.0, (now_dt - dt).total_seconds())
            except Exception:
                pass
        if ws_ts == 0.0:
            try:
                ws_ts = ws_path.stat().st_mtime
                ws_age = max(0.0, time.time() - ws_ts)
            except Exception:
                pass

    # Determine handoff timestamp
    handoff_ts = 0.0
    has_handoff_ts = False
    
    # 1. session-handoff.json
    hj = harness / "session-handoff.json"
    if hj.exists():
        try:
            content = hj.read_text(encoding="utf-8")
            data = json.loads(content)
            updated_at = data.get("updated_at")
            if updated_at:
                if updated_at.endswith("Z"):
                    updated_at = updated_at[:-1] + "+00:00"
                handoff_ts = datetime.fromisoformat(updated_at).timestamp()
                has_handoff_ts = True
        except Exception:
            pass
        if not has_handoff_ts:
            try:
                handoff_ts = hj.stat().st_mtime
                has_handoff_ts = True
            except Exception:
                pass

    # 2. session-handoff.md files (only fallback if json didn't provide a timestamp)
    if not has_handoff_ts:
        for md_file in [harness / "session-handoff.md", harness / "state" / "session-handoff.md"]:
            if md_file.exists():
                try:
                    handoff_ts = max(handoff_ts, md_file.stat().st_mtime)
                except Exception:
                    pass

    if not ws_exists:
        resume_source = "session_handoff"
        freshness = "handoff is newer"
        resume_reason = "working set missing"
    elif ws_corrupt:
        resume_source = "session_handoff"
        freshness = "handoff is newer"
        resume_reason = "working set corrupt"
    else:
        if ws_ts > handoff_ts:
            resume_source = "working_set"
            freshness = "working_set is newer"
            resume_reason = "working_set is newer than session-handoff"
        else:
            resume_source = "session_handoff"
            freshness = "handoff is newer"
            resume_reason = "session-handoff is newer or equal"

    return {
        "working_set_source": str(ws_path),
        "working_set_age": ws_age,
        "resume_source": resume_source,
        "freshness": freshness,
        "resume_reason": resume_reason,
        "exists": ws_exists and not ws_corrupt,
        "corrupt": ws_corrupt,
    }


def create_checkpoint(reason: str, project: str | Path | None = None) -> Path | None:
    from oem_knowledge.engine import KnowledgeEngine
    import json
    from datetime import datetime, timezone

    try:
        engine = KnowledgeEngine(project)
        harness = engine._resolve_harness(project)
        ws_path = harness / "state" / "working_set.json"

        if ws_path.exists():
            try:
                data = json.loads(ws_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}

        if "workspace_root" not in data:
            data["workspace_root"] = str(engine.layout().root.parent.resolve())
        data["checkpoint_reason"] = reason
        data["updated_at"] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        ws = WorkingSet(**data)
        content = json.dumps(ws.model_dump() if hasattr(ws, "model_dump") else ws.dict(), indent=2) + "\n"

        checkpoints_dir = harness / "state" / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)

        ts_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')
        cp_name = f"checkpoint_{ts_str}.json"
        cp_path = checkpoints_dir / cp_name

        cp_path.write_text(content, encoding="utf-8")
        prune_checkpoints(checkpoints_dir)
        return cp_path
    except Exception as e:
        logger.warning("Failed to create checkpoint: %s", e)
        return None


def prune_checkpoints(checkpoints_dir: Path) -> None:
    existing_files = list(checkpoints_dir.glob("checkpoint_*.json"))
    existing_files.sort(key=lambda x: x.name)
    if len(existing_files) > 20:
        to_delete = existing_files[:-20]
        for f in to_delete:
            try:
                f.unlink()
            except Exception:
                pass


def restore_checkpoint(target: str, project: str | Path | None = None) -> bool:
    from oem_knowledge.engine import KnowledgeEngine
    try:
        engine = KnowledgeEngine(project)
        harness = engine._resolve_harness(project)
        checkpoints_dir = harness / "state" / "checkpoints"
        ws_path = harness / "state" / "working_set.json"

        if not checkpoints_dir.exists():
            return False

        target_str = str(target).strip()
        cp_path = None

        if target_str.endswith(".json"):
            p = checkpoints_dir / target_str
            if p.exists():
                cp_path = p
        else:
            p = checkpoints_dir / f"{target_str}.json"
            if p.exists():
                cp_path = p
            else:
                p = checkpoints_dir / f"checkpoint_{target_str}.json"
                if p.exists():
                    cp_path = p

        if cp_path is None:
            candidates = []
            for f in checkpoints_dir.glob("checkpoint_*.json"):
                if target_str in f.name:
                    candidates.append(f)
            if len(candidates) == 1:
                cp_path = candidates[0]

        if cp_path is None or not cp_path.exists():
            return False

        content = cp_path.read_text(encoding="utf-8")
        data = json.loads(content)
        WorkingSet(**data)

        ws_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = ws_path.with_name(f".working_set.json.{int(time.time() * 1000000)}.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(ws_path)
        return True
    except Exception as e:
        logger.warning("Failed to restore checkpoint: %s", e)
        return False


def list_checkpoints(project: str | Path | None = None) -> list[dict]:
    from oem_knowledge.engine import KnowledgeEngine
    try:
        engine = KnowledgeEngine(project)
        harness = engine._resolve_harness(project)
        checkpoints_dir = harness / "state" / "checkpoints"
        if not checkpoints_dir.exists():
            return []

        files = list(checkpoints_dir.glob("checkpoint_*.json"))
        files.sort(key=lambda x: x.name)

        out = []
        for f in files:
            updated_at = None
            checkpoint_reason = None
            active_work_item = None
            goal = None
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                updated_at = data.get("updated_at")
                checkpoint_reason = data.get("checkpoint_reason")
                active_work_item = data.get("active_work_item")
                goal = data.get("goal")
            except Exception:
                pass
            
            name_id = f.stem[11:] if len(f.stem) > 11 else f.stem
            out.append({
                "name": f.name,
                "name_id": name_id,
                "path": str(f),
                "updated_at": updated_at,
                "checkpoint_reason": checkpoint_reason,
                "active_work_item": active_work_item,
                "goal": goal,
            })
        return out
    except Exception:
        return []


def compact_working_set(project: str | Path | None = None) -> bool:
    from oem_knowledge.runtime.working_set import load_working_set, save_working_set
    try:
        ws = load_working_set(project)
        if ws is None:
            return False

        if len(ws.active_files) > 20:
            ws.active_files = ws.active_files[-20:]
        if len(ws.active_concepts) > 30:
            ws.active_concepts = ws.active_concepts[-30:]
        if len(ws.active_memory_ids) > 50:
            ws.active_memory_ids = ws.active_memory_ids[-50:]
        if len(ws.blocked_by) > 10:
            ws.blocked_by = ws.blocked_by[-10:]
        if len(ws.open_questions) > 10:
            ws.open_questions = ws.open_questions[-10:]

        save_working_set(ws, project)
        return True
    except Exception as e:
        logger.warning("Failed to compact working set: %s", e)
        return False

