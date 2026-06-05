# P0 Technical Execution Plan

## Overview

Four P0 items from the road to zero-touch autopilot. Each item has a clear
entry point, file changes, and test strategy. Items are ordered by dependency.

---

## P0.1 — Auto-Init Wizard

**Problem**: First-time user runs `oem run opencode` but hits missing `.oem/`
and uncached embedding model. Must manually `oem init` + `oem warmup`.

**Solution**: `run_agent()` detects incomplete workspace and completes setup
automatically.

### File: `packages/oem-knowledge/src/oem_knowledge/runtime/runner.py`

Insert after line 22 (after adapter resolution), before plugin linking:

```python
# P0.1 — Auto-init wizard: bootstrap project if first run
_ensure_workspace_ready(eng, project, adapter)
```

New module-level function:

```python
def _ensure_workspace_ready(eng: KnowledgeEngine, project: str | None, adapter: BaseAdapter) -> None:
    """Auto-init project and warm up model if needed. Idempotent."""
    harness = eng._resolve_harness(project)
    is_new = not (harness / "concept_registry.json").exists()

    if is_new:
        logging.info("First-run detected — bootstrapping project...")
        eng.init_project(project or ".")

    # Warmup embedding model if not cached
    try:
        from fastembed import TextEmbedding
        TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
    except Exception:
        logging.info("Embedding model not cached — running warmup...")
        print("[oem] Downloading embedding model (one-time)...")
        eng.warmup()

    # Verify plugin linked
    if not adapter.verify_mcp():
        logging.info("Plugin not linked — installing...")
        adapter.install_skill()
        _link_plugin()  # existing symlink logic extracted
```

Extract the plugin-symlink logic (lines 29-45) into a `_link_plugin()` helper
so it can be called independently.

### File: `packages/oem-knowledge/src/oem_knowledge/engine.py`

Make `init_project()` idempotent — if `.oem/` already exists, skip directory
creation but still ensure skill file and state files exist. Current code
already does file-existence checks, so this should be safe.

Add `is_initialized()` method:

```python
def is_initialized(self, project: str | None = None) -> bool:
    try:
        harness = self._resolve_harness(project)
        return (harness / "concept_registry.json").exists()
    except Exception:
        return False
```

### Tests: `tests/test_auto_init.py` (new file)

```python
class TestAutoInitWizard:
    def test_is_initialized_returns_false_for_new_project(self, tmp_path):
        eng = KnowledgeEngine(project_path=tmp_path)
        assert not eng.is_initialized(str(tmp_path))

    def test_init_project_is_idempotent(self, engine):
        res1 = engine.init_project(str(engine.project_path))
        res2 = engine.init_project(str(engine.project_path))
        assert res1["status"] == "success"
        assert res2["status"] == "success"

    def test_ensure_workspace_ready_bootstraps(self, tmp_path):
        eng = KnowledgeEngine(project_path=tmp_path)
        _ensure_workspace_ready(eng, str(tmp_path), mock_adapter)
        assert eng.is_initialized(str(tmp_path))
```

---

## P0.2 — Plugin Link Health Check + Auto-Repair

**Problem**: Plugin symlink breaks silently between sessions. Agent spawns
without OEM plugin, context injection fails silently.

**Solution**: Add `verify_health()` to adapter interface. Runner checks health
before each session and auto-repairs.

### File: `packages/oem-knowledge/src/oem_knowledge/adapters/base.py`

Add new method:

```python
def verify_health(self) -> tuple[bool, str]:
    """Check if adapter runtime environment is healthy.

    Returns (healthy, message) tuple.
    Default: checks MCP registration.
    """
    healthy = self.verify_mcp()
    return healthy, "MCP registered" if healthy else "MCP not registered"
```

### File: `packages/oem-knowledge/src/oem_knowledge/adapters/opencode/adapter.py`

Override:

```python
def verify_health(self) -> tuple[bool, str]:
    # 1. Check plugin file exists and is valid
    plugins_dir = Path(os.environ.get(
        "OPENCODE_PLUGINS_DIR",
        Path.home() / ".config" / "opencode" / "plugins"
    ))
    plugin_dest = plugins_dir / "openempiric.ts"
    if not (plugin_dest.exists() or plugin_dest.is_symlink()):
        return False, "Plugin openempiric.ts not found"
    # 2. Check plugin content is recent (not stale)
    #    Compare mtime with expected source
    from oem_knowledge.runtime.config import _REPO_ROOT
    plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
    if plugin_src.exists():
        if plugin_dest.is_symlink():
            try:
                if plugin_dest.readlink() != plugin_src.resolve():
                    return False, "Plugin symlink points to wrong location"
            except Exception:
                return False, "Plugin symlink broken"
    return True, "Plugin healthy"
```

### File: `packages/oem-knowledge/src/oem_knowledge/runtime/runner.py`

In `run_agent()`, after `_ensure_workspace_ready()` and before plugin linking:

```python
# P0.2 — Verify adapter health, auto-repair if needed
healthy, msg = adapter.verify_health()
if not healthy:
    logging.warning("Adapter health check failed: %s — attempting repair", msg)
    _link_plugin()  # re-link plugin
    adapter.install_skill()  # re-install skill YAML
    healthy, msg = adapter.verify_health()
    if not healthy:
        logging.warning("Adapter repair failed: %s — continuing without plugin", msg)
```

### Tests: `tests/test_auto_init.py`

```python
class TestPluginHealthCheck:
    def test_health_returns_false_when_plugin_missing(self, engine, tmp_path):
        adapter = OpenCodeAdapter(engine, str(tmp_path))
        healthy, msg = adapter.verify_health()
        assert not healthy  # no plugin in tmp_path
        assert "not found" in msg

    def test_health_returns_true_when_plugin_present(self, engine, monkeypatch):
        # Mock the plugin file existing
        adapter = OpenCodeAdapter(engine, "/tmp/fake_project")
        monkeypatch.setattr(
            "oem_knowledge.adapters.opencode.adapter.Path.exists",
            lambda self: True,
        )
        monkeypatch.setattr(
            "oem_knowledge.adapters.opencode.adapter.Path.is_symlink",
            lambda self: True,
        )
        healthy, msg = adapter.verify_health()
        assert healthy
```

---

## P0.3 — Auto-Recovery on `oem run`

**Problem**: If agent crashes, user sees a warning and must manually run
`oem recover`. Worst case: user ignores warning, starts new session, old
session knowledge is lost.

**Solution**: At `run_agent()` entry, detect stale `active_session.json` and
auto-recover before creating new session. Recovery is silent — no user prompt.
Only if recovery fails do we warn.

### File: `packages/oem-knowledge/src/oem_knowledge/runtime/runner.py`

Insert at top of `run_agent()`, before anything else:

```python
def run_agent(agent_name: str, eng: KnowledgeEngine, project: str | None = None):
    # P0.3 — Auto-recover stale sessions before starting new one
    _auto_recover_stale_session(eng, project)

    # ... rest of existing code ...
```

New private function:

```python
def _auto_recover_stale_session(eng: KnowledgeEngine, project: str | None = None) -> None:
    """Detect and auto-recover any unfinished session. Silent if nothing to do."""
    try:
        harness = eng._resolve_harness(project)
        active_file = harness / "state" / "active_session.json"
        if not active_file.exists():
            return

        session_state = SessionState.load(active_file)
        if not session_state:
            return

        if session_state.status == "completed":
            active_file.unlink(missing_ok=True)
            return

        session_id = session_state.session_id
        agent_name = session_state.agent
        logging.info("Auto-recovering stale session %s (state=%s)", session_id, session_state.status)

        from oem_knowledge.adapters import get_adapter
        adapter = get_adapter(agent_name, eng, project)

        # Discover transcript
        chat_text = ""
        if session_state.transcript_path:
            t_file = Path(session_state.transcript_path)
            if t_file.exists():
                chat_text = adapter.parse_transcript(t_file) if hasattr(adapter, "parse_transcript") else t_file.read_text()

        if not chat_text and hasattr(adapter, "discover_latest_transcript"):
            latest_t = adapter.discover_latest_transcript()
            if latest_t:
                chat_text = adapter.parse_transcript(latest_t)

        if not chat_text:
            chat_path = harness / "state" / f"chat_{session_id}.md"
            if chat_path.exists():
                chat_text = chat_path.read_text()
                try:
                    chat_path.unlink()
                except Exception:
                    pass

        if chat_text:
            commit_res = eng.session_commit(project, conversation_text=chat_text, session_id=session_id)
            eng.record_outcome("success", session_id=session_id, project=project)
            logging.info("Auto-recovery complete: report=%s events=%d",
                         commit_res.get("report_path", "?"),
                         len(commit_res.get("canonical_events", [])))
        else:
            logging.info("No transcript found for stale session — recording as abandoned")
            eng.record_outcome("abandoned", session_id=session_id, project=project)

        # Cleanup
        from oem_knowledge.tools.metrics import update_metrics_file
        try:
            metrics_file = harness / "state" / "metrics.json"
            update_metrics_file(metrics_file, {"sessions_recovered": 1})
        except Exception:
            pass

        active_file.unlink(missing_ok=True)

    except Exception as e:
        logging.warning("Auto-recovery failed: %s — user can still run oem recover manually", e)
```

### File: `packages/oem-knowledge/src/oem_knowledge/cli.py`

Update the unfinished-session warning (lines 194-214). Currently it warns for
`status`, `stats`, and `run` commands. Change to:

- `status`, `stats`: still warn (user explicitly asked for info)
- `run`: **remove the warning** (runner handles auto-recovery now)

```python
if args.command in ("status", "stats"):
    # Show warning for informational commands only
    ...existing warning code...
elif args.command == "run":
    # Suppress warning — runner.py auto-recovers
    pass
```

### Tests: `tests/test_auto_init.py`

```python
class TestAutoRecovery:
    def test_no_stale_session_does_nothing(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        if active_file.exists():
            active_file.unlink()
        _auto_recover_stale_session(engine, str(tmp_path))
        # Should not raise or create anything new
        assert not active_file.exists()

    def test_recovery_with_chat_transcript(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        ss = SessionState.create(
            session_id="stale_001", agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat_stale.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        chat_file = tmp_path / "chat_stale.md"
        chat_file.write_text("Fixed the parser module.")

        _auto_recover_stale_session(engine, str(tmp_path))

        assert not active_file.exists()
        # Verify outcomes recorded
        outcomes_file = harness / "state" / "outcomes.jsonl"
        assert outcomes_file.exists()

    def test_recovery_with_no_transcript_records_abandoned(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        ss = SessionState.create(
            session_id="stale_002", agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "nonexistent.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "running"
        ss.save(active_file)

        _auto_recover_stale_session(engine, str(tmp_path))

        assert not active_file.exists()
        outcomes = harness / "state" / "outcomes.jsonl"
        assert outcomes.exists()
        last_line = outcomes.read_text().strip().splitlines()[-1]
        assert json.loads(last_line)["outcome"] == "abandoned"

    def test_completed_session_is_cleaned_up(self, engine, tmp_path):
        harness = engine._resolve_harness(str(tmp_path))
        active_file = harness / "state" / "active_session.json"
        ss = SessionState.create(
            session_id="done_001", agent="opencode",
            project=str(tmp_path),
            transcript_path=str(tmp_path / "chat.md"),
            context_path=str(tmp_path / "ctx.json"),
            temp_instructions=str(tmp_path / "inst.md"),
        )
        ss.status = "completed"
        ss.save(active_file)

        _auto_recover_stale_session(engine, str(tmp_path))
        assert not active_file.exists()
```

---

## P0.4 — Memory-Aware Agent Instructions

**Problem**: Agent instructions frame OEM as a search requirement ("search
before answering"), creating unnecessary overhead. The agent should treat OEM
as its long-term memory — context injection loads what it already knows, and
`knowledge_search` is for retrieving details when needed.

**Solution**: Replace "force retrieval before answering" with memory-aware
instructions. OEM loads what the agent already knows at session start. The
agent uses `knowledge_search` when it needs information it doesn't have — not
before every response.

### File: `packages/oem-knowledge/src/oem_knowledge/runtime/context.py`

Replace `retrieval_instruction` with `memory_context` key:

```python
return {
    "active_concepts": active_concepts,
    "active_decisions": active_decisions,
    "relevant_failures": relevant_failures,
    "open_questions": open_questions,
    "memory_context": (
        "OEM is your long-term memory for this project. "
        "The concepts, decisions, failures, and questions above represent "
        "what you already know. Use the knowledge_search tool to retrieve "
        "details on any of them. You do not need to search before every "
        "response — only when you need information you do not already have."
    ),
}
```

### File: `plugins/openempiric.ts` — `ContextAssembler.assemble()`

Replace "Knowledge Search" with "Memory Context" section:

```typescript
instContent += "\n## Memory Context\n";
instContent += "OEM is your long-term memory for this project. ";
instContent += "The concepts and context above represent what you already know. ";
instContent += "Use `knowledge_search` when you need details on a specific concept. ";
instContent += "You do not need to search before every response — only when you lack information.\n";
```

### File: `packages/oem-knowledge/src/oem_knowledge/adapters/opencode/adapter.py`

Update skill YAML: remove `search_existing_knowledge_before_work` and
`knowledge_search_before_work` from `required`. Frame OEM as memory:

```yaml
required:
  - knowledge_search
  - knowledge_session_start
  - knowledge_capture_after_work
best_practices:
  - Treat OEM as your long-term memory for the project.
  - Context is pre-loaded for you — you already know the active concepts.
  - Use knowledge_search when you need details, not before every response.
```

### Tests: `tests/test_auto_init.py`

```python
class TestMemoryContext:
    def test_context_contains_memory_key(self, engine, tmp_path):
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)
        assert "memory_context" in ctx
        assert "long-term memory" in ctx["memory_context"].lower()
        assert "search" in ctx["memory_context"].lower()

    def test_context_mentions_retrieval_when_relevant(self, engine, tmp_path):
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(engine)
        mc = ctx["memory_context"].lower()
        assert "do not need to search before every response" in mc
```

### Verification

Manual: `oem run opencode` → temp instructions file contains "## Memory Context"
section instead of "## Knowledge Search".

---

## Implementation Order

```
Step 1: P0.1 — Auto-init wizard (runner.py + engine.py)
Step 2: P0.2 — Plugin health check (adapter/base.py + adapter/opencode/adapter.py)
Step 3: P0.3 — Auto-recovery (runner.py + cli.py)
Step 4: P0.4 — Auto-retrieval prompt (context.py + openempiric.ts)
Step 5: Tests (test_auto_init.py)
```

Steps 1-4 are independent enough that they can be implemented in a single
session. Step 5 wraps them all together.

---

## Edge Cases & Risks

| Risk | Mitigation |
|------|-----------|
| Auto-init tries to init a project that exists | `init_project` is already idempotent (checks file existence) |
| Model download fails (no internet) | `warmup()` wrapped in try/except; session continues without model |
| Auto-recovery deletes active session then fails | Recovery deletes only after successful commit; failed recovery keeps file |
| Auto-recovery on interrupted auto-recovery | Active file deleted on success; if crash mid-recovery, next startup retries |
| Plugin health check false positive | Health check validates both existence AND content (mtime comparison) |
| Multiple stale sessions in queue | Auto-recovery handles one at a time; each session run clears previous |

---

## Rollout

Since these are all internal infrastructure changes (no user-facing API
changes), they ship as part of the next `oem-knowledge` release. The user
experience change is:

- **Before**: `oem init && oem warmup && oem run opencode` (with crash warnings)
- **After**: `oem run opencode` (just works, no warnings)
