<p align="center">
  <img src="logo.png" alt="openempiric" width="800"/>
</p>

<p align="center">
  <strong>Agent-first event-sourced knowledge runtime for AI coding sessions.</strong>
</p>

---

OpenEmpiric is an agent-first learning runtime.

Run:

```bash
oem run opencode
```

Then work normally.

OEM automatically:
- **restores context** — Pre-loads active concepts, decisions, and past failures into the agent's prompt context at the start of a session.
- **learns from conversations** — Reflects on chat transcripts post-session, automatically extracting hypotheses, experiments, decisions, and outcomes.
- **records outcomes** — Evaluates and logs session status (success, failure, abandoned) and satisfaction ratings.
- **improves future sessions** — Evolves the concept registry, semantic wiki pages, and reciprocal links to continuously optimize long-term memory.

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

This automatically checks for configuration, plugin links, and warms up the embedding cache model.

### 3. Run Your Agent

Launch your agent (e.g., `opencode`, `claude-code`, `cursor`) and begin working:

```bash
oem run opencode
```

---

## Command Taxonomy

### Public User Surface
These are the primary entrypoints for users working with OpenEmpiric.
* `oem run` - Spawns a managed coding agent session with dynamic config injection & lifecycle commitment.
* `oem doctor` - Validates the local environment, MCP registry status, and warms the cache.
* `oem search` - Performs hybrid semantic + BM25 keyword search across project registry concepts.
* `oem health` - Scans the workspace for stale concepts, duplicates, and contradicting knowledge.

### Advanced
For power users performing manual maintenance or diagnostics.
* `oem merge` - Merges two duplicate/overlapping registry concepts together.
* `oem rebuild` - Replays the raw event store to rebuild the concept registry.
* `oem reflect` - Dry-runs session transcript reflection and concept candidate extraction.

### Internal Runtime
Low-level commands managed by the session coordinator. These are implementation details and do not need to be run manually.
* `oem session-start` - Restores pre-injection context and prepares files before the agent starts.
* `oem session-end` - Finalizes context, runs extraction, and commits learnings after agent exits.
* `oem outcome` - Records manual outcome status, referenced concepts, and goal satisfaction ratings.
* `oem recover` - Restores, commits, or aborts crashed or unfinished agent sessions.

---

## Best Practices

OpenEmpiric learns best when decisions, experiments, and outcomes are made explicit during conversations.

* **Explicit Rationale**: Instead of *"use typescript"*, write: *"we chose typescript because python startup latency caused timeouts."*
* **Detailed Failures**: Instead of *"it failed"*, write: *"the indexing pipeline failed because ChromaDB rejected np.float32 embeddings."*
* **Outcome Recording**: Use outcomes to help the ranking model prioritize the most relevant concepts for context injection.

---

## Repository Structure

- [packages/oem-knowledge](file:///home/xpajonx/.config/openempiric/packages/oem-knowledge) — Core RAG logic, SQLite event database, and Python CLI entrypoint.
- [packages/oem-tui](file:///home/xpajonx/.config/openempiric/packages/oem-tui) — Shared TUI layout utilities and rendering panels.
- [plugins](file:///home/xpajonx/.config/openempiric/plugins) — TypeScript-native plugins for agent integration.
