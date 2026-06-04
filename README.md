<p align="center">
  <img src="logo.png" alt="openempiric" width="800"/>
</p>

<p align="center">
  <strong>Agent-agnostic event-sourced knowledge operating system and agent runtime for AI.</strong>
</p>

<p align="center">
  <a href="https://github.com/xpajonx/openempiric">GitHub</a> • 
  <a href="docs/adapter-spec.md">Adapter Spec</a> •
  <a href="docs/adapter-architecture.md">Adapter Architecture</a> •
  <a href="docs/releases/v0.9.4.md">v0.9.4 Release Notes</a> •
  <a href="docs/development/docker.md">Docker Dev Environment</a> •
  <a href="CONTRIBUTING.md">Contributing</a> • 
  <a href="LICENSE">License</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-%3E%3D%203.12-blue.svg" alt="Python Version"/>
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License"/>
</p>

---

Most AI memory systems focus on *remembering*. We believe the harder and more important problem is **learning**.

`openempiric` is an agent-agnostic event-sourced knowledge runtime for coding agents that continuously converts conversations, experiments, decisions, successes, and failures into structured, durable organizational knowledge that improves over time.

---

## Core Capabilities

`openempiric` is first and foremost a **knowledge accumulation system** and **agent runtime** (the `oem` CLI):

### 🧠 Knowledge Layer
- 🕸️ **Knowledge Graph** — Hybrid vector + BM25 search over your project's historical knowledge.
- 📂 **Isolated Contexts** — Each project keeps its own `.oem/` folder containing its private vector DB, sessions, and concept registry.
- 🛡️ **Safety Guards** — Enforces path traversal sandboxing and a >50% truncation prevention guard during writes.
- 📋 **Todo Tracking** — Maintain persistent task lists directly alongside validated concept growth.

### ⚡ Agent Runtime (`oem`)
- 🤖 **Agent Wrapping (`oem run`)** — Automatically injects openempiric settings into your coding agent on the fly, and restores original files on exit.
- 🔄 **Session Lifecycle Automation** — Spawns agent processes with pre-injection context loaded from past sessions, committing and reflecting on learnings automatically upon exit.

---

## Install & Setup

### Global Installation (For General Users)

Install the `oem` CLI globally on your machine using `uv` (no cloning required) or via a local clone:

```bash
# Using uv direct from Git (Recommended)
uv tool install "git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge"

# Or using uv locally after cloning
uv tool install --editable ./packages/oem-knowledge

# Or using pipx locally after cloning
pipx install ./packages/oem-knowledge
```

Once installed, verify the setup:
```bash
oem doctor
```

---

### Workspace Contributor Setup

If you are developing or contributing to OpenEmpiric:

1. **Clone the repository:**
   ```bash
   git clone https://github.com/xpajonx/openempiric.git
   cd openempiric
   ```

2. **Synchronize the UV workspace:**
   Use the `--all-packages` flag to sync all workspace members and install tools:
   ```bash
   uv sync --all-packages --all-extras --dev
   ```

3. **Verify the installation:**
   Run the test suite (runs offline using a mocked embedding engine):
   ```bash
   uv run pytest
   ```

4. **Verify doctor check status:**
   Validate your environment dependencies and cache status:
   ```bash
   uv run oem doctor
   ```

### Development Environment

OpenEmpiric uses a single UV workspace virtual environment. Do NOT create package-local virtual environments.

* **Correct:**
  ```bash
  uv sync --all-packages
  uv run pytest
  ```
* **Incorrect:**
  ```bash
  cd packages/oem-knowledge
  uv venv
  ```

### Repository Layout

```text
root/
├── .venv/            ← Single shared virtual environment
├── docs/             ← Release notes, architecture, and adapter specifications
└── packages/         ← Workspace member packages (oem-knowledge, oem-tui)
```

---

## Configure as OpenCode Plugin (WSL Optional)

While `openempiric` is designed to be fully agent-agnostic and work with any wrapper, it features a native OpenCode plugin:

1. **Install/Enable the Plugin:**
   Symlink the plugin file to OpenCode's plugins directory:
   ```bash
   mkdir -p ~/.config/opencode/plugins
   ln -s $(pwd)/plugins/openempiric.ts ~/.config/opencode/plugins/openempiric.ts
   ```

OpenCode automatically loads `.ts` plugins from the plugins directory. The plugin dynamically resolves the repository location (via symlink, environment variable `OPENEMPIRIC_DIR`, or home directory candidates) to register the `openempiric` MCP server and load the required instructions!

---

## Uninstallation

To completely remove OpenEmpiric from your system:

1. **Remove the OpenCode plugin link:**
   ```bash
   rm ~/.config/opencode/plugins/openempiric.ts
   ```

2. **Clean up virtual environments and caches (optional):**
   ```bash
   rm -rf .venv
   rm -rf /tmp/fastembed_cache
   ```

## OEM CLI Usage

The package exposes the unified `oem` command-line utility for managing the project memory:

```bash
# Initialize openempiric in a project
oem init

# Run an agent (e.g. opencode) with dynamic config injection & lifecycle commitment
oem run opencode

# Manually start/commit session states
oem session-start
oem session-end --chat "conversation text"

# Lint and auto-heal wiki links/orphans
oem lint --fix

# Query stats and status
oem status
```

---

## The Knowledge Pipeline

When you commit a session, the engine strictly executes the knowledge formation pipeline:

```text
Human + Agent Collaboration (via oem run)
            ↓
    Session Reflection
            ↓
    Knowledge Events
            ↓
       Event Store  (Immutable log: events.jsonl)
            ↓
      Concept Registry  (Candidate → Emerging → Validated → Canonical)
            ↓
       Knowledge Wiki  (Materializes markdown files in .oem/wiki/)
            ↓
      Knowledge Graph  (Reciprocal link mapping)
            ↓
   Workspace Intelligence  (Semantic + BM25 Search)
```

All data lives securely in `.oem/` at the project root. Any legacy `.harness/` directories are migrated automatically.

---

## Thesis & Architecture

Today's AI agents suffer from a fundamental limitation: **context window boundaries**. When the context window ends, the wisdom accumulated during collaboration disappears. Traditional memory systems store transcripts or raw embeddings. They remember information, but they do not accumulate wisdom.

`openempiric` treats learning as a first-class primitive:

1. **Events Are The Source Of Truth:** Everything begins with immutable events (decisions, validations, failures). Everything else (registry, wiki, graph) can be rebuilt by replaying history.
2. **Concepts Emerge From Evidence:** Ideas must earn trust. Only validated concepts become materialized wiki files.
3. **Failure Is Knowledge:** Agents must remember what failed and why, not just what worked, to avoid repeating historical errors.

---

## Mounted MCP Tools

### Todo Tools (`oem_todo_*`)

| Tool | Purpose |
|---|---|
| `oem_todo_write` | Write/replace the todo list |
| `oem_todo_read` | Read the todo list |
| `oem_todo_advance` | Advance/update a todo item status |

### Knowledge Tools (`knowledge_*`)

| Tool | Purpose |
|---|---|
| `knowledge_init` | Initialize `.oem/` for the current project |
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
| `knowledge_graph_query` | Query semantic relationships for a concept |
| `knowledge_usage_report` | Report concept usage and decision alignment for the session |

---

## Open Research Problems

- **Concept Identity Resolution:** When are two concepts actually the same (e.g. `Video Hooks` vs `Opening Hooks` vs `Hook Formats`)?
- **Knowledge Evolution:** How should concepts improve over time and how should evidence reshape existing validated knowledge?
- **Organizational Learning:** How can agents remember what worked and failed across months or years of collaboration?
