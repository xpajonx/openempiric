# OEM Adapters package
from typing import Optional
from oem_knowledge.engine import KnowledgeEngine

def get_adapter(agent_name: str, engine: KnowledgeEngine, project_path: Optional[str] = None):
    name_clean = agent_name.lower().strip()
    if name_clean == "opencode":
        from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
        return OpenCodeAdapter(engine, project_path)
    elif name_clean in ("agy", "antigravity"):
        from oem_knowledge.adapters.antigravity.adapter import AntigravityAdapter
        return AntigravityAdapter(engine, project_path)
    else:
        from oem_knowledge.adapters.base import BaseAdapter
        return BaseAdapter(engine, project_path)
