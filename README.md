# opencode-harness

A single MCP server that combines process orchestration with local-first knowledge management for [opencode](https://opencode.ai).

## What it does

- **Subagent orchestration** — spawn child opencode sessions, run tasks in parallel
- **Session lifecycle** — create, prompt, list, export, fork sessions
- **Plan mode** — decompose prompts into deterministic sub-tasks, batch-execute them
- **Todo tracking** — write/read/advance persistent todo lists
- **Knowledge graph** — hybrid vector + BM25 search over your project's session history
- **Per-project `.harness/`** — each project keeps its own isolated vector DB and concept registry

## Install

```bash
git clone https://github.com/xpajonx/opencode-harness.git
cd opencode-harness
uv sync
```

## Configure as OpenCode Plugin (WSL)

`opencode-harness` now runs as a native OpenCode plugin and skill. 

1. **Install the Plugin:**
   Copy the plugin file to OpenCode's plugins directory.
   ```bash
   mkdir -p ~/.config/opencode/plugins
   cp opencode-harness/harness.ts ~/.config/opencode/plugins/
   ```

2. **Install the Skill:**
   Copy the skill folder to OpenCode's skills directory. This allows you to configure limits (e.g. `max_parallel_tasks`) using YAML frontmatter.
   ```bash
   mkdir -p ~/.config/opencode/skills/harness-orchestrator
   cp opencode-harness/SKILL.md ~/.config/opencode/skills/harness-orchestrator/
   ```

You no longer need to manually edit `opencode.jsonc`. The plugin will dynamically register the `harness` MCP server and automatically handle session lifecycle hooks!



## Tools (32 total)

### Orchestrator (`harness_*`)

| Tool | Purpose |
|---|---|
| `harness_run_opencode` | Run a prompt in a child opencode session |
| `harness_run_tasks` | Run multiple independent tasks sequentially |
| `harness_session_run_json` | Run a session and get structured output (tokens, calls, cost) |
| `harness_session_create` | Create a new session |
| `harness_session_prompt` | Continue an existing session |
| `harness_session_list` | List all sessions |
| `harness_session_export` | Export session data as JSON |
| `harness_session_fork` | Fork a session into a new one |
| `harness_db_query` | Read-only SQL on session database |
| `harness_list_agents` | List configured agents |
| `harness_list_projects` | List available project directories |
| `harness_plan_begin` | Start a plan — decompose a prompt into sub-tasks |
| `harness_plan_step` | Advance the plan by one step |
| `harness_plan_finalize` | Add remaining tasks and generate summary |
| `harness_plan_execute` | Execute all completed plan steps |
| `harness_plan_status` | Get current plan state |
| `harness_plan_abort` | Cancel a plan |
| `harness_todo_write` | Write/replace the todo list |
| `harness_todo_read` | Read the todo list |
| `harness_todo_advance` | Advance to the next todo item |

### Knowledge (`knowledge_*`)

| Tool | Purpose |
|---|---|
| `knowledge_init` | Initialize `.harness/` for the current project |
| `knowledge_search` | Hybrid (BM25 + dense) search across project knowledge |
| `knowledge_index` | Index files into the vector store |
| `knowledge_stats` | Show knowledge storage statistics |
| `knowledge_session_start` | Get pre-injection context for a new session |
| `knowledge_reflect` | Reflect on a session transcript and produce insights |
| `knowledge_materialize` | Materialize concepts from reflected insights |
| `knowledge_update_graph` | Rebuild the concept graph |
| `knowledge_session_commit` | Full session-end pipeline (reflect → materialize → graph → re-index) |
| `knowledge_consolidate` | Consolidate similar concepts |
| `knowledge_get_events` | List knowledge events |
| `knowledge_get_event` | Get a single knowledge event by ID |

## How knowledge works

When you commit a session (`knowledge_session_commit`), the engine strictly follows the knowledge formation pipeline:

1. **Reflects** on the session transcript to extract structured Knowledge Events.
2. **Event Store** — appends events to the immutable log (`events.jsonl`).
3. **Concept Registry** — resolves concept identity and evaluates promotion rules (Candidate → Emerging → Validated → Canonical).
4. **Materializes** — generates canonical wiki files for validated concepts only.
5. **Updates the graph** — updates reciprocal links between materialized concepts.
6. **Re-indexes** — rebuilds the vector store for semantic search.

All data lives in `.harness/` at the project root.

## License

MIT
