from __future__ import annotations

import re
from pathlib import Path

def _strip_jsonc_comments(text: str) -> str:
    """Safely strip JSONC comments without destroying comments/slashes inside string literals (like URLs)."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/')
    return pattern.sub(lambda m: m.group(1) if m.group(1) else "", text)


def is_oem_managed_plugin(path: Path) -> bool:
    if not path.exists():
        return True
    if path.is_symlink():
        return True
    try:
        content = path.read_text(encoding="utf-8")
        return "generated_by: openempiric" in content or "source_type: oem_opencode_plugin" in content
    except Exception:
        return False
