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
                setattr(ws, k, v)
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
