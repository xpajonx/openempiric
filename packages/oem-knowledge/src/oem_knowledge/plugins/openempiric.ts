import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import * as path from "path"
import * as fs from "fs"
import * as os from "os"
import { fileURLToPath } from "url"
import { execFileSync } from "child_process"

// Import authoritative types from generated schema
import type {
  ConceptData as RegistryItem,
  TodoItem,
  RetrievalMetrics,
  ContextMetrics,
  KnowledgeUsageMetrics,
  MetricsSchema
} from "./generated/schemas"

type Registry = Record<string, RegistryItem>;

// ── Color Constants for TUI rendering ──────────────────────────────────────
const RESET = "\x1b[0m";
const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const BLUE = "\x1b[34m";
const MAGENTA = "\x1b[35m";
const CYAN = "\x1b[36m";

// ── Shared SearchStrategy Specification ─────────────────────────────────────
function levenshteinDistance(s1: string, s2: string): number {
  const s1_len = s1.length;
  const s2_len = s2.length;
  const dp: number[][] = Array.from({ length: s1_len + 1 }, () => Array(s2_len + 1).fill(0));
  for (let i = 0; i <= s1_len; i++) dp[i][0] = i;
  for (let j = 0; j <= s2_len; j++) dp[0][j] = j;
  for (let i = 1; i <= s1_len; i++) {
    for (let j = 1; j <= s2_len; j++) {
      if (s1[i - 1] === s2[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1];
      } else {
        dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 1);
      }
    }
  }
  return dp[s1_len][s2_len];
}

function stringSimilarity(s1: string, s2: string): number {
  const maxLen = Math.max(s1.length, s2.length);
  if (maxLen === 0) return 1.0;
  const dist = levenshteinDistance(s1.toLowerCase(), s2.toLowerCase());
  return (maxLen - dist) / maxLen;
}

// ── Metrics Persistence (Local TS helper) ────────────────────────────────────
function updateMetrics(projectPath: string, updates: {
  cache_hit?: boolean;
  cache_miss?: boolean;
  search_latency?: number;
  context_latency?: number;
  concepts_retrieved?: number;
}) {
  const metricsDir = path.join(projectPath, ".oem", "state");
  const metricsPath = path.join(metricsDir, "metrics.json");

  let data: MetricsSchema = {
    retrieval: {
      search_count: 0,
      search_latency_total: 0.0,
      search_latency_min: null,
      search_latency_max: null,
      last_search_latency: null,
      last_search_at: null,
      cache_hits: 0,
      cache_misses: 0,
      concepts_retrieved: 0
    },
    context: {
      context_count: 0,
      context_latency_total: 0.0,
      context_latency_min: null,
      context_latency_max: null,
      last_context_latency: null,
      last_context_at: null
    },
    knowledge_usage: {
      concepts_injected: 0,
      concepts_referenced: 0,
      concepts_ignored: 0,
      agent_decisions_aligned: 0,
      last_report_at: null
    }
  };

  try {
    if (fs.existsSync(metricsPath)) {
      const existing = JSON.parse(fs.readFileSync(metricsPath, "utf-8"));
      data = { ...data, ...existing };
      data.retrieval = { ...data.retrieval, ...existing.retrieval };
      data.context = { ...data.context, ...existing.context };
      data.knowledge_usage = { ...data.knowledge_usage, ...existing.knowledge_usage };
    }
  } catch (e) {
    // Ignore errors
  }

  const nowStr = new Date().toISOString();

  if (updates.cache_hit) {
    data.retrieval.cache_hits = (data.retrieval.cache_hits || 0) + 1;
  }
  if (updates.cache_miss) {
    data.retrieval.cache_misses = (data.retrieval.cache_misses || 0) + 1;
  }
  if (updates.search_latency !== undefined) {
    const lat = updates.search_latency;
    data.retrieval.search_count = (data.retrieval.search_count || 0) + 1;
    data.retrieval.search_latency_total = (data.retrieval.search_latency_total || 0.0) + lat;
    data.retrieval.search_latency_min = data.retrieval.search_latency_min === null ? lat : Math.min(data.retrieval.search_latency_min, lat);
    data.retrieval.search_latency_max = data.retrieval.search_latency_max === null ? lat : Math.max(data.retrieval.search_latency_max, lat);
    data.retrieval.last_search_latency = lat;
    data.retrieval.last_search_at = nowStr;
  }
  if (updates.concepts_retrieved !== undefined) {
    data.retrieval.concepts_retrieved = (data.retrieval.concepts_retrieved || 0) + updates.concepts_retrieved;
  }
  if (updates.context_latency !== undefined) {
    const lat = updates.context_latency;
    data.context.context_count = (data.context.context_count || 0) + 1;
    data.context.context_latency_total = (data.context.context_latency_total || 0.0) + lat;
    data.context.context_latency_min = data.context.context_latency_min === null ? lat : Math.min(data.context.context_latency_min, lat);
    data.context.context_latency_max = data.context.context_latency_max === null ? lat : Math.max(data.context.context_latency_max, lat);
    data.context.last_context_latency = lat;
    data.context.last_context_at = nowStr;
  }

  try {
    fs.mkdirSync(metricsDir, { recursive: true });
    fs.writeFileSync(metricsPath, JSON.stringify(data, null, 2), "utf-8");
  } catch (e) {
    // Ignore write errors
  }
}

// ── In-Memory RegistryCache (TS Native for retrieval performance) ───────────
class RegistryCache {
  private cache: Map<string, { data: Registry; mtime: number }> = new Map();

  getRegistry(projectPath: string): Registry {
    const oemDir = path.join(projectPath, ".oem");
    const regPath = path.join(oemDir, "concept_registry.json");
    if (!fs.existsSync(regPath)) {
      updateMetrics(projectPath, { cache_miss: true });
      return {};
    }
    const stat = fs.statSync(regPath);
    const cached = this.cache.get(regPath);
    if (cached && cached.mtime === stat.mtimeMs) {
      updateMetrics(projectPath, { cache_hit: true });
      return cached.data;
    }
    try {
      const data = JSON.parse(fs.readFileSync(regPath, "utf-8")) as Registry;
      this.cache.set(regPath, { data, mtime: stat.mtimeMs });
      updateMetrics(projectPath, { cache_miss: true });
      return data;
    } catch (e) {
      console.error("Failed to read registry cache:", e);
      updateMetrics(projectPath, { cache_miss: true });
      return cached ? cached.data : {};
    }
  }
}
const registryCache = new RegistryCache();

// ── Ranking Strategy Abstraction (TS Native) ───────────────────────────────
interface RankingStrategy {
  score(candidate: { id: string; item: RegistryItem; score: number }): number;
}

class Phase09RankingStrategy implements RankingStrategy {
  score(candidate: { id: string; item: RegistryItem; score: number }): number {
    const { item, score: similarity } = candidate;
    const confidence = item.confidence || 1;
    const evidence = item.evidence_count || 0;
    const failures = (item as any).failure_count || 0;
    const status = item.status || "candidate";

    const healthBoost = 0.05 * (confidence / 5.0) + 0.03 * Math.min(evidence / 10.0, 1.0) - 0.05 * Math.min(failures / 5.0, 1.0);

    let statusBoost = 0.0;
    if (status === "global" || status === "canonical") {
      statusBoost = 0.10;
    } else if (status === "validated") {
      statusBoost = 0.05;
    }

    const sessionCount = (item as any).session_count || 0;
    const usageBoost = 0.02 * Math.min(sessionCount / 10.0, 1.0);

    return similarity + healthBoost + statusBoost + usageBoost;
  }
}

const rankingStrategy = new Phase09RankingStrategy();

// ── TUI Render Panel Helpers ────────────────────────────────────────────────
function statusTag(status: string): string {
  const s = status.toUpperCase();
  if (s === "OK" || s === "SUCCESS" || s === "GREEN") return `${GREEN} SUCCESS${RESET}`;
  if (s === "SEARCH" || s === "SEARCHING" || s === "FIND") return `${BLUE} SEARCHING${RESET}`;
  if (s === "WRITE" || s === "WRITING" || s === "SAVE") return `${CYAN} WRITING${RESET}`;
  if (s === "STATS" || s === "INFO") return `${MAGENTA} STATS${RESET}`;
  if (s === "BOOTSTRAP" || s === "INIT" || s === "SETUP") return `${YELLOW} BOOTSTRAPPING${RESET}`;
  if (s === "ERROR" || s === "FAIL" || s === "FAILURE") return `${RED} ERROR${RESET}`;
  return `${CYAN} ${s}${RESET}`;
}

function renderPanel(title: string, lines: string[], status: string = "OK", width: number = 72): string {
  let borderTop = "╔" + "═".repeat(width - 2) + "╗";
  const borderBottom = "╚" + "═".repeat(width - 2) + "╝";

  const tag = statusTag(status);
  const headerText = `  ${title} | ${tag}  `;
  const cleanHeaderLength = headerText.replace(/\x1b\[\d+m/g, "").length;
  if (cleanHeaderLength < width - 6) {
    const paddingLeft = Math.floor((width - 2 - cleanHeaderLength) / 2);
    const paddingRight = width - 2 - cleanHeaderLength - paddingLeft;
    borderTop = "╔" + "═".repeat(paddingLeft) + headerText + "═".repeat(paddingRight) + "╗";
  }

  const panelLines = [borderTop];
  for (const line of lines) {
    let lineStr = String(line);
    while (lineStr.length > width - 4) {
      const chunk = lineStr.slice(0, width - 4);
      panelLines.push(`║ ${chunk.padEnd(width - 4)} ║`);
      lineStr = lineStr.slice(width - 4);
    }
    panelLines.push(`║ ${lineStr.padEnd(width - 4)} ║`);
  }

  panelLines.push(borderBottom);
  return panelLines.join("\n");
}

// ── Context Assembler (TS Native for session start performance) ─────────────
interface ContextBudget {
  conceptCountLimit: number;
  tokenBudgetLimit: number;
}

class ContextAssembler {
  private budget: ContextBudget;

  constructor(budget: ContextBudget = { conceptCountLimit: 5, tokenBudgetLimit: 1500 }) {
    this.budget = budget;
  }

  assemble(projectPath: string): string {
    const contextPath = process.env.OEM_RUNTIME_CONTEXT_PATH || path.join(os.homedir(), ".config", "opencode", "plugins", ".oem_runtime_context.json");
    if (fs.existsSync(contextPath)) {
      try {
        const oemContext = JSON.parse(fs.readFileSync(contextPath, "utf-8"));
        
        let instContent = `# OEM Runtime Notice
Project memory is already active. Relevant project memory has been restored automatically. Use OEM search when additional project context is needed.

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
        instContent += oemContext.memory_context || "OEM is your long-term memory for this project. Use this information when relevant. Do not assume work should continue unless the user requests it.\n";
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
        console.error("Failed to assemble context from context file, falling back to TS-native: ", e);
      }
    }

    const oemDir = path.join(projectPath, ".oem");
    const registry = registryCache.getRegistry(projectPath);


    // 1. Load active concepts from registry prioritizing validated/canonical/global
    const concepts: Array<{ id: string; name: string; description: string; score: number }> = [];
    for (const [cid, cdata] of Object.entries(registry)) {
      if (["validated", "canonical", "global"].includes(cdata.status)) {
        const score = rankingStrategy.score({ id: cid, item: cdata, score: 0.5 });
        
        let description = "";
        const wikiFile = path.join(oemDir, "wiki", `${cid}.md`);
        if (fs.existsSync(wikiFile)) {
          try {
            const text = fs.readFileSync(wikiFile, "utf-8");
            const body = text.replace(/^---[\s\S]*?---/, "").trim();
            description = body.split("\n")[0].replace(/^#.*?\n/, "").trim().slice(0, 150);
          } catch (e) {
            // ignore
          }
        }
        concepts.push({ id: cid, name: cdata.canonical_name, description, score });
      }
    }

    concepts.sort((a, b) => b.score - a.score);

    // 2. Read workspace state files directly
    const readList = (filename: string): string[] => {
      const fp = path.join(oemDir, filename);
      if (!fs.existsSync(fp)) return [];
      try {
        const text = fs.readFileSync(fp, "utf-8");
        return text.split("\n")
          .map(line => line.trim())
          .filter(line => line.startsWith("-"))
          .map(line => line.replace(/^-\s*(\[[ xX/]])?\s*/, "").trim());
      } catch (e) {
        return [];
      }
    };

    let activeGoals = readList("state/current-goals.md");
    const blockers = readList("state/open-issues.md");
    const discoveries = readList("state/active-decisions.md");

    let lastTopic = "General development";
    // Parse neutralized session-handoff
    const handoffPath = path.join(oemDir, "session-handoff.md");
    if (fs.existsSync(handoffPath)) {
      try {
        const text = fs.readFileSync(handoffPath, "utf-8");
        
        // Parse "Historical Context"
        const histMatch = text.match(/## Historical Context\s*\n\s*([^#]+)/);
        if (histMatch && histMatch[1]) {
          const firstLine = histMatch[1].trim().split("\n")[0].trim().replace(/^-\s*/, "");
          if (firstLine) {
            lastTopic = firstLine;
          }
        }
        
        // Parse "Previous Decisions"
        const decMatch = text.match(/## Previous Decisions\s*\n\s*([^#]+)/);
        if (decMatch && decMatch[1]) {
          const lines = decMatch[1].split("\n");
          for (const line of lines) {
            const clean = line.trim();
            if (clean.startsWith("-")) {
              const content = clean.replace(/^-\s*(\[[ xX/]])?\s*/, "").trim();
              if (content && !discoveries.includes(content)) {
                discoveries.push(content);
              }
            }
          }
        }
        
        // Parse "Open Questions"
        const qMatch = text.match(/## Open Questions\s*\n\s*([^#]+)/);
        if (qMatch && qMatch[1]) {
          const lines = qMatch[1].split("\n");
          for (const line of lines) {
            const clean = line.trim();
            if (clean.startsWith("-")) {
              const content = clean.replace(/^-\s*(\[[ xX/]])?\s*/, "").trim();
              if (content && !activeGoals.includes(content)) {
                activeGoals.push(content);
              }
            }
          }
        }

        // Support legacy Next Action as fallback
        if (lastTopic === "General development") {
          const m = text.match(/## Next Action\s*\n\s*(?:-\s*)?([^\n#]+)/);
          if (m && m[1]) {
            lastTopic = m[1].trim();
          }
        }
      } catch (e) {
        // Ignore
      }
    } else if (activeGoals.length > 0) {
      lastTopic = activeGoals[0];
      activeGoals = activeGoals.slice(1);
    }

    // 3. Format dynamic markdown instructions under budget constraints
    let instContent = `# OEM Runtime Notice
Project memory is already active. Relevant project memory has been restored automatically. Use OEM search when additional project context is needed.

# Previous Session Context

`;

    instContent += "## Last Topic\n";
    instContent += lastTopic + "\n\n";

    instContent += "## Recent Decisions\n";
    let budgetUsedChars = instContent.length;
    const charLimit = this.budget.tokenBudgetLimit * 4; // Approx 4 chars per token
    let decisionCount = 0;
    for (const d of discoveries.slice(0, 5)) {
      const decisionStr = `- ${d}\n`;
      if (budgetUsedChars + decisionStr.length > charLimit) break;
      instContent += decisionStr;
      budgetUsedChars += decisionStr.length;
      decisionCount++;
    }
    if (decisionCount === 0) {
      instContent += "- None\n";
    }

    instContent += "\n## Open Questions\n";
    let questionCount = 0;
    for (const q of activeGoals.slice(0, 5)) {
      const questionStr = `- ${q}\n`;
      if (budgetUsedChars + questionStr.length > charLimit) break;
      instContent += questionStr;
      budgetUsedChars += questionStr.length;
      questionCount++;
    }
    if (questionCount === 0) {
      instContent += "- None\n";
    }

    instContent += "\n## Active Concepts\n";
    let conceptCount = 0;
    const injectedIds: string[] = [];

    for (const c of concepts) {
      if (conceptCount >= this.budget.conceptCountLimit) break;
      const conceptStr = `- **${c.name}** (${c.id}): ${c.description || 'No description available.'}\n`;
      if (budgetUsedChars + conceptStr.length > charLimit) break;
      instContent += conceptStr;
      budgetUsedChars += conceptStr.length;
      conceptCount++;
      injectedIds.push(c.id);
    }
    if (conceptCount === 0) {
      instContent += "- None\n";
    }

    instContent += "\n## Relevant Failures\n";
    let failureCount = 0;
    for (const b of blockers.slice(0, 5)) {
      const failureStr = `- ${b}\n`;
      if (budgetUsedChars + failureStr.length > charLimit) break;
      instContent += failureStr;
      budgetUsedChars += failureStr.length;
      failureCount++;
    }
    if (failureCount === 0) {
      instContent += "- None\n";
    }

    // 4. Memory context: frame OEM as long-term memory
    instContent += "\n## Memory Context\n";
    instContent += "OEM is your long-term memory for this project. ";
    instContent += "Use this information when relevant. ";
    instContent += "Do not assume work should continue unless the user requests it.\n";

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
    // Perform inline metrics updates directly to avoid python startup latency in config hook
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
  }
}

// ── Repo Dir Resolver ───────────────────────────────────────────────────────
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

// ── CLI Wrapper Execution Helper ───────────────────────────────────────────
function runOemCli(repoDir: string, args: string[], projectPath?: string): string {
  const execArgs = ["run", "oem", ...args];
  if (projectPath) {
    execArgs.push("--project", projectPath);
  }
  try {
    return execFileSync("uv", execArgs, { cwd: repoDir, encoding: "utf-8" }).trim();
  } catch (e: any) {
    const errMsg = e.stderr ? e.stderr.toString() : e.message;
    return renderPanel("Execution Error", [`Failed to run oem CLI:`, errMsg], "error");
  }
}

export const OpenempiricPlugin: Plugin = async ({ $ }) => {
  const repoDir = resolveRepoDir()
  return {
    config: async (config) => {
      try {
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
            "oem_knowledge.server"
          ],
          enabled: true,
          timeout: 60000,
          ...existingMcp,
          env: {
            ...(existingMcp.env || {})
          }
        }

        // 2. Read context using ContextAssembler dynamically from workspace
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
    },

    // ── TypeScript Tools Definition ─────────────────────────────────────────
    tool: {
      knowledge_search: tool({
        description: "Fast TS-native registry-first lookup and term-based search across concepts.",
        args: {
          query: tool.schema.string().describe("Search query"),
          k: tool.schema.number().optional().default(3).describe("Number of results to return"),
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ query, k, project }, context) {
          const startTime = performance.now();
          const root = project || context.directory || process.cwd();
          const registry = registryCache.getRegistry(root);
          const normalizedQuery = query.toLowerCase().trim();

          const candidates: Array<{ id: string; item: RegistryItem; score: number }> = [];

          for (const [id, item] of Object.entries(registry)) {
            const canonical = item.canonical_name.toLowerCase();
            let score = 0;

            if (canonical === normalizedQuery) {
              score = 1.0;
            } else {
              const exactAlias = item.aliases.some(a => a.toLowerCase() === normalizedQuery);
              if (exactAlias) {
                score = 0.85;
              } else {
                let maxSim = 0;
                const terms = [canonical, ...item.aliases];
                for (const term of terms) {
                  const sim = stringSimilarity(normalizedQuery, term);
                  if (sim > maxSim) maxSim = sim;
                }
                if (maxSim >= 0.80) {
                  score = 0.50 + 0.35 * maxSim;
                }
              }
            }

            if (score > 0) {
              candidates.push({ id, item, score });
            }
          }

          candidates.sort((a, b) => b.score - a.score);
          const topCandidates = candidates.slice(0, 5);

          const results: Array<{ rel_path: string; snippet: string; score: number }> = [];
          for (const cand of topCandidates) {
            const wikiFile = path.join(root, ".oem", "wiki", `${cand.id}.md`);
            let fileBoost = 0;
            let snippet = "No content available.";
            if (fs.existsSync(wikiFile)) {
              try {
                const content = fs.readFileSync(wikiFile, "utf-8");
                snippet = content.replace(/^---[\s\S]*?---/, "").trim().slice(0, 150).replace(/\n/g, " ");
                const queryTerms = normalizedQuery.split(/\s+/);
                const contentLower = content.toLowerCase();
                let matchCount = 0;
                for (const term of queryTerms) {
                  if (contentLower.includes(term)) {
                    matchCount++;
                  }
                }
                if (queryTerms.length > 0) {
                  fileBoost = 0.15 * (matchCount / queryTerms.length);
                }
              } catch (e) {
                // Ignore
              }
            }
            results.push({
              rel_path: path.join(".oem", "wiki", `${cand.id}.md`),
              snippet,
              score: rankingStrategy.score({ id: cand.id, item: cand.item, score: cand.score + fileBoost })
            });
          }

          results.sort((a, b) => b.score - a.score);
          const finalResults = results.slice(0, k || 3);

          const duration = performance.now() - startTime;
          updateMetrics(root, { search_latency: duration, concepts_retrieved: finalResults.length });

          if (finalResults.length === 0) {
            return renderPanel(`Search: 0 results`, [`No matches for: '${query}'`], "search");
          }

          const lines = [`Query: "${query}"`, `Results: ${finalResults.length}`, ""];
          finalResults.forEach((r, idx) => {
            lines.push(`${idx + 1}. [${r.rel_path}] (score: ${r.score.toFixed(4)})`);
            lines.push(`   ${r.snippet}...`);
            lines.push("");
          });
          return renderPanel("Knowledge Search Results", lines, "search");
        }
      }),



      knowledge_health_check: tool({
        description: "Scan the knowledge base for stale concepts, duplicate concepts (merge proposals), and architectural contradictions.",
        args: {
          stale_sessions: tool.schema.number().optional().default(5).describe("Number of sessions threshold to consider a concept stale"),
          similarity_threshold: tool.schema.number().optional().default(0.85).describe("Similarity threshold to propose merges"),
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ stale_sessions, similarity_threshold, project }, context) {
          const root = project || context.directory || process.cwd();
          const cmdArgs = ["health", "--stale-sessions", String(stale_sessions), "--similarity-threshold", String(similarity_threshold)];
          return runOemCli(repoDir, cmdArgs, root);
        }
      }),

      knowledge_stats: tool({
        description: "Show oem/ knowledge statistics.",
        args: {
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ project }, context) {
          const root = project || context.directory || process.cwd();
          const oemDir = path.join(root, ".oem");
          const registry = registryCache.getRegistry(root);

          let dbSize = 0;
          const dbPath = path.join(oemDir, ".local_vector_db");
          if (fs.existsSync(dbPath)) {
            const getFilesSize = (dir: string): number => {
              let size = 0;
              for (const f of fs.readdirSync(dir)) {
                const fp = path.join(dir, f);
                const stat = fs.statSync(fp);
                if (stat.isDirectory()) {
                  size += getFilesSize(fp);
                } else {
                  size += stat.size;
                }
              }
              return size;
            };
            try {
              dbSize = getFilesSize(dbPath);
            } catch (e) {
              // Ignore
            }
          }

          const lines = [
            `Total Concepts:       ${Object.keys(registry).length}`,
            `Vector DB Size:       ${(dbSize / (1024 * 1024)).toFixed(2)} MB`,
            `OEM Path:             ${oemDir}`
          ];
          return renderPanel("Knowledge Stats", lines, "stats");
        }
      }),

      knowledge_explain_concept: tool({
        description: "Explain a concept and its details from registry.",
        args: {
          concept_id: tool.schema.string().describe("Concept ID (e.g. concept_001)"),
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ concept_id, project }, context) {
          const root = project || context.directory || process.cwd();
          const registry = registryCache.getRegistry(root);
          const cdata = registry[concept_id];

          if (!cdata) {
            return renderPanel("Concept Not Found", [`Concept ${concept_id} not in registry.`], "error");
          }

          const wikiFile = path.join(root, ".oem", "wiki", `${concept_id}.md`);
          let recentEvidence: string[] = [];
          if (fs.existsSync(wikiFile)) {
            try {
              const content = fs.readFileSync(wikiFile, "utf-8");
              const evMatch = content.match(/## Learnings.*?\n([\s\S]*)/);
              if (evMatch && evMatch[1]) {
                recentEvidence = evMatch[1].split("\n")
                  .map(line => line.trim())
                  .filter(line => line.startsWith("-"))
                  .map(line => line.replace(/^-\s*/, "").trim());
              }
            } catch (e) {
              // Ignore
            }
          }

          const lines = [
            `Concept: ${cdata.canonical_name.replace(/-/g, " ").toUpperCase()} (${concept_id})`,
            `Status: ${cdata.status.toUpperCase()}`,
            `Confidence: ${cdata.confidence || 1}/5`,
            `Aliases: ${cdata.aliases.join(", ")}`,
            "",
            `Recent Evidence:`,
            ...(recentEvidence.length > 0 ? recentEvidence.map(e => `  - ${e}`) : ["  - None"])
          ];
          return renderPanel("Concept Explanation", lines, "ok");
        }
      }),

      knowledge_graph_query: tool({
        description: "Query semantic relationships for a concept.",
        args: {
          concept_id: tool.schema.string().describe("Target concept ID"),
          direction: tool.schema.string().optional().default("both").describe("incoming, outgoing, or both"),
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ concept_id, direction, project }, context) {
          const root = project || context.directory || process.cwd();
          const registry = registryCache.getRegistry(root);
          const cdata = registry[concept_id];

          if (!cdata) {
            return renderPanel("Query Error", [`Concept ${concept_id} not found.`], "error");
          }

          const lines = [
            `Concept: ${cdata.canonical_name.replace(/-/g, " ").toUpperCase()} (${concept_id})`,
            ""
          ];

          if (direction === "outgoing" || direction === "both") {
            lines.push("Outgoing Relationships:");
            const relationships = cdata.relationships || [];
            relationships.forEach(r => {
              const targetName = registry[r.target]?.canonical_name || r.target;
              lines.push(`  - [${r.type}] -> ${targetName} (${r.target})`);
            });
            if (relationships.length === 0) lines.push("  - None");
            lines.push("");
          }

          if (direction === "incoming" || direction === "both") {
            lines.push("Incoming Relationships:");
            let incomingCount = 0;
            for (const [cid, data] of Object.entries(registry)) {
              if (cid === concept_id) continue;
              const relationships = data.relationships || [];
              relationships.forEach(r => {
                if (r.target === concept_id) {
                  lines.push(`  - ${data.canonical_name} (${cid}) -> [${r.type}]`);
                  incomingCount++;
                }
              });
            }
            if (incomingCount === 0) lines.push("  - None");
          }

          return renderPanel("Graph Query Results", lines, "ok");
        }
      }),

      // ── Python-delegated Authoritative Mutation Tools ──────────────────────
      oem_todo_read: tool({
        description: "Read the current todo list from .oem/state/todos.json.",
        args: {
          workdir: tool.schema.string().optional().describe("Project directory")
        },
        async execute({ workdir }, context) {
          const root = workdir || context.directory || process.cwd();
          return runOemCli(repoDir, ["todo", "read"], root);
        }
      }),

      oem_todo_write: tool({
        description: "Replace the current todo list with new items.",
        args: {
          items: tool.schema.string().describe("JSON array of todo item objects"),
          workdir: tool.schema.string().optional().describe("Project directory")
        },
        async execute({ items, workdir }, context) {
          const root = workdir || context.directory || process.cwd();
          return runOemCli(repoDir, ["todo", "write", items], root);
        }
      }),

      oem_todo_advance: tool({
        description: "Update one todo item's status.",
        args: {
          item_id: tool.schema.string().describe("The item's UUID"),
          status: tool.schema.string().optional().describe("New status (pending, in_progress, completed)"),
          workdir: tool.schema.string().optional().describe("Project directory")
        },
        async execute({ item_id, status, workdir }, context) {
          const root = workdir || context.directory || process.cwd();
          const cmdArgs = ["todo", "advance", item_id];
          if (status) {
            cmdArgs.push("--status", status);
          }
          return runOemCli(repoDir, cmdArgs, root);
        }
      }),

      knowledge_usage_report: tool({
        description: "Report concept usage and decision alignment for the session. (Experimental/Low-Confidence Telemetry)",
        args: {
          concepts_used: tool.schema.array(tool.schema.string()).describe("Concept IDs referenced during this session"),
          concepts_ignored: tool.schema.array(tool.schema.string()).optional().describe("Concept IDs ignored/not used during this session (optional, auto-derived if omitted)"),
          decisions: tool.schema.array(tool.schema.string()).optional().describe("Decisions aligned with concepts (optional)"),
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ concepts_used, concepts_ignored, decisions, project }, context) {
          const root = project || context.directory || process.cwd();
          const cmdArgs = ["metrics", "--report", "--used", JSON.stringify(concepts_used)];
          if (concepts_ignored) {
            cmdArgs.push("--ignored", JSON.stringify(concepts_ignored));
          }
          if (decisions) {
            cmdArgs.push("--decisions", JSON.stringify(decisions));
          }
          return runOemCli(repoDir, cmdArgs, root);
        }
      })
    }
  }
}
