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

class RegistryCache {
  private cache: Map<string, { data: Registry; mtime: number }> = new Map();

  getRegistry(projectPath: string): Registry {
    const oemDir = path.join(projectPath, ".oem");
    const regPath = path.join(oemDir, "concept_registry.json");
    if (!fs.existsSync(regPath)) {
      return {};
    }
    const stat = fs.statSync(regPath);
    const cached = this.cache.get(regPath);
    if (cached && cached.mtime === stat.mtimeMs) {
      return cached.data;
    }
    try {
      const data = JSON.parse(fs.readFileSync(regPath, "utf-8")) as Registry;
      this.cache.set(regPath, { data, mtime: stat.mtimeMs });
      return data;
    } catch (e) {
      console.error("Failed to read registry cache:", e);
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
              score: cand.score + fileBoost
            });
          }

          results.sort((a, b) => b.score - a.score);
          const finalResults = results.slice(0, k || 3);

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
      })
    }
  }
}
