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

      // 2. Register Instructions (OEMRuntimeContext prompt injection)
      config.instructions = config.instructions || []
      
      const tempInstPath = path.join(os.homedir(), ".config", "opencode", "plugins", ".openempiric_temp_instructions.md")
      let instContent = "# openempiric Session Context\n\n"
      
      if (config.openempiric) {
        const oe = config.openempiric
        
        instContent += "## Active Concepts\n"
        if (oe.active_concepts && oe.active_concepts.length > 0) {
          oe.active_concepts.forEach((c: any) => {
            instContent += `- **${c.name}** (${c.id}): ${c.description || 'No description available.'}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Active Decisions\n"
        if (oe.active_decisions && oe.active_decisions.length > 0) {
          oe.active_decisions.forEach((d: any) => {
            instContent += `- ${d}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Relevant Failures\n"
        if (oe.relevant_failures && oe.relevant_failures.length > 0) {
          oe.relevant_failures.forEach((f: any) => {
            instContent += `- ${f}\n`
          })
        } else {
          instContent += "- None\n"
        }
        
        instContent += "\n## Open Questions\n"
        if (oe.open_questions && oe.open_questions.length > 0) {
          oe.open_questions.forEach((q: any) => {
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
