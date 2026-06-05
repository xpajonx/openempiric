from __future__ import annotations
import os
from pathlib import Path

def _find_repo_root() -> Path:
    curr = Path(__file__).resolve().parent
    for _ in range(10):
        if (curr / "plugins" / "openempiric.ts").exists() or (curr / ".git").exists():
            return curr
        if curr.parent == curr:
            break
        curr = curr.parent
    # fallback
    return Path(__file__).resolve().parent.parent.parent.parent.parent.parent

_REPO_ROOT = _find_repo_root()


_OPENCODE_PLUGINS_DIR = Path(
    os.environ.get(
        "OPENCODE_PLUGINS_DIR",
        Path.home() / ".config" / "opencode" / "plugins",
    )
)

_OEM_RUNTIME_CONTEXT_PATH = Path(
    os.environ.get(
        "OEM_RUNTIME_CONTEXT_PATH",
        _OPENCODE_PLUGINS_DIR / ".oem_runtime_context.json",
    )
)

_OEM_TEMP_INSTRUCTIONS = Path(
    os.environ.get(
        "OEM_TEMP_INSTRUCTIONS",
        _OPENCODE_PLUGINS_DIR / ".openempiric_temp_instructions.md",
    )
)
