<p align="center">
  <img src="logo.png" alt="openempiric" width="800"/>
</p>

<p align="center">
  <strong>State-of-the-art event-sourced knowledge operating system and agent runtime for AI.</strong>
</p>

<p align="center">
  <a href="https://github.com/xpajonx/openempiric">GitHub</a> • 
  <a href="CONTRIBUTING.md">Contributing</a> • 
  <a href="LICENSE">License</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-%3E%3D%203.12-blue.svg" alt="Python Version"/>
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"/>
</p>

---

Most AI memory systems focus on *remembering*. We believe the harder and more important problem is **learning**.

`openempiric` is an event-sourced knowledge operating system for coding agents that continuously converts conversations, experiments, decisions, successes, and failures into structured, durable organizational knowledge that improves over time.

---

## Core Capabilities

- 🤖 **Subagent Orchestration** — Spawn child OpenCode sessions and execute parallel tasks.
- 🔄 **Session Lifecycle Hooks** — Create, prompt, list, export, and fork sessions.
- 📋 **Plan Mode** — Decompose complex prompts into deterministic sub-tasks, and execute them.
- 📝 **Todo Tracking** — Write, read, and advance persistent task lists.
- 🕸️ **Knowledge Graph** — Hybrid vector + BM25 search over your project's historical knowledge.
- 📂 **Isolated Contexts** — Each project keeps its own `.harness/` folder containing its private vector DB and concept registry.
- 🛡️ **Safety Guards** — Enforces path traversal sandboxing and a >50% truncation prevention guard during writes.

---

## Install & Setup

```bash
git clone https://github.com/xpajonx/openempiric.git
cd openempiric
uv sync
```

---

## Configure as OpenCode Plugin (WSL)

`openempiric` operates as a native OpenCode plugin and skill.

1. **Install the Plugin:**
   Copy the plugin file to OpenCode's plugins directory.
   ```bash
   mkdir -p ~/.config/opencode/plugins
   cp openempiric/plugins/openempiric.ts ~/.config/opencode/plugins/
   ```

2. **Install the Skill:**
   Copy the skill folder to OpenCode's skills directory.
   ```bash
   mkdir -p ~/.config/opencode/skills/harness-orchestrator
   cp openempiric/skills/harness-orchestrator/SKILL.md ~/.config/opencode/skills/harness-orchestrator/
   ```

The plugin will dynamically register the `openempiric` MCP server and automatically handle session lifecycle hooks!

---

## The Knowledge Pipeline

When you commit a session (`knowledge_session_commit`), the engine strictly executes the knowledge formation pipeline:

```text
Human + Agent Collaboration
            ↓
    Session Reflection
            ↓
     Knowledge Events
            ↓
       Event Store  (Immutable log: events.jsonl)
            ↓
     Concept Registry  (Candidate → Emerging → Validated → Canonical)
            ↓
      Knowledge Wiki  (Materializes wiki files for validated concepts)
            ↓
     Knowledge Graph  (Reciprocal link mapping)
            ↓
  Workspace Intelligence  (Semantic + BM25 Search)
```

All data lives securely in `.harness/` at the project root.

---

## Thesis & Architecture

Today's AI agents suffer from a fundamental limitation: **context window boundaries**. When the context window ends, the wisdom accumulated during collaboration disappears. Traditional memory systems store transcripts or raw embeddings. They remember information, but they do not accumulate wisdom.

`openempiric` treats learning as a first-class primitive:

1. **Events Are The Source Of Truth:** Everything begins with immutable events (decisions, validations, failures). Everything else (registry, wiki, graph) can be rebuilt by replaying history.
2. **Concepts Emerge From Evidence:** Ideas must earn trust. Only validated concepts become materialized wiki files.
3. **Failure Is Knowledge:** Agents must remember what failed and why, not just what worked, to avoid repeating historical errors.

---

## Tools Listing (34 total)

### Orchestrator (`harness_*`)

| Tool | Purpose |
|---|---|
| `harness_run_opencode` | Run a prompt in a child opencode session |
| `harness_run_tasks` | Run multiple independent tasks sequentially/parallel |
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
| `knowledge_update_graph` | Rebuild the concept graph with reciprocal links |
| `knowledge_session_commit` | Full session-end pipeline (reflect → materialize → graph → re-index) |
| `knowledge_consolidate` | Consolidate similar concepts |
| `knowledge_get_events` | List knowledge events |
| `knowledge_get_event` | Get a single knowledge event by ID |
| `knowledge_explain_concept` | Explain a concept and its evolution based on evidence |
| `knowledge_merge_concepts` | Merge a secondary concept into a primary concept |
| `knowledge_lint` | Check the knowledge base for broken links and orphan concepts |

---

## Open Research Problems

- **Concept Identity Resolution:** When are two concepts actually the same (e.g. `Video Hooks` vs `Opening Hooks` vs `Hook Formats`)?
- **Knowledge Evolution:** How should concepts improve over time and how should evidence reshape existing validated knowledge?
- **Organizational Learning:** How can agents remember what worked and failed across months or years of collaboration?
