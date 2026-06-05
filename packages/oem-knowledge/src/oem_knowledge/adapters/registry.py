from __future__ import annotations
import sys
from typing import Dict, Type

# Global registration dictionary
_REGISTRY: Dict[str, Type] = {}

def register_adapter(name: str):
    """Decorator to register a custom agent adapter."""
    def decorator(cls: Type):
        _REGISTRY[name.lower().strip()] = cls
        return cls
    return decorator

def get_registered_adapter(name: str) -> Type | None:
    """Retrieve an adapter by name, checking entry points if not loaded."""
    name_clean = name.lower().strip()
    
    # 1. Check local decorator registry
    if name_clean in _REGISTRY:
        return _REGISTRY[name_clean]
        
    # 2. Lazy load python entry points
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        if hasattr(eps, "select"):  # Python 3.10+ structure
            group_eps = eps.select(group="oem_knowledge.adapters")
        else:
            group_eps = eps.get("oem_knowledge.adapters", [])
            
        for ep in group_eps:
            if ep.name.lower().strip() == name_clean:
                try:
                    cls = ep.load()
                    _REGISTRY[name_clean] = cls
                    return cls
                except Exception:
                    pass
    except Exception:
        pass
        
    return None
