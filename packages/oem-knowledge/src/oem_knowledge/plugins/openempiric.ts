// generated_by: openempiric
// source_type: oem_opencode_plugin
// This file is managed by `oem setup opencode`.

import type { Plugin, Hooks } from "@opencode-ai/plugin"
import * as path from "path"
import * as fs from "fs"
import * as os from "os"
import { fileURLToPath } from "url"
import { spawnSync } from "child_process"

interface PreflightConfig {
  enabled: boolean;
  writeAudit: boolean;
}

function getPreflightConfig(projectRoot: string): PreflightConfig {
  const config: PreflightConfig = { enabled: false, writeAudit: true };
  if (process.env.OEM_PREFLIGHT_AUTOMATIC === "1") {
    config.enabled = true;
  }
  if (process.env.OEM_PREFLIGHT_AUDIT === "0") {
    config.writeAudit = false;
  }
  
  // Parse from config files
  const configPaths = [
    path.join(projectRoot, ".oem", "config.json"),
    path.join(projectRoot, ".oem", "preflight", "config.json")
  ];
  
  for (const configPath of configPaths) {
    try {
      if (fs.existsSync(configPath)) {
        const raw = fs.readFileSync(configPath, "utf-8");
        const parsed = JSON.parse(raw);
        if (parsed && parsed.preflight && parsed.preflight.automatic) {
          const auto = parsed.preflight.automatic;
          if (auto.enabled !== undefined) {
            config.enabled = !!auto.enabled;
          }
          if (auto.write_audit !== undefined) {
            config.writeAudit = !!auto.write_audit;
          }
        }
      }
    } catch (e) {
      // Ignore
    }
  }
  
  return config;
}

function isTrivialPrompt(prompt: string): boolean {
  const normalized = prompt.trim().toLowerCase();
  if (!normalized) return true;
  
  const trivialPatterns = [
    /^thanks$/,
    /^ok$/,
    /^yes$/,
    /^continue$/,
    /^fix typo$/,
    /^run it$/,
    /^no$/,
    /^sure$/,
    /^please$/,
    /^hello$/,
    /^hi$/,
  ];
  
  if (trivialPatterns.some(pat => pat.test(normalized))) {
    return true;
  }
  
  const taskKeywords = [
    "implement", "fix", "refactor", "write", "create", "analyze", "review",
    "debug", "plan", "task", "concept", "build", "change", "update", "add", "make"
  ];
  
  const hasTaskKeyword = taskKeywords.some(kw => normalized.includes(kw));
  if (normalized.length < 8 && !hasTaskKeyword) {
    return true;
  }
  
  return false;
}

function extractPromptText(msgInput: any): string {
  if (!msgInput) return "";
  if (typeof msgInput === "string") return msgInput;
  if (msgInput.content && typeof msgInput.content === "string") return msgInput.content;
  if (msgInput.text && typeof msgInput.text === "string") return msgInput.text;
  if (msgInput.prompt && typeof msgInput.prompt === "string") return msgInput.prompt;
  if (Array.isArray(msgInput.parts)) {
    return msgInput.parts.map((p: any) => typeof p === "string" ? p : (p.text || "")).join(" ");
  }
  return "";
}

function safelyDeletePreflightContextFile(projectRoot: string): void {
  const preflightMdPath = path.join(projectRoot, ".oem", ".runtime", "preflight_context.md");
  if (fs.existsSync(preflightMdPath)) {
    try {
      const content = fs.readFileSync(preflightMdPath, "utf-8");
      if (content.includes("generated_by: openempiric") && content.includes("source_type: oem_preflight_runtime")) {
        fs.unlinkSync(preflightMdPath);
      }
    } catch (e) {
      // Ignore
    }
  }
}

function executePreflightCli(projectRoot: string, task: string, repoDir: string | null, writeAudit: boolean): any {
  const truncatedTask = task.slice(0, 1000);
  
  let cmd: string;
  let args: string[];
  
  if (repoDir && fs.existsSync(path.join(repoDir, "pyproject.toml"))) {
    cmd = "uv";
    args = [
      "run",
      "--directory",
      repoDir,
      "oem",
      "preflight",
      truncatedTask,
      "--project",
      projectRoot,
      "--json"
    ];
  } else {
    cmd = "oem";
    args = [
      "preflight",
      truncatedTask,
      "--project",
      projectRoot,
      "--json"
    ];
  }

  if (!writeAudit) {
    args.push("--no-audit");
  }
  
  try {
    const res = spawnSync(cmd, args, {
      encoding: "utf-8",
      env: process.env,
      shell: false,
      timeout: 3000,
      maxBuffer: 10 * 1024 * 1024
    });
    if (res.status === 0 && res.stdout) {
      return JSON.parse(res.stdout);
    } else {
      console.warn("Preflight CLI execution failed:", res.stderr || res.stdout);
    }
  } catch (e: any) {
    console.warn("Error running preflight CLI:", e.message);
  }
  return null;
}

function findActiveProjectRoot(): string | null {
  if (process.env.OEM_PROJECT_ROOT) {
    return path.resolve(process.env.OEM_PROJECT_ROOT);
  }
  const activeProject = process.cwd();
  return findOemProjectRoot(activeProject);
}

function redactSecrets(text: string): string {
  if (!text) return "";
  let redacted = text;
  // Redact API keys: e.g., sk-proj-... or similar patterns
  redacted = redacted.replace(/(?:api_key|apikey|secret|password|token|auth|authorization)(?:\s*[:=]\s*|["']\s*[:=]\s*["']?\s*)["']?([a-zA-Z0-9_\-\.\/]{8,})["']?/gi, (match, p1) => {
    return match.replace(p1, "[REDACTED]");
  });
  // Redact bearer tokens
  redacted = redacted.replace(/bearer\s+([a-zA-Z0-9_\-\.\/]+)/gi, "Bearer [REDACTED]");
  return redacted;
}

function limitSnippet(text: string, maxLen: number = 200): string {
  if (!text) return "";
  const redacted = redactSecrets(text);
  if (redacted.length <= maxLen) return redacted;
  return redacted.slice(0, maxLen) + "... [Truncated]";
}

function findOemProjectRoot(startDir: string): string | null {
  let dir = path.resolve(startDir);
  while (dir && dir !== path.parse(dir).root) {
    if (fs.existsSync(path.join(dir, ".oem"))) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

class ContextAssembler {
  private lastRebuildTime: number = 0;

  assemble(projectPath: string, force: boolean = false): string {
    const projectRoot = findOemProjectRoot(projectPath);
    if (!projectRoot) {
      return "# OEM Notice\nProject memory not initialized (.oem folder not found).";
    }

    const oemDir = path.join(projectRoot, ".oem");
    const runtimeDir = path.join(oemDir, ".runtime");
    const contextMdPath = path.join(runtimeDir, "context.md");

    const now = Date.now();
    if (!force && fs.existsSync(contextMdPath) && (now - this.lastRebuildTime) < 30000) {
      try {
        return fs.readFileSync(contextMdPath, "utf-8");
      } catch (e) {
        // Fallback to rebuilding
      }
    }

    this.lastRebuildTime = now;

    try {
      // 1. Resolve canonical files with tolerant lookup
      const eventsPath = fs.existsSync(path.join(oemDir, "runtime_events.jsonl"))
        ? path.join(oemDir, "runtime_events.jsonl")
        : path.join(oemDir, "events.jsonl");

      const outcomesPath = fs.existsSync(path.join(oemDir, "outcomes.jsonl"))
        ? path.join(oemDir, "outcomes.jsonl")
        : path.join(oemDir, "state", "outcomes.jsonl");

      const registryPath = path.join(oemDir, "concept_registry.json");
      const skillsDir = path.join(oemDir, "skills");

      // 2. Lightweight checks (readability/existence)
      const warnings: string[] = [];
      if (!fs.existsSync(registryPath)) {
        warnings.push("Concept registry file missing.");
      }
      if (!fs.existsSync(eventsPath)) {
        warnings.push("Events log file missing.");
      }

      // 3. Extract recent decisions & failures
      const decisions: string[] = [];
      const failures: string[] = [];
      if (fs.existsSync(eventsPath)) {
        try {
          const content = fs.readFileSync(eventsPath, "utf-8");
          const lines = content.split(/\r?\n/).filter(line => line.trim());
          for (let i = lines.length - 1; i >= 0; i--) {
            try {
              const ev = JSON.parse(lines[i]);
              const type = ev.event_type || ev.type;
              const evidence = ev.evidence || ev.summary || "";
              if (type === "decision" && decisions.length < 5 && !decisions.includes(evidence)) {
                decisions.push(evidence);
              }
              if (type === "failure" && failures.length < 5 && !failures.includes(evidence)) {
                failures.push(evidence);
              }
            } catch (e) {
              // Ignore
            }
            if (decisions.length >= 5 && failures.length >= 5) {
              break;
            }
          }
        } catch (e) {
          warnings.push("Failed to read events log.");
        }
      }

      // 4. Load approved skills
      const approvedSkills: string[] = [];
      if (fs.existsSync(skillsDir) && fs.statSync(skillsDir).isDirectory()) {
        try {
          const files = fs.readdirSync(skillsDir);
          for (const file of files) {
            if (file.endsWith(".md")) {
              const p = path.join(skillsDir, file);
              const data = fs.readFileSync(p, "utf-8");
              const match = data.match(/^#\s+(.+)$/m);
              if (match) {
                approvedSkills.push(match[1].trim());
              } else {
                approvedSkills.push(path.basename(file, ".md"));
              }
            }
          }
        } catch (e) {
          warnings.push("Failed to read skills directory.");
        }
      }

      // 5. Read outcomes for last session summary
      let lastSessionStatus = "No prior session logged";
      if (fs.existsSync(outcomesPath)) {
        try {
          const content = fs.readFileSync(outcomesPath, "utf-8");
          const lines = content.split(/\r?\n/).filter(line => line.trim());
          if (lines.length > 0) {
            const lastOutcome = JSON.parse(lines[lines.length - 1]);
            const status = lastOutcome.outcome || lastOutcome.status || "unknown";
            const timestamp = lastOutcome.timestamp || "";
            lastSessionStatus = `${status.toUpperCase()} (${timestamp})`;
          }
        } catch (e) {
          // Ignore
        }
      }

      // 6. Build the dynamic instruction content
      let md = `# OEM Runtime Context

OpenEmpiric project memory is active for this repository.

Lifecycle:
1. Before planning a non-trivial task, check \`.oem/.runtime/preflight_context.md\` if it exists. If it contains an OEM Preflight Context with decision \`required\`, follow it before planning. If stale or absent, call \`knowledge_preflight\` directly.
2. If preflight returns \`required\`, follow the returned OEM context before planning.
3. If preflight returns \`suggest\`, consider the returned context and optionally use \`knowledge_search\` or \`knowledge_source_search\`.
4. Use \`knowledge_session_start\` when beginning work.
5. Use \`knowledge_read\` whenever you need orientation, project background, recent context, conventions, or approved skills.
6. Use \`knowledge_search\` when you have a specific memory query.
7. Use \`knowledge_reflect\` to record important decisions, failures, constraints, risks, and outcomes.
8. Use \`knowledge_session_end\` before finishing.

Current memory baseline:
`;

      md += `* **Last Session Status**: ${lastSessionStatus}\n`;
      
      md += `* **Recent Decisions**:\n`;
      if (decisions.length > 0) {
        decisions.forEach(d => { md += `  - ${d}\n`; });
      } else {
        md += `  - None\n`;
      }

      md += `* **Approved Skills**:\n`;
      if (approvedSkills.length > 0) {
        approvedSkills.forEach(s => { md += `  - ${s}\n`; });
      } else {
        md += `  - None\n`;
      }

      md += `* **Recent Failures**:\n`;
      if (failures.length > 0) {
        failures.forEach(f => { md += `  - ${f}\n`; });
      } else {
        md += `  - None\n`;
      }

      if (warnings.length > 0) {
        md += `\n* **Runtime Warnings**:\n`;
        warnings.forEach(w => { md += `  - ${w}\n`; });
      }

      md += `\nRules:
- Do not manually edit \`.oem\` files.
- Do not use \`knowledge_index\` as a fallback for failed reflection.
- Do not treat the source corpus as learned memory.
- Prefer structured events or explicit markers for reflection.
- If OEM health is degraded, report it and suggest \`oem doctor\` or \`oem recover\`.
`;

      try {
        fs.mkdirSync(runtimeDir, { recursive: true });
        fs.writeFileSync(contextMdPath, md, "utf-8");
      } catch (e) {
        // Ignore
      }

      return md;
    } catch (error: any) {
      return `# OEM Runtime Context\n\nWarning: Failed to compile context: ${error.message}`;
    }
  }
}

function resolveRepoDir(): string | null {
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
    path.join(home, ".config", "openempiric-dev"),
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

  return null
}

export const OpenempiricPlugin: Plugin = async (input, options) => {
  const repoDir = resolveRepoDir()
  const assembler = new ContextAssembler()

  return {
    config: async (config) => {
      try {
        const existingMcp = config.mcp?.openempiric || {}
        config.mcp = config.mcp || {}
        
        let mcpCmd: string | string[]
        let mcpArgs: string[] | undefined
        
        if (repoDir && fs.existsSync(path.join(repoDir, "pyproject.toml"))) {
          mcpCmd = [
            "uv",
            "run",
            "--directory",
            repoDir,
            "python",
            "-m",
            "oem_knowledge.server"
          ]
          mcpArgs = undefined
        } else {
          mcpCmd = "oem"
          mcpArgs = ["mcp"]
        }

        config.mcp.openempiric = {
          type: "local",
          command: mcpCmd,
          ...(mcpArgs ? { args: mcpArgs } : {}),
          enabled: true,
          timeout: 60000,
          ...existingMcp,
          env: {
            ...(existingMcp.env || {})
          }
        }

        const activeProject = config.directory || process.cwd()
        const projectRoot = findOemProjectRoot(activeProject)
        
        if (projectRoot) {
          const instContent = assembler.assemble(projectRoot, true)
          const contextMdPath = path.join(projectRoot, ".oem", ".runtime", "context.md")
          
          config.instructions = config.instructions || []
          if (!config.instructions.includes(contextMdPath)) {
            config.instructions.push(contextMdPath)
          }

          const preflightConfig = getPreflightConfig(projectRoot)
          if (preflightConfig.enabled) {
            const preflightMdPath = path.join(projectRoot, ".oem", ".runtime", "preflight_context.md")
            if (!fs.existsSync(preflightMdPath)) {
              try {
                fs.mkdirSync(path.dirname(preflightMdPath), { recursive: true })
                fs.writeFileSync(preflightMdPath, 
                  `<!-- generated_by: openempiric -->\n` +
                  `<!-- source_type: oem_preflight_runtime -->\n` +
                  `<!-- ingestion_eligible: false -->\n` +
                  `# OEM Preflight Context\n\nStatus: pending\nReason: no user prompt processed yet\n\nBefore planning a non-trivial task, use the latest prompt-specific preflight context.\n`, 
                  "utf-8"
                )
              } catch (e) {}
            }
            if (!config.instructions.includes(preflightMdPath)) {
              config.instructions.push(preflightMdPath)
            }
          } else {
            safelyDeletePreflightContextFile(projectRoot)
          }
        }
      } catch (error) {
        console.error("openempiric plugin config hook failed, isolating error:", error)
      }
    },

    "tui.prompt.append": async (msgInput, msgOutput) => {
      try {
        const projectRoot = findActiveProjectRoot()
        if (!projectRoot) return

        const preflightConfig = getPreflightConfig(projectRoot)
        if (!preflightConfig.enabled) {
          safelyDeletePreflightContextFile(projectRoot)
          return
        }

        const promptText = extractPromptText(msgInput)
        if (!promptText) return

        if (isTrivialPrompt(promptText)) {
          safelyDeletePreflightContextFile(projectRoot)
          return
        }

        const repoDir = resolveRepoDir()
        const result = executePreflightCli(projectRoot, promptText, repoDir, preflightConfig.writeAudit)
        
        const preflightMdPath = path.join(projectRoot, ".oem", ".runtime", "preflight_context.md")
        if (result && result.context) {
          const formattedContext = 
            `<!-- generated_by: openempiric -->\n` +
            `<!-- source_type: oem_preflight_runtime -->\n` +
            `<!-- ingestion_eligible: false -->\n` +
            result.context;

          // Attempt direct prompt injection/append
          let injected = false;
          if (msgInput && typeof msgInput === "object") {
            const oemContextBlock = `\n\n[OEM Preflight Context]\n${result.context}`;
            if (typeof msgInput.content === "string") {
              msgInput.content += oemContextBlock;
              injected = true;
            } else if (typeof msgInput.text === "string") {
              msgInput.text += oemContextBlock;
              injected = true;
            }
          }

          // Write file fallback
          fs.mkdirSync(path.dirname(preflightMdPath), { recursive: true })
          fs.writeFileSync(preflightMdPath, formattedContext, "utf-8")
        } else {
          safelyDeletePreflightContextFile(projectRoot)
        }

        // Always update general context assembler
        assembler.assemble(projectRoot, false)
      } catch (error) {
        console.warn("openempiric tui.prompt.append hook failed dynamically:", error)
      }
    },

    "tool.execute.after": async (toolInput, toolOutput) => {
      try {
        const activeProject = process.cwd()
        const projectRoot = findOemProjectRoot(activeProject)
        if (!projectRoot) return

        const toolName = toolInput.tool || ""
        const isCommand = toolName.includes("command") || toolName.includes("shell") || toolName.includes("bash") || toolName.includes("execute")
        
        if (isCommand) {
          const cmdText = toolInput.args?.command || toolInput.args?.script || ""
          const outText = toolOutput.output || ""
          const metadata = toolOutput.metadata || {}
          const exitCode = metadata.exitCode !== undefined ? metadata.exitCode : (metadata.status || 0)
          
          let eventType = "observation"
          let summary = `Command execution: ${cmdText}`
          const isFailing = exitCode !== 0 || outText.toLowerCase().includes("failed") || outText.toLowerCase().includes("error")
          
          if (isFailing) {
            eventType = "failure"
            summary = `Command failed: ${cmdText}`
          }
          
          const evidence = `Command \`${cmdText}\` executed with exit code ${exitCode}.\nOutput: ${limitSnippet(outText, 200)}`
          
          const pendingEvent = {
            event_type: eventType,
            summary: redactSecrets(summary),
            evidence: redactSecrets(evidence),
            source: "opencode_hook",
            source_type: "agent_runtime_signal",
            ingestion_eligible: true,
            durable: false,
            timestamp: new Date().toISOString()
          }
          
          const pendingFile = path.join(projectRoot, ".oem", ".runtime", "pending_events.jsonl")
          fs.mkdirSync(path.dirname(pendingFile), { recursive: true })
          fs.appendFileSync(pendingFile, JSON.stringify(pendingEvent) + "\n", "utf-8")
        }
      } catch (error) {
        console.warn("openempiric tool.execute.after hook failed dynamically:", error)
      }
    }
  }
}
