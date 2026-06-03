import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import * as path from "path"
import * as fs from "fs"
import * as os from "os"
import { fileURLToPath } from "url"

// ── Color Constants for TUI rendering ──────────────────────────────────────
const RESET = "\x1b[0m";
const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const YELLOW = "\x1b[33m";
const BLUE = "\x1b[34m";
const MAGENTA = "\x1b[35m";
const CYAN = "\x1b[36m";

// ── Shared SearchStrategy Specification ─────────────────────────────────────
/**
 * SearchStrategy definition:
 * 1. Normalize query (lowercase, alphanumeric characters).
 * 2. Search concept registry first:
 *    - Score = 1.0 for exact canonical name matches.
 *    - Score = 0.85 for alias matches.
 *    - Score = 0.50 + 0.35 * similarity for fuzzy matches (SequenceMatcher/Levenshtein ratio >= 0.80).
 * 3. Filter top candidates (max 5 candidates).
 * 4. Read wiki markdown files ONLY for those top candidates.
 * 5. Boost score by +0.15 for matching query terms in file contents (TF-IDF/simple term frequency).
 * 6. Sort results descending by score and return top K.
 */
function levenshteinDistance(s1: string, s2: string): number {
  const m = s1.length;
  const n = s2.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  for (let i = 0; i <= m; i++) dp[i][0] = i;
  for (let j = 0; j <= n; j++) dp[0][j] = j;
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (s1[i - 1] === s2[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1];
      } else {
        dp[i][j] = Math.min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + 1);
      }
    }
  }
  return dp[m][n];
}

function stringSimilarity(s1: string, s2: string): number {
  const maxLen = Math.max(s1.length, s2.length);
  if (maxLen === 0) return 1.0;
  const dist = levenshteinDistance(s1.toLowerCase(), s2.toLowerCase());
  return (maxLen - dist) / maxLen;
}

// ── Metrics Definitions & Persistence ─────────────────────────────────────────
interface RetrievalMetrics {
  search_count: number;
  search_latency_total: number;
  search_latency_min: number | null;
  search_latency_max: number | null;
  last_search_latency: number | null;
  last_search_at: string | null;
  cache_hits: number;
  cache_misses: number;
  concepts_retrieved: number;
}

interface ContextMetrics {
  context_count: number;
  context_latency_total: number;
  context_latency_min: number | null;
  context_latency_max: number | null;
  last_context_latency: number | null;
  last_context_at: string | null;
}

interface KnowledgeUsageMetrics {
  concepts_injected: number;
  concepts_referenced: number;
  concepts_ignored: number;
  agent_decisions_aligned: number;
  last_report_at: string | null;
}

interface MetricsSchema {
  retrieval: RetrievalMetrics;
  context: ContextMetrics;
  knowledge_usage: KnowledgeUsageMetrics;
  [key: string]: any;
}

function updateMetrics(projectPath: string, updates: {
  cache_hit?: boolean;
  cache_miss?: boolean;
  search_latency?: number;
  context_latency?: number;
  concepts_retrieved?: number;
  concepts_injected?: number;
  concepts_referenced?: number;
  concepts_ignored?: number;
  agent_decisions_aligned?: number;
  last_report_at?: string | null;
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
  if (updates.concepts_injected !== undefined) {
    data.knowledge_usage.concepts_injected = (data.knowledge_usage.concepts_injected || 0) + updates.concepts_injected;
  }
  if (updates.concepts_referenced !== undefined) {
    data.knowledge_usage.concepts_referenced = (data.knowledge_usage.concepts_referenced || 0) + updates.concepts_referenced;
  }
  if (updates.concepts_ignored !== undefined) {
    data.knowledge_usage.concepts_ignored = (data.knowledge_usage.concepts_ignored || 0) + updates.concepts_ignored;
  }
  if (updates.agent_decisions_aligned !== undefined) {
    data.knowledge_usage.agent_decisions_aligned = (data.knowledge_usage.agent_decisions_aligned || 0) + updates.agent_decisions_aligned;
  }
  if (updates.last_report_at !== undefined) {
    data.knowledge_usage.last_report_at = updates.last_report_at;
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

// ── In-Memory RegistryCache ──────────────────────────────────────────────────
interface RegistryItem {
  concept_id: string;
  canonical_name: string;
  aliases: string[];
  status: string;
  confidence: number;
  evidence_count: number;
  session_count: number;
  sessions: string[];
  relationships?: Array<{ type: string; target: string; label?: string }>;
}

type Registry = Record<string, RegistryItem>;

// ── Ranking Strategy Abstraction ─────────────────────────────────────────────
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

    const sessionCount = item.session_count || 0;
    const usageBoost = 0.02 * Math.min(sessionCount / 10.0, 1.0);

    return similarity + healthBoost + statusBoost + usageBoost;
  }
}

const rankingStrategy = new Phase09RankingStrategy();

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
  // Clean raw length for layout calculation
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

// ── Todo Data Management Helpers ───────────────────────────────────────────
interface TodoItem {
  id: string;
  content: string;
  status: string;
  created_at: string;
}

function getTodosPath(workdir: string): string {
  const base = workdir || process.cwd();
  return path.join(base, ".oem", "state", "todos.json");
}

function loadTodos(workdir: string): TodoItem[] {
  const p = getTodosPath(workdir);
  if (fs.existsSync(p)) {
    try {
      const data = JSON.parse(fs.readFileSync(p, "utf-8"));
      if (Array.isArray(data)) return data;
    } catch (e) {
      // Ignore
    }
  }
  return [];
}

function saveTodos(todos: TodoItem[], workdir: string) {
  const p = getTodosPath(workdir);
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(todos, null, 2), "utf-8");
}

// ── Context Assembler ────────────────────────────────────────────────────────
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

    const activeGoals = readList("state/current-goals.md");
    const blockers = readList("state/open-issues.md");
    const discoveries = readList("state/active-decisions.md");

    // Parse session-handoff
    const handoffPath = path.join(oemDir, "session-handoff.md");
    if (fs.existsSync(handoffPath)) {
      try {
        const text = fs.readFileSync(handoffPath, "utf-8");
        const m = text.match(/## Next Action\s*\n\s*(?:-\s*)?([^\n#]+)/);
        if (m && m[1]) {
          activeGoals.unshift(m[1].trim());
        }
      } catch (e) {
        // Ignore
      }
    }

    // 3. Format dynamic markdown instructions under budget constraints
    let instContent = "# openempiric Session Context\n\n";

    instContent += "## Active Concepts\n";
    let budgetUsedChars = instContent.length;
    const charLimit = this.budget.tokenBudgetLimit * 4; // Approx 4 chars per token
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

    instContent += "\n## Active Decisions\n";
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

    // Save injected concepts to session state
    const sessionStatePath = path.join(projectPath, ".oem", "state", "session_state.json");
    try {
      fs.mkdirSync(path.dirname(sessionStatePath), { recursive: true });
      fs.writeFileSync(sessionStatePath, JSON.stringify({
        last_injected_concepts: injectedIds,
        last_injected_at: new Date().toISOString()
      }, null, 2), "utf-8");
    } catch (e) {
      // Ignore
    }

    // Increment injected concepts count immediately
    updateMetrics(projectPath, { concepts_injected: injectedIds.length });

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
          "oem_knowledge.server"
        ],
        enabled: true,
        timeout: 60000,
        ...existingMcp,
        env: {
          ...(existingMcp.env || {})
        }
      }

      // 2. Read context using ContextAssembler dynamically from workspace, falling back to JSON
      const tempInstPath = process.env.OEM_TEMP_INSTRUCTIONS ||
        path.join(os.homedir(), ".config", "opencode", "plugins", ".openempiric_temp_instructions.md")

      let instContent = "";
      try {
        const assembler = new ContextAssembler();
        const activeProject = config.directory || process.cwd();
        instContent = assembler.assemble(activeProject);
      } catch (e) {
        console.error("ContextAssembler failed, falling back to legacy JSON context:", e);
      }

      // Legacy fallback
      if (!instContent || instContent.trim() === "# openempiric Session Context\n\n## Active Concepts\n- None\n\n## Active Decisions\n- None\n\n## Relevant Failures\n- None\n\n## Open Questions\n- None") {
        const contextPath = process.env.OEM_RUNTIME_CONTEXT_PATH ||
          path.join(os.homedir(), ".config", "opencode", "plugins", ".oem_runtime_context.json")
        let oemContext: any = null
        try {
          if (fs.existsSync(contextPath)) {
            const content = fs.readFileSync(contextPath, "utf-8")
            oemContext = JSON.parse(content)
          }
        } catch (e) {
          // Ignore
        }
        if (!oemContext && config.mcp.openempiric.env?.OEM_RUNTIME_CONTEXT) {
          try {
            oemContext = JSON.parse(config.mcp.openempiric.env.OEM_RUNTIME_CONTEXT)
          } catch (e) {
            // Ignore
          }
        }

        if (oemContext) {
          instContent = "# openempiric Session Context\n\n";
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

          // Sort and pick top 5 candidates to read from disk
          candidates.sort((a, b) => b.score - a.score);
          const topCandidates = candidates.slice(0, 5);

          // Read wiki markdown files ONLY for those top candidates
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

      knowledge_session_start: tool({
        description: "Read project .oem/ state files and return context for session.",
        args: {
          project: tool.schema.string().optional().describe("Project directory path")
        },
        async execute({ project }, context) {
          const startTime = performance.now();
          const root = project || context.directory || process.cwd();
          const oemDir = path.join(root, ".oem");

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

          const activeGoals = readList("state/current-goals.md");
          const blockers = readList("state/open-issues.md");
          const discoveries = readList("state/active-decisions.md");

          // Parse session-handoff
          const handoffPath = path.join(oemDir, "session-handoff.md");
          if (fs.existsSync(handoffPath)) {
            try {
              const text = fs.readFileSync(handoffPath, "utf-8");
              const m = text.match(/## Next Action\s*\n\s*(?:-\s*)?([^\n#]+)/);
              if (m && m[1]) {
                activeGoals.unshift(m[1].trim());
              }
            } catch (e) {
              // Ignore
            }
          }

          const lines = [
            `Active Goals:`,
            ...activeGoals.slice(0, 5).map(g => `  🎯 ${g}`),
            "",
            `Blockers / Open Issues:`,
            ...blockers.slice(0, 5).map(b => `  ⚠️ ${b}`),
            "",
            `Recent Discoveries:`,
            ...discoveries.slice(0, 5).map(d => `  💡 ${d}`)
          ];

          const duration = performance.now() - startTime;
          updateMetrics(root, { context_latency: duration });

          return renderPanel("Session Start", lines, "restore");
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

          let chunkCount = 0;
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

      oem_todo_read: tool({
        description: "Read the current todo list from .oem/state/todos.json.",
        args: {
          workdir: tool.schema.string().optional().describe("Project directory")
        },
        async execute({ workdir }, context) {
          const root = workdir || context.directory || process.cwd();
          const todos = loadTodos(root);
          if (todos.length === 0) {
            return "Todo list is empty.";
          }
          const summary = [`Todo list (${todos.length} items):`];
          for (const t of todos) {
            const icon = t.status === "completed" ? "✓" : t.status === "in_progress" ? "→" : " ";
            summary.push(`  [${icon}] ${t.content}  (id: ${t.id})`);
          }
          return summary.join("\n");
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
          let parsed: any[];
          try {
            parsed = JSON.parse(items);
          } catch (e) {
            return `Error: invalid JSON: ${e}`;
          }
          if (!Array.isArray(parsed)) {
            return "Error: items must be a JSON array";
          }

          const todos: TodoItem[] = parsed.map(item => ({
            id: item.id || crypto.randomUUID(),
            content: item.content || "",
            status: item.status || "pending",
            created_at: new Date().toISOString().slice(0, 16).replace("T", " ")
          })).filter(t => t.content);

          saveTodos(todos, root);

          const summary = todos.map(t => `  [${t.status[0].toUpperCase()}] ${t.content}`);
          return `Todo list updated (${todos.length} items):\n` + summary.join("\n");
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
          const todos = loadTodos(root);
          if (todos.length === 0) {
            return "Error: No todo items found.";
          }

          const target = todos.find(t => t.id === item_id);
          if (!target) {
            return `Error: Item ${item_id} not found.`;
          }

          if (status) {
            target.status = status;
          } else {
            const nextStatus: Record<string, string> = {
              "pending": "in_progress",
              "in_progress": "completed",
              "completed": "pending"
            };
            target.status = nextStatus[target.status] || "in_progress";
          }

          if (target.status === "completed") {
            const nextPending = todos.find(t => t.status === "pending");
            if (nextPending) {
              nextPending.status = "in_progress";
            }
          }

          saveTodos(todos, root);
          return `Updated item ${item_id}: ${target.content} → ${target.status}`;
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
          
          // 1. Read last injected concepts from session state
          const sessionStatePath = path.join(root, ".oem", "state", "session_state.json");
          let injectedConcepts: string[] = [];
          if (fs.existsSync(sessionStatePath)) {
            try {
              const stateData = JSON.parse(fs.readFileSync(sessionStatePath, "utf-8"));
              if (Array.isArray(stateData.last_injected_concepts)) {
                injectedConcepts = stateData.last_injected_concepts;
              }
            } catch (e) {
              // Ignore
            }
          }

          // 2. Auto-derive ignored concepts if not provided
          let ignored = concepts_ignored || [];
          if (!concepts_ignored) {
            ignored = injectedConcepts.filter(cid => !concepts_used.includes(cid));
          }

          // 3. Update metrics
          const nowStr = new Date().toISOString();
          updateMetrics(root, {
            concepts_referenced: concepts_used.length,
            concepts_ignored: ignored.length,
            agent_decisions_aligned: (decisions || []).length,
            last_report_at: nowStr
          });

          // 4. Append to usage_log.jsonl
          const logPath = path.join(root, ".oem", "state", "usage_log.jsonl");
          const logEntry = {
            timestamp: nowStr,
            concepts_used,
            concepts_ignored: ignored,
            decisions: decisions || [],
            session_concepts_injected: injectedConcepts
          };
          try {
            fs.mkdirSync(path.dirname(logPath), { recursive: true });
            fs.appendFileSync(logPath, JSON.stringify(logEntry) + "\n", "utf-8");
          } catch (e) {
            console.error("Failed to write to usage_log.jsonl:", e);
          }

          const lines = [
            `Report Timestamp: ${nowStr}`,
            `Concepts Used:    [${concepts_used.join(", ") || "None"}]`,
            `Concepts Ignored: [${ignored.join(", ") || "None"}]`,
            `Decisions Aligned: ${(decisions || []).length}`,
            "",
            "Note: This is experimental telemetry for establishing pipelines.",
            "Roadmap decisions are not made on this reported usage."
          ];
          return renderPanel("Knowledge Usage Report Received", lines, "ok");
        }
      })
    }
  }
}
