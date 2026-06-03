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
      // 1. Register MCP (safely merge existing config to preserve env context)
      const existingMcp = config.mcp?.openempiric || {}
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
        timeout: 60000,
        ...existingMcp,
        env: {
          ...(existingMcp.env || {})
        }
      }

      // 2. Read OEMRuntimeContext from well-known file (primary) or env var (fallback)
      const contextPath = process.env.OEM_RUNTIME_CONTEXT_PATH ||
        path.join(os.homedir(), ".config", "opencode", "plugins", ".oem_runtime_context.json")
      const tempInstPath = process.env.OEM_TEMP_INSTRUCTIONS ||
        path.join(os.homedir(), ".config", "opencode", "plugins", ".openempiric_temp_instructions.md")

      let oemContext: any = null
      try {
        if (fs.existsSync(contextPath)) {
          const content = fs.readFileSync(contextPath, "utf-8")
          oemContext = JSON.parse(content)
        }
      } catch (e) {
        console.error("Failed to read OEM runtime context from file:", e)
      }
      if (!oemContext && config.mcp.openempiric.env?.OEM_RUNTIME_CONTEXT) {
        try {
          oemContext = JSON.parse(config.mcp.openempiric.env.OEM_RUNTIME_CONTEXT)
        } catch (e) {
          console.error("Failed to parse OEM_RUNTIME_CONTEXT JSON:", e)
        }
      }

      // 3. Register Instructions (OEMRuntimeContext prompt injection)
      config.instructions = config.instructions || []
      let instContent = "# openempiric Session Context\n\n"

      if (oemContext) {
        instContent += "## Active Concepts\n"
        if (oemContext.active_concepts && oemContext.active_concepts.length > 0) {
          oemContext.active_concepts.forEach((c: any) => {
            instContent += `- **${c.name}** (${c.id}): ${c.description || 'No description available.'}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Active Decisions\n"
        if (oemContext.active_decisions && oemContext.active_decisions.length > 0) {
          oemContext.active_decisions.forEach((d: any) => {
            instContent += `- ${d}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Relevant Failures\n"
        if (oemContext.relevant_failures && oemContext.relevant_failures.length > 0) {
          oemContext.relevant_failures.forEach((f: any) => {
            instContent += `- ${f}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Open Questions\n"
        if (oemContext.open_questions && oemContext.open_questions.length > 0) {
          oemContext.open_questions.forEach((q: any) => {
            instContent += `- ${q}\n`
          })
        } else {
          instContent += "- None\n"
        }
      }
      
      try {
        fs.writeFileSync(tempInstPath, instContent, "utf-8")
        if (!config.instructions.includes(tempInstPath)) {
          config.instructions.push(tempInstPath)
        }
      } catch (e) {
        console.error("Failed to write transient openempiric instructions:", e)
      }
    }
  }
}
