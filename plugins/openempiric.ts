import type { Plugin } from "@opencode-ai/plugin"
import * as path from "path"
import * as fs from "fs"
import * as os from "os"
import { fileURLToPath } from "url"

function resolveRepoDir(): string {
  if (process.env.OPENEMPIRIC_DIR) {
    return process.env.OPENEMPIRIC_DIR
  }

  try {
    const currentFilePath = fileURLToPath(import.meta.url)
    const realFilePath = fs.realpathSync(currentFilePath)
    let dir = path.dirname(realFilePath)
    while (dir && dir !== path.parse(dir).root) {
      if (fs.existsSync(path.join(dir, "pyproject.toml")) && fs.existsSync(path.join(dir, "packages", "harness-orchestrator"))) {
        return dir
      }
      dir = path.dirname(dir)
    }
  } catch (e) {
    // Ignore error
  }

  const home = os.homedir()
  const candidates = [
    path.join(home, ".config", "opencode", "harness-mcp", "opencode-harness"),
    path.join(home, "openempiric"),
    path.join(home, "projects", "openempiric"),
    path.join(home, "workspace", "openempiric")
  ]

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, "pyproject.toml"))) {
      return candidate
    }
  }

  return path.join(home, ".config", "opencode", "harness-mcp", "opencode-harness")
}

export const OpenempiricPlugin: Plugin = async ({ $ }) => {
  const repoDir = resolveRepoDir()
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
          "harness_knowledge.server"
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
