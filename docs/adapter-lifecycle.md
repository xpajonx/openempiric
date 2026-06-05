# OpenEmpiric Adapter Lifecycle Contract

This document defines the integration boundaries, lifecycle phases, and expected behaviors for building OpenEmpiric (OEM) adapters. 

Building an adapter allows you to integrate new AI agents, CLI wrappers, or editors into the OpenEmpiric platform. As a contributor, **you do not need to understand OEM's internal database layouts, event sourcing code, or fitness score math** to successfully build an adapter. You only need to implement the lifecycle hooks defined in this contract.

---

## 1. OEM Core vs Adapter Responsibilities

A clean separation of concerns ensures that the core knowledge-engine logic remains robust and reusable, while adapters focus solely on integration with the specific target agent's environment.

| OEM Core Responsibilities | Adapter Responsibilities |
| :--- | :--- |
| **Event Sourcing**: Appending to the immutable `events.jsonl` log. | **Agent Launching**: Spawning the agent process (`oem run`). |
| **State & Lifecycle**: Concept promotion, demotion, and deprecation. | **Context Injection**: Prepended rules and system prompt modification. |
| **Retrieval Engine**: Semantic vector and BM25 hybrid search execution. | **Transcript Capture**: Fetching raw dialogue text when the agent exits. |
| **Materialization**: Writing Markdown wiki files (`.oem/wiki/*.md`). | **Doctor Diagnostics**: Verifying installation of files and dependencies. |
| **Telemetry**: Recording session outcomes (`outcomes.jsonl`) and fitness. | **Skill Registration**: Installing yaml declarations into `skills/`. |

---

## 2. Lifecycle Overview

The diagram below outlines the sequential lifecycle of an agent running under OpenEmpiric orchestration:

```text
       ┌────────────────────────┐
       │   1. pre_session Hook  │  ← Setup directory, generate session UUID
       └───────────┬────────────┘
                   │
       ┌───────────▼────────────┐
       │  2. context_injection  │  ← Generate rules/prompts from handoff & registry
       └───────────┬────────────┘
                   │
                   ├─────────────────────────┐
                   ▼                         │ (Tool queries)
       ┌────────────────────────┐            ▼
       │      Agent Process     │   ┌────────────────────────┐
       │   (Claude, Cursor...)  │ ◄─┤  3. knowledge_search   │ (MCP Tool requests)
       └───────────┬────────────┘   └────────────────────────┘
                   │
       ┌───────────▼────────────┐
       │  4. post_session Hook  │  ← Process terminates; read transcripts
       └───────────┬────────────┘
                   │
       ┌───────────▼────────────┐
       │   OEM Session Commit   │  ← Reflect → Materialize → Graph → Re-index
       └────────────────────────┘
```

---

## 3. Lifecycle Phases

### Phase 1: `pre_session`
* **Purpose**: Prepare the workspace environment before the agent runs.
* **Orchestration**: Called immediately before spawning the subprocess.
* **Duties**:
  - Resolve the harness/project path.
  - Generate a unique session identifier (`session_id`).
  - Read from `.oem/session-handoff.md` to identify active targets.
  - Load the active goals, blockers, and recent decisions.

### Phase 2: `context_injection`
* **Purpose**: Pre-load the agent's context window with relevant workspace history.
* **Orchestration**: Runs just before launching the agent subprocess.
* **Duties**:
  - Read active concepts from `.oem/concept_registry.json`.
  - Assemble these concepts and the handoff file into a system prompt wrapper.
  - Write this wrapper to a file location that the agent reads automatically (e.g. `.cursorrules`, `.openempiric_temp_instructions.md`, or environment variables).

### Phase 3: `knowledge_search`
* **Purpose**: Provide real-time knowledge retrieval while the agent is running.
* **Orchestration**: Triggered on-demand via the MCP tool server (`oem serve`).
* **Duties**:
  - Execute dense vector and sparse keyword queries.
  - Merge and rank results using Reciprocal Rank Fusion (RRF).
  - Return formatted summaries directly to the agent's context window.

### Phase 4: `post_session`
* **Purpose**: Capture conversation data and commit new learnings after the agent exits.
* **Orchestration**: Runs immediately after the agent process terminates.
* **Duties**:
  - Read raw dialogue transcripts from the agent's internal log.
  - Clean and format the dialogue text into User/Agent roles.
  - Trigger the OEM commit pipeline (`session_commit`) using the extracted transcript.
  - Record the final outcome (`success`, `failure`, or `abandoned`) and clean up transient environment configurations.

---

## 4. Adapter Contract

Adapters subclass `BaseAdapter` and register their capability sets using YAML metadata in `skills/`.

### Required Hooks
Your adapter must implement these three hooks to function:

1. **`session_start(project_path)`**: Registers session states, restores goals, and initializes session-level telemetry.
2. **`context_injection(project_path)`**: Gathers registry concepts and outputs the system instructions wrapper.
3. **`session_commit(conversation_text, outcome, reason, session_id, project_path)`**: Passes the transcript and outcome back to the OEM Core reflection and materialization services.

### Optional Hooks
These hooks can be added for deeper agent integrations:

* **`install_skill()`**: Installs declarative YAML configurations containing requirements and tool metadata into `.oem/skills/`.
* **`verify_mcp()`**: Diagnoses whether the agent has registered the MCP server. Used by the `oem doctor` command.
* **`parse_transcript(path)`**: Custom file parser for extracting User/Agent text from proprietary logs.

---

## 5. Failure Handling Guidelines

A good adapter ensures that the agent's execution is not blocked or corrupted by downstream knowledge-logging failures.

### Context Injection Failures
* **Guideline**: If compiling context or writing prompt instructions fails (e.g. due to missing files or write permissions), the adapter should **continue to spawn the agent**. It should degrade gracefully by launching the agent without prepended context rather than crashing.

### Retrieval / Search Failures
* **Guideline**: If the embedding model fails to load or ChromaDB is locked during a runtime query, the search tool should return an empty results list gracefully. It must **not throw unhandled exceptions** that terminate the agent's current step.

### Transcript & Commit Failures
* **Guideline**: If the adapter cannot extract the conversation transcript upon agent exit (e.g. the transcript file is empty or corrupted), the adapter must:
  1. Record the outcome as `abandoned` or write a dummy telemetry failure.
  2. Skip reflection.
  3. Emit a warning box to the developer's terminal.
  4. Ensure the original workspace configs are fully restored to prevent environment pollution.

---

## 6. Reference Example: OpenCodeAdapter

The **[OpenCodeAdapter](file:///workspace/openempiric/packages/oem-knowledge/src/oem_knowledge/adapters/opencode/adapter.py)** is the reference implementation of this lifecycle model:

1. **Pre-session**: `cli.py` creates a temporary JSON runtime context file containing active decisions, failures, and goals.
2. **Context Injection**: The plugin loads this context, writes it to `.openempiric_temp_instructions.md`, and registers it in the config's `instructions` array.
3. **MCP Registration**: The config hook registers the `oem serve` MCP command in-memory.
4. **Post-session**: When OpenCode exits, the adapter reads the transcript from the cached logs, commits the session using the reflection service, and cleans up the temporary files.

---

## 7. Future Adapter Ideas

Here are planned integrations and potential implementation vectors for contributors to explore:

### Claude Code (Planned)
* **MCP Registration**: Append the `oem serve` configuration to `~/.config/claude-code/config.json` under the `mcpServers` key.
* **Transcript Collection**: Extract chat history from Claude Code's terminal log cache.

### Cursor (Planned)
* **Rules File**: Injects context directly into `.cursorrules` in the workspace root.
* **MCP Integration**: Uses Cursor's UI settings to point to the local Python MCP server.

### Aider (Planned)
* **Chat History**: Read conversation text directly from `.aider.chat.history.md`.
* **Config Integration**: Configure the MCP server via `--mcp-server` arguments in `.aider.conf.yml`.
