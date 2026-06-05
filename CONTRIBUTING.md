# Contributing to openempiric

We are not building another memory database. We are exploring a different question:

> How can AI systems accumulate knowledge the way organizations do?

If that problem excites you, we'd love your help. Open an issue, start a discussion, challenge the architecture, or contribute code. Help us build the future knowledge layer for coding agents.

---

## Who We're Looking For

We're actively looking for contributors interested in:

### AI Agents
- OpenCode, Claude Code, Cursor integration.
- Dynamic agent rule injection.
- Telemetry & performance metrics extraction.

### Knowledge Systems
- Event sourcing & transaction ledgers.
- Typed knowledge graphs (reciprocal link mapping).
- Local-first durable storage & indexing architectures.

### LLM Research
- Concept identity resolution (fuzzy deduplication, merging).
- Semantic drift & knowledge evolution tracking.
- Session transcript reflection algorithms.

### Systems Engineering
- Asynchronous parallel linters.
- Boundary protection & traversal validation (sandboxing).
- Cross-platform CLI utilities.

---

## Environment Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/xpajonx/openempiric.git
   cd openempiric
   ```
2. Install `uv`.
3. Initialize the workspace and install all dependencies:
   ```bash
   uv sync
   ```
4. Verify the setup by running the test suite:
   ```bash
   uv run pytest
   ```

> [!WARNING]
> Never run `uv sync` or `uv venv` inside `packages/oem-knowledge` or other sub-packages. All packages must be managed from the repository root workspace.


## Agent-First Principle

OEM should not require agents to:

- initialize sessions
- commit sessions
- activate memory

Lifecycle management belongs to the runtime.

When introducing new features, prefer runtime automation over agent instructions.

