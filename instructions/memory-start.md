## openempiric Memory Protocol

### 1. Plugin Validation (Mandatory Session Start)
At the start of every session, you **MUST** verify that the `openempiric` plugin is active:
- Read `~/.config/opencode/opencode.jsonc` (or equivalent config).
- Ensure `"openempiric"` is registered inside the `"plugin"` array.
- If it is missing, advise/ask to add it, or write/register it so that the openempiric MCP tools are available.

### 2. Session Start & Context Loading
- Call `knowledge_session_start(project="<path>")` to load existing wiki paths and log contexts.
- Call `knowledge_search(query="<topic>", project="<path>")` to search for relevant historical insights.
- The retrieved results represent your baseline context for the conversation. Do not repeat searches unless the topic shifts.

### 3. Session End & Organization
- When the session is completed or the user signals session-end, you **MUST** call `knowledge_session_commit(project="<path>")`.
- This runs the reflection pipeline (reflect → materialize → graph rebuild → re-index) to durably save learnings in the `.oem/` directory.

### 4. Orchestration Rule
- You **MUST** use the `harness-orchestrator` tools (`harness_run_opencode`, `harness_run_tasks`) for any parallel subtasks, self-healing, or subagent tasks to ensure structured execution telemetry is captured.
