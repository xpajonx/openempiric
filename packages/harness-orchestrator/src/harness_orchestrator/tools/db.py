from __future__ import annotations

import re
import subprocess

from ..client import find_opencode

_ALLOWED_PREFIXES = ("SELECT", "PRAGMA", "WITH")
_FORBIDDEN_KEYWORDS = (
    "DELETE",
    "INSERT",
    "UPDATE",
    "DROP",
    "ALTER",
    "CREATE",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
)


def _is_safe(sql: str) -> tuple[bool, str]:
    cleaned = sql.strip().upper()
    if not cleaned:
        return False, "empty query"
    if not any(cleaned.startswith(p) for p in _ALLOWED_PREFIXES):
        return (
            False,
            f"only SELECT/PRAGMA/WITH queries allowed (got: {cleaned.split()[0]})",
        )
    for kw in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", cleaned):
            return False, f"forbidden keyword: {kw}"
    return True, ""


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_db_query(query: str, format: str = "json") -> str:
        """Execute a read-only SQL query against opencode's session database.

        Only SELECT, PRAGMA, and WITH queries are allowed. Write queries are blocked.
        Use this to inspect session data, token usage, or tool call history programmatically.

        Args:
            query: SQL query (SELECT, PRAGMA, or WITH only)
            format: Output format — 'json' or 'tsv'
        """
        safe, reason = _is_safe(query)
        if not safe:
            return f"Error: {reason}"

        cmd = find_opencode()
        args = [cmd, "db", "--format", format, query]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        except Exception as e:
            return f"Error: {e}"
        return result.stdout.strip() or result.stderr.strip() or "(empty)"
