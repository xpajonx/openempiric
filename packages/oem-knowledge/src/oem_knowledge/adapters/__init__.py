# OEM Adapters package
from typing import Optional
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter, get_registered_adapter

# Decorate and import standard adapters here so they get registered on import
from oem_knowledge.adapters.opencode.adapter import OpenCodeAdapter
from oem_knowledge.adapters.antigravity.adapter import AntigravityAdapter

def get_adapter(agent_name: str, engine: KnowledgeEngine, project_path: Optional[str] = None):
    name_clean = agent_name.lower().strip()
    
    # 1. Resolve registered adapter
    cls = get_registered_adapter(name_clean)
    if cls is not None:
        return cls(engine, project_path)
        
    # 2. Fallback to default BaseAdapter
    return BaseAdapter(engine, project_path)
