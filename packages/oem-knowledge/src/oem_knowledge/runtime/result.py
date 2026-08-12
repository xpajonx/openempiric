"""Result contract for OEM operations.

Allowed statuses: success, partial, warn, empty, error.
Caller precedence when multiple signals disagree: error > partial > warn > empty > success.
`failed_step` names the phase that failed (e.g. "reflection", "indexing").
Recovery metadata (`recovered`, `recovery_action`, `recovery_warning`) is additive:
it must never alter existing fields or status precedence.
"""
from __future__ import annotations
from typing import Any

def make_result(
    status: str,
    operation: str,
    project: str = "",
    message: str = "",
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    suggestion: str | None = None,
    failed_step: str | None = None,
    **kwargs
) -> dict[str, Any]:
    # Restrict status vocabulary
    allowed_statuses = {"success", "partial", "empty", "warn", "error"}
    if status not in allowed_statuses:
        # Map old variants or defaults if necessary
        if status in ("ok", "done", "clean", "repaired"):
            status = "success"
        elif status in ("failed", "failure"):
            status = "error"
        elif status in ("warning", "degraded", "issues_found", "repaired_partial"):
            status = "warn"
        else:
            status = "success"

    res = {
        "status": status,
        "operation": operation,
        "project": project,
        "message": message,
        "warnings": warnings or [],
        "errors": errors or [],
        "suggestion": suggestion,
        "failed_step": failed_step,
        "data": kwargs.get("data", {}),
    }

    # Copy all other kwargs directly
    for k, v in kwargs.items():
        if k != "data":
            res[k] = v

    # Expose data keys on the root for backward compatibility
    if isinstance(res["data"], dict):
        for k, v in res["data"].items():
            if k not in res:
                res[k] = v

    return res

def success(operation: str, message: str = "", **kwargs) -> dict[str, Any]:
    return make_result("success", operation, message=message, **kwargs)

def partial(operation: str, message: str = "", warnings=None, **kwargs) -> dict[str, Any]:
    return make_result("partial", operation, message=message, warnings=warnings, **kwargs)

def warn(operation: str, message: str = "", warnings=None, **kwargs) -> dict[str, Any]:
    return make_result("warn", operation, message=message, warnings=warnings, **kwargs)

def error(operation: str, message: str, failed_step=None, errors=None, **kwargs) -> dict[str, Any]:
    return make_result("error", operation, message=message, failed_step=failed_step, errors=errors, **kwargs)
