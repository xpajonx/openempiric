import type { Plugin } from "@opencode-ai/plugin"
import * as path from "path"

export const OpenempiricPlugin: Plugin = async ({ $ }) => {
  const repoDir = "/home/xpajonx/.config/opencode/harness-mcp/opencode-harness"
  return {
    config: async (config) => {
      // 1. Register MCP
      config.mcp = config.mcp || {}
      config.mcp.openempiric = {
        type: "local",
        command: [
          "uv",
          "run",
          "--directory",
          repoDir,
          "python",
          "-m",
          "harness_orchestrator.server"
        ],
        enabled: true,
        timeout: 60000
      }

      // 2. Register Instructions
      config.instructions = config.instructions || []
      const memoryStart = path.join(repoDir, "instructions", "memory-start.md")
      const aggressiveStart = path.join(repoDir, "instructions", "aggressive-start.md")

      if (!config.instructions.includes(memoryStart)) {
        config.instructions.push(memoryStart)
      }
      if (!config.instructions.includes(aggressiveStart)) {
        config.instructions.push(aggressiveStart)
      }
    }
  }
}
