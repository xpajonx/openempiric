from __future__ import annotations

# Deprecated compatibility re-exports: project resolution will be moved to oem_knowledge.project in a future release.
from .project import (
    ProjectResolutionError,
    ProjectMismatchError,
    ProjectUnresolvedError,
    SESSION_TO_PROJECT,
    find_nearest_oem_root,
    is_oem_dev_repo,
    is_path_inside,
    get_project_root_from_active_session,
    resolve_active_project,
    handle_resolution_error
)


def mount_tools(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    from oem_knowledge.tools import todos, metrics, lifecycle, concepts, skills, source, diagnostics, checkpoint
    todos.register(mcp)
    metrics.register(mcp)
    lifecycle.register(mcp)
    concepts.register(mcp)
    skills.register(mcp)
    source.register(mcp)
    diagnostics.register(mcp)
    checkpoint.register(mcp)


def main() -> None:
    import os
    # Suppress fastmcp logs on stdout/stderr to protect MCP JSON-RPC transport stream
    if "FASTMCP_LOG_LEVEL" not in os.environ:
        os.environ["FASTMCP_LOG_LEVEL"] = "WARNING"
    if "FASTMCP_LOG_ENABLED" not in os.environ:
        os.environ["FASTMCP_LOG_ENABLED"] = "false"

    from oem_knowledge.engine import apply_oem_process_env_defaults
    apply_oem_process_env_defaults()

    from fastmcp import FastMCP
    mcp = FastMCP("openempiric")
    mount_tools(mcp)
    mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
