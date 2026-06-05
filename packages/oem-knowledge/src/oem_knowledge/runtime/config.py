from __future__ import annotations
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]

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
