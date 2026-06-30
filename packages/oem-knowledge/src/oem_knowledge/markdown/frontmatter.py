"""
Canonical frontmatter parser for OpenEmpiric markdown files.

Rules:
- Only line 0 may open frontmatter (must be exactly "---").
- First "---" or "..." after line 0 closes frontmatter.
- All later "---" lines are body content.
- No line limit cap.
- Warnings are structured dicts with "reason" and optional "path" keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class ParsedMarkdown:
    """Result of parsing frontmatter from a markdown document."""

    metadata: dict[str, Any]
    body: str
    warnings: list[dict[str, str]] = field(default_factory=list)


def safe_yaml_load(yaml_text: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """
    Parse YAML frontmatter text, returning (metadata, warnings).

    Warnings are produced for:
    - yaml.safe_load raises an exception → frontmatter_yaml_parse_error
    - yaml.safe_load returns a non-dict → frontmatter_not_mapping

    In warning cases, metadata is still populated via manual key:value
    fallback, but the warning is NOT suppressed.
    """
    warnings: list[dict[str, str]] = []

    if yaml is not None:
        try:
            loaded = yaml.safe_load(yaml_text) or {}
            if not isinstance(loaded, dict):
                warnings.append({"reason": "frontmatter_not_mapping"})
            else:
                return loaded, warnings
        except Exception:
            warnings.append({"reason": "frontmatter_yaml_parse_error"})

    # Manual key:value fallback
    metadata: dict[str, Any] = {}
    for line in yaml_text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    return metadata, warnings


def parse_frontmatter(
    text: str, *, source_path: str | None = None
) -> ParsedMarkdown:
    """
    Parse YAML frontmatter from a markdown document.

    Only line 0 may open frontmatter.  The first ``---`` or ``...`` after line 0
    closes it.  All later ``---`` lines are treated as body content.

    Args:
        text: Full markdown document text.
        source_path: Optional path for inclusion in warning dicts.

    Returns:
        ParsedMarkdown with metadata, body, and any warnings.
    """
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        return ParsedMarkdown(metadata={}, body=text, warnings=[])

    # Scan for closing delimiter; only the first one counts
    for i in range(1, len(lines)):
        if lines[i].strip() in {"---", "..."}:
            yaml_text = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :])
            metadata, yaml_warnings = safe_yaml_load(yaml_text)

            # Attach source_path to each warning
            sp = source_path or ""
            for w in yaml_warnings:
                w["path"] = sp

            return ParsedMarkdown(metadata=metadata, body=body, warnings=yaml_warnings)

    # No closing delimiter found
    return ParsedMarkdown(
        metadata={},
        body=text,
        warnings=[
            {
                "reason": "frontmatter_block_not_closed",
                "path": source_path or "",
            }
        ],
    )
