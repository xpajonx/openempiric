# openempiric

> Transform human-agent collaboration into durable organizational knowledge.

Most AI memory systems focus on remembering.

We believe the harder problem is learning.

`openempiric` is an event-sourced knowledge operating system for coding agents that continuously converts conversations, experiments, decisions, successes, and failures into structured knowledge that improves over time.

---

## Install

```bash
git clone https://github.com/xpajonx/openempiric.git
cd openempiric
uv sync
```

## Configure as OpenCode Plugin (WSL)

`openempiric` now runs as a native OpenCode plugin and skill. 

1. **Install the Plugin:**
   Copy the plugin file to OpenCode's plugins directory.
   ```bash
   mkdir -p ~/.config/opencode/plugins
   cp openempiric/harness.ts ~/.config/opencode/plugins/
   ```

2. **Install the Skill:**
   Copy the skill folder to OpenCode's skills directory. This allows you to configure limits (e.g. `max_parallel_tasks`) using YAML frontmatter.
   ```bash
   mkdir -p ~/.config/opencode/skills/harness-orchestrator
   cp openempiric/SKILL.md ~/.config/opencode/skills/harness-orchestrator/
   ```

You no longer need to manually edit `opencode.jsonc`. The plugin will dynamically register the `harness` MCP server and automatically handle session lifecycle hooks!



## Tools (34 total)

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
| `knowledge_explain_concept` | Explain a concept and its evolution based on evidence |
| `knowledge_merge_concepts` | Merge a secondary concept into a primary concept |

## How knowledge works

When you commit a session (`knowledge_session_commit`), the engine strictly follows the knowledge formation pipeline:

1. **Reflects** on the session transcript to extract structured Knowledge Events.
2. **Event Store** — appends events to the immutable log (`events.jsonl`).
3. **Concept Registry** — resolves concept identity and evaluates promotion rules (Candidate → Emerging → Validated → Canonical).
4. **Materializes** — generates canonical wiki files for validated concepts only.
5. **Updates the graph** — updates reciprocal links between materialized concepts.
6. **Re-indexes** — rebuilds the vector store for semantic search.

All data lives in `.harness/` at the project root.

## Why We Started This Project

Today's AI agents suffer from a fundamental limitation:

```text
Conversation
↓
Context Window Ends
↓
Knowledge Disappears
```

Even when memory systems exist, they typically store:

* transcripts
* embeddings
* retrieved snippets

They remember information.

They do not accumulate wisdom.

As a result, agents repeatedly:

* rediscover the same solutions
* repeat failed experiments
* forget architectural decisions
* lose organizational learning

We think there is a better approach.

---

## Our Thesis

AI agents should not remember conversations.

AI agents should remember:

* validated knowledge
* failed experiments
* architectural decisions
* recurring patterns
* organizational learning

Instead of storing conversations directly:

```text
Conversation
↓
Knowledge Events
↓
Concept Formation
↓
Knowledge Evolution
↓
Organizational Learning
```

The conversation becomes an input.

Knowledge becomes the product.

---

## Architecture

### Event-Sourced Knowledge Pipeline

```text
Human + Agent Collaboration
↓
Session Reflection
↓
Knowledge Events
↓
Event Store
↓
Concept Registry
↓
Promotion Engine
↓
Knowledge Wiki
↓
Knowledge Graph
↓
Workspace Intelligence
```

Unlike traditional memory systems:

```text
Conversation
↓
Embedding
↓
Retrieval
```

openempiric treats learning as a first-class primitive.

---

## Core Principles

### 1. Events Are The Source Of Truth

Everything begins with immutable events.

Examples:

```yaml
event_type: validation
concept: stripe-retry-architecture
```

```yaml
event_type: failure
concept: auto-linking
```

```yaml
event_type: decision
concept: event-sourced-knowledge-system
```

Events are append-only.

Events are never modified.

Everything else can be rebuilt from them.

---

### 2. Concepts Emerge From Evidence

Knowledge should earn trust.

Every concept progresses through a lifecycle:

```text
Candidate
↓
Emerging
↓
Validated
↓
Canonical
↓
Deprecated
```

Not every idea deserves a wiki page.

Only validated knowledge becomes durable.

---

### 3. The Knowledge Base Is Rebuildable

The system follows event-sourcing principles.

```text
events.jsonl
↓
Replay
↓
Concept Registry
↓
Knowledge Wiki
```

If the registry is lost.

If the wiki is deleted.

If the graph becomes corrupted.

The system can rebuild itself from history.

---

### 4. Failure Is Knowledge

Most memory systems only remember facts.

We want agents to remember:

```text
What worked.
What failed.
Why decisions were made.
```

Because organizational learning comes from outcomes.

Not transcripts.

---

## Current Status

### Implemented

* Event Store
* Session Reflection Engine
* Concept Registry
* Concept Promotion Lifecycle
* Replay Engine
* Knowledge Materialization
* Explainable Event History

### In Progress

* Concept Identity Resolution
* Knowledge Evolution
* Concept Explainability

### Future

* Typed Knowledge Graphs
* Organizational Learning Layer
* Workspace Intelligence
* Multi-Agent Knowledge Sharing
* Autonomous Knowledge Stewardship

---

## Example

Instead of storing:

```text
Conversation #184
```

The system produces:

```yaml
event_type: validation

concept:
  stripe-retry-architecture

evidence:
  webhook duplication issue resolved
```

Which eventually evolves into:

```markdown
# Stripe Retry Architecture

Validated retry strategy for webhook processing.

Evidence:
- Session 2026-06-01
- Session 2026-06-12
- Session 2026-07-02
```

The goal is not memory retrieval.

The goal is durable knowledge.

---

## Who We're Looking For

We're actively looking for contributors interested in:

### AI Agents

* OpenCode
* Claude Code
* Cursor
* Agent frameworks

### Knowledge Systems

* Event sourcing
* Knowledge graphs
* Organizational memory
* Information architecture

### LLM Research

* Concept extraction
* Identity resolution
* Knowledge evolution
* Reflection systems

### Systems Engineering

* Local-first infrastructure
* Indexing pipelines
* Replay architectures
* Distributed knowledge systems

---

## Open Research Problems

We believe these are largely unsolved:

### Concept Identity Resolution

When are two concepts actually the same concept?

```text
Video Hooks
Opening Hooks
Hook Formats
```

One concept?

Three concepts?

How do we know?

---

### Knowledge Evolution

How should concepts improve over time?

How should evidence reshape knowledge?

---

### Organizational Learning

How can agents remember:

```text
What worked?
What failed?
What should never be repeated?
```

across months or years of collaboration?

---

## Contributing

We are not building another memory database.

We are exploring a different question:

> How can AI systems accumulate knowledge the way organizations do?

If that problem excites you, we'd love your help.

Open an issue.

Start a discussion.

Challenge the architecture.

Help us build the future knowledge layer for coding agents.

---


## What it does

- **Subagent orchestration** — spawn child opencode sessions, run tasks in parallel
- **Session lifecycle** — create, prompt, list, export, fork sessions
- **Plan mode** — decompose prompts into deterministic sub-tasks, batch-execute them
- **Todo tracking** — write/read/advance persistent todo lists
- **Knowledge graph** — hybrid vector + BM25 search over your project's session history
- **Per-project `.harness/`** — each project keeps its own isolated vector DB and concept registry


## License

MIT
