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

## Configure in opencode.jsonc

Add this entry to your `~/.config/opencode/opencode.jsonc`:

```jsonc
"harness": {
  "type": "local",
  "command": ["uv", "run", "--directory", "/path/to/opencode-harness", "python", "-m", "harness_orchestrator.server"],
  "enabled": true,
  "timeout": 60000
}
```

You can also register only specific tools via `disabledTools` or use a subdirectory as the project root — the `.harness/` folder is created inside whatever directory the MCP server is run from.

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

When you commit a session (`knowledge_session_commit`), the engine:

1. **Reflects** on the session transcript — extracts key facts, decisions, patterns
2. **Materializes** — creates or updates concepts in the registry
3. **Updates the graph** — runs community detection (Leiden) on concept co-occurrence
4. **Re-indexes** — rebuilds the vector store with updated concept embeddings

All data lives in `.harness/` at the project root.

## License

MIT
