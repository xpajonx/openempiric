from __future__ import annotations

from .config import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS, _OPENCODE_PLUGINS_DIR

def __getattr__(name: str):
    if name == "_compile_oem_context":
        from .context import _compile_oem_context
        return _compile_oem_context
    elif name == "run_agent":
        from .runner import run_agent
        return run_agent
    elif name == "cmd_recover":
        from .recovery import cmd_recover
        return cmd_recover
    elif name == "SessionState":
        from .session import SessionState
        return SessionState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
