from __future__ import annotations

from .main import main
from ..runtime import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS

# Dynamic attribute resolution for test mocks/compatibility and lazy loading
def __getattr__(name: str):
    if name == "KnowledgeEngine":
        from ..engine import KnowledgeEngine
        return KnowledgeEngine
    elif name == "check_mcp_server":
        from .helpers import check_mcp_server
        return check_mcp_server
    elif name == "subprocess":
        import subprocess
        return subprocess
    elif name == "shutil":
        import shutil
        return shutil

    elif name == "run_agent":
        from ..runtime import run_agent
        return run_agent
    elif name == "cmd_recover":
        from ..runtime import cmd_recover
        return cmd_recover
    elif name == "SessionState":
        from ..runtime import SessionState
        return SessionState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
