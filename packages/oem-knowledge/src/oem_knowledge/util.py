from __future__ import annotations

import re
from pathlib import Path

def _strip_jsonc_comments(text: str) -> str:
    """Normalize JSONC for Python's JSON parser.

    OpenCode accepts comments and trailing commas in ``opencode.jsonc``. Keep
    string literals byte-for-byte intact while removing those JSONC-only
    features so the result can be passed to :func:`json.loads` safely.
    """
    without_comments: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]

        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            without_comments.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < len(text) and text[index + 1] == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue

        if char == "/" and index + 1 < len(text) and text[index + 1] == "*":
            comment_start = index
            index += 2
            closed = False
            while index < len(text):
                if text[index:index + 2] == "*/":
                    index += 2
                    closed = True
                    break
                if text[index] in "\r\n":
                    # Preserve line positions in parse errors.
                    without_comments.append(text[index])
                index += 1
            if not closed:
                # Preserve malformed comments so invalid JSONC still fails
                # validation instead of being silently accepted.
                without_comments.append(text[comment_start:])
                break
            continue

        without_comments.append(char)
        index += 1

    return _strip_jsonc_trailing_commas("".join(without_comments))


def _strip_jsonc_trailing_commas(text: str) -> str:
    """Remove commas immediately before ``]`` or ``}`` outside strings."""
    normalized: list[str] = []
    index = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]

        if in_string:
            normalized.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            normalized.append(char)
            index += 1
            continue

        if char == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                index += 1
                continue

        normalized.append(char)
        index += 1

    return "".join(normalized)


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
