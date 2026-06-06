<p align="center">
  <img src="logo.png" alt="openempiric" width="800"/>
</p>

<p align="center">
  <strong>Agent-first event-sourced knowledge runtime for AI coding sessions.</strong>
</p>

---

OpenEmpiric is an agent-first learning runtime.

Run your coding agent through OEM:

```bash
oem run opencode
```

Then work normally.

OpenEmpiric automatically:

- restores project memory
- injects relevant context
- learns from conversations
- records outcomes
- improves future retrieval

You don't manage memory.

You talk to your agent.
OEM manages the learning loop.

---

## Quick Start

### 1. Install Globally

Install the unified `oem` runtime globally via `uv`:

```bash
uv tool install "git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge"
```

### 2. Verify Your Environment

Check if your workspace is healthy and ready:

```bash
oem doctor
```

This automatically checks for workspace configuration, workstation integration, and warms up the embedding cache model.

### 3. Configure Workstation Integration

Set up the OpenCode agent workstation-level integration (copies plugins, registers instructions, validates configuration):

```bash
oem setup opencode
```

If you ever need to forcefully overwrite and recreate the integration configuration, you can use:

```bash
oem setup opencode --repair
```

### 4. Initialize Project Memory

Initialize the project-level OpenEmpiric memory repository in the root of your project:

```bash
oem init
```

This creates the `.oem/` directory structure to store project-specific concept files, state, and event logs.

### 5. Run Your Agent

Launch your agent (e.g., `opencode`, `claude-code`, `cursor`) and begin working:

```bash
oem run opencode
```

---

## Common Commands

Daily Use

- `oem run opencode`
- `oem doctor`
- `oem search`

Setup & Admin

- `oem setup opencode`
- `oem init`

Knowledge Health

- `oem health`

Advanced

- `oem merge`
- `oem rebuild`
- `oem reflect`

---

## Best Practices

OEM works automatically.

For better knowledge capture:

"Session start" and
- State your goal

"Session end" and
- State what was completed

These markers are optional but help OEM generate higher-quality reflections and project memory.

OpenEmpiric also learns best when decisions, experiments, and outcomes are made explicit during conversations:

* **Explicit Rationale**: Instead of *"use typescript"*, write: *"we chose typescript because python startup latency caused timeouts."*
* **Detailed Failures**: Instead of *"it failed"*, write: *"the indexing pipeline failed because ChromaDB rejected np.float32 embeddings."*
* **Outcome Recording**: Use outcomes to help the ranking model prioritize the most relevant concepts for context injection.

---

## Repository Structure

- [packages/oem-knowledge](file:///home/xpajonx/.config/openempiric/packages/oem-knowledge) — Core RAG logic, SQLite event database, and Python CLI entrypoint.
- [packages/oem-tui](file:///home/xpajonx/.config/openempiric/packages/oem-tui) — Shared TUI layout utilities and rendering panels.
- [plugins](file:///home/xpajonx/.config/openempiric/plugins) — TypeScript-native plugins for agent integration.
