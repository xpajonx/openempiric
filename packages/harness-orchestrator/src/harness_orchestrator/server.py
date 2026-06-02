from __future__ import annotations

from fastmcp import FastMCP

from .tools import sessions, subagents, config, db
from .tools import plan as plan_tools
from .tools import todos as todo_tools

from harness_knowledge.server import mount_tools

mcp = FastMCP("harness")

sessions.register(mcp)
subagents.register(mcp)
config.register(mcp)
db.register(mcp)
plan_tools.register(mcp)
todo_tools.register(mcp)

mount_tools(mcp)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
