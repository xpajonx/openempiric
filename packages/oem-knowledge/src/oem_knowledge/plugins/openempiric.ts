import type { Plugin } from "@opencode-ai/plugin"
import * as path from "path"
import * as fs from "fs"
import * as os from "os"
import { fileURLToPath } from "url"

interface ContextBudget {
  conceptCountLimit: number;
}

class ContextAssembler {
  private budget: ContextBudget;

  constructor(budget: ContextBudget = { conceptCountLimit: 5 }) {
    this.budget = budget;
  }

  assemble(projectPath: string): string {
    const contextPath = process.env.OEM_RUNTIME_CONTEXT_PATH || path.join(os.homedir(), ".config", "opencode", "plugins", ".oem_runtime_context.json");
    if (!fs.existsSync(contextPath)) {
      return `Warning: OEM runtime context unavailable.
Please run: oem session-start`;
    }

    try {
      const oemContext = JSON.parse(fs.readFileSync(contextPath, "utf-8"));
      
      let instContent = `# OEM Runtime Notice
Project memory is already active. Relevant project memory has been restored automatically.
OEM memory serves as a persistent knowledge layer to inform your planning and guide your execution of project workflows (e.g. AGENTS.md) without replacing them.
Cross-reference the active concepts and past failures below to ensure your work aligns with existing decisions and avoids repeat mistakes.
Use OEM search when additional project context is needed (such as reviewing project history, understanding prior decisions, or investigating known failures).
Diagnostics (doctor/health) are not required for normal work and are automatically managed by the runtime supervisor. Do not assume work should proceed unless the user requests it.
Your utilization of memory is measured and logged at the end of the session via the knowledge_usage_report tool.

# Previous Session Context

`;
      
      instContent += "## Last Topic\n";
      instContent += (oemContext.last_topic || "General development") + "\n\n";

      instContent += "## Recent Decisions\n";
      const decisions = oemContext.recent_decisions || [];
      const injectedIds: string[] = [];
      if (decisions.length > 0) {
        for (const d of decisions.slice(0, 5)) {
          instContent += `- ${d}\n`;
        }
      } else {
        instContent += "- None\n";
      }

      instContent += "\n## Open Questions\n";
      const questions = oemContext.open_questions || [];
      if (questions.length > 0) {
        for (const q of questions.slice(0, 5)) {
          instContent += `- ${q}\n`;
        }
      } else {
        instContent += "- None\n";
      }

      instContent += "\n## Active Concepts\n";
      const concepts = oemContext.active_concepts || [];
      if (concepts.length > 0) {
        for (const c of concepts.slice(0, this.budget.conceptCountLimit)) {
          instContent += `- **${c.name}** (${c.id}): ${c.description || 'No description available.'}\n`;
          injectedIds.push(c.id);
        }
      } else {
        instContent += "- None\n";
      }

      instContent += "\n## Relevant Failures\n";
      const failures = oemContext.relevant_failures || [];
      if (failures.length > 0) {
        for (const f of failures.slice(0, 5)) {
          instContent += `- ${f}\n`;
        }
      } else {
        instContent += "- None\n";
      }

      instContent += "\n## Memory Context\n";
      instContent += oemContext.memory_context || "OEM is your long-term memory for this project. Use this information when relevant. Do not assume work should continue unless the user requests it. Your utilization of memory is measured and logged at the end of the session via the knowledge_usage_report tool.\n";
      instContent += "\n";

      // Save injected concepts to session state
      const sessionStatePath = path.join(projectPath, ".oem", "state", "session_state.json");
      try {
        fs.mkdirSync(path.dirname(sessionStatePath), { recursive: true });
        const sessionId = `session_${Date.now()}`;
        fs.writeFileSync(sessionStatePath, JSON.stringify({
          session_id: sessionId,
          last_injected_concepts: injectedIds,
          last_injected_at: new Date().toISOString()
        }, null, 2), "utf-8");
      } catch (e) {
        // Ignore
      }

      // Increment injected concepts count immediately
      const injectedCount = injectedIds.length;
      const metricsDir = path.join(projectPath, ".oem", "state");
      const metricsPath = path.join(metricsDir, "metrics.json");
      try {
        let mData = { knowledge_usage: { concepts_injected: 0 } };
        if (fs.existsSync(metricsPath)) {
          mData = JSON.parse(fs.readFileSync(metricsPath, "utf-8"));
        }
        mData.knowledge_usage = mData.knowledge_usage || { concepts_injected: 0 };
        mData.knowledge_usage.concepts_injected = (mData.knowledge_usage.concepts_injected || 0) + injectedCount;
        fs.writeFileSync(metricsPath, JSON.stringify(mData, null, 2), "utf-8");
      } catch (e) {
        // ignore
      }

      return instContent;
    } catch (e) {
      return `Warning: OEM runtime context unavailable.
Please run: oem session-start`;
    }
  }
}

function resolveRepoDir(): string {
  if (process.env.OPENEMPIRIC_DIR) {
    return process.env.OPENEMPIRIC_DIR
  }

  try {
    const currentFilePath = fileURLToPath(import.meta.url)
    const realFilePath = fs.realpathSync(currentFilePath)
    let dir = path.dirname(realFilePath)
    while (dir && dir !== path.parse(dir).root) {
      if (fs.existsSync(path.join(dir, "pyproject.toml")) && fs.existsSync(path.join(dir, "packages", "oem-knowledge"))) {
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
      try {
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
            "oem_knowledge.server"
          ],
          enabled: true,
          timeout: 60000,
          ...existingMcp,
          env: {
            ...(existingMcp.env || {})
          }
        }

        const tempInstPath = process.env.OEM_TEMP_INSTRUCTIONS ||
          path.join(os.homedir(), ".config", "opencode", "plugins", ".openempiric_temp_instructions.md")

        let instContent = "";
        try {
          const assembler = new ContextAssembler();
          const activeProject = config.directory || process.cwd();
          instContent = assembler.assemble(activeProject);
        } catch (e) {
          console.error("ContextAssembler failed:", e);
        }

        config.instructions = config.instructions || []
        try {
          fs.writeFileSync(tempInstPath, instContent || "# openempiric Session Context\n\nNo active context available.", "utf-8")
          if (!config.instructions.includes(tempInstPath)) {
            config.instructions.push(tempInstPath)
          }
        } catch (e) {
          console.error("Failed to write transient openempiric instructions:", e)
        }
      } catch (error) {
        console.error("openempiric plugin config hook failed, isolating error:", error);
      }
    }
  }
}
