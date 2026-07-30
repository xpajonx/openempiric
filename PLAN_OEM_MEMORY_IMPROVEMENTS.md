# Plan: Making OpenEmpiric a True AI Agent Persistence Memory

## What We Learned

### OEM already does more than we initially thought

The reviewer caught three misdiagnoses:

- **Context injection exists**: `_compile_oem_context()` in `runtime/context.py` builds active concepts, decisions, and failures. `current_directives.md` is auto-generated at session start.
- **Memory type classification exists**: `classify_memory_type()` in `memory_ranking.py` already has 7 types (decision, failure, outcome, technical_handoff, workaround, debug_note, command_log) with 15+ tuned boost/penalty weights.
- **Concept consolidation exists**: `state.consolidate()` and `evolution.py` handle merge proposals and deduplication.

### The real gaps

1. **No inline memory creation tool** — agents must wait until session end to persist anything. Mem0 has `add()`. Claude Code has auto-memory that triggers on corrections. OEM has no equivalent.
2. **Dense LLM extraction is `enabled: false`** by default — this means the reflection pipeline relies on structured markers only, producing thin events.
3. **No memory scope model** — no distinction between project-level, user-level, or session-level memory. This matters increasingly as agents work across projects.
4. **The session-end handler is a monolith** — `engine.py:644-1151` is ~500 lines with no transaction boundary. Partial failure = inconsistent state. This is the highest-risk code in the system.
5. **Search relevance is noisy** — `is_weak_memory_match()` is a band-aid on a chunking problem. The preflight search for "make OEM more useful to AI agents" returned raw command logs instead of architectural insights.
6. **Source code index is BM25-only** — while memory concepts get hybrid (BM25 + dense) search, source code does not.
7. **Embedding model is static** — `BAAI/bge-small-en-v1.5` hardcoded, no versioning, no re-embedding strategy.
8. **No user preference tracking** — Claude Code's auto-memory specifically captures user preferences as a first-class memory type. OEM doesn't distinguish "this is how you like things done" from other facts.
9. **The reflection-search-reinforcement loop has no balancing** — the only dampener is `is_weak_memory_match()`. Without stronger gates, any new memory creation path risks runaway low-quality memory growth.

---

## Approach: Phased Delivery, Highest Leverage First

| Phase | Change | Rationale |
|-------|--------|-----------|
| **0** | Strengthen quality gate for inline memory creation | Prevents reinforcement loop from runaway before any new memory paths |
| **1** | Add `knowledge_add_memory` tool routed through existing reflection validation | Highest agent-facing value, lowest risk |
| **2** | Decompose session-end handler into a pipeline with rollback | Every other feature depends on reliable commits |
| **3** | Post-session "dream" (consolidation + decay) in-process | Reuses existing lock infrastructure |
| **4** | Add scope model (user/project/session) to `ConceptData` and search index | Enables multi-user without architectural change |
| **5** | Source code embedding + temporal filters + search quality improvements | Closes the BM25-only gap for source corpus |

Each phase is independently shippable. Sequence matters more than scope.

---

## Phase 0: Strengthen the Quality Gate

### Goal

Before adding any new memory creation path, ensure the rejection filter (`is_weak_memory_match()` in `preflight/router.py`) is strong enough to prevent runaway low-quality memory growth.

### What to change

1. **`services/state.py`** — add an ingestion-time filter that rejects events from being chunked into the vector store when their content matches command-log patterns (lines starting with `Command \`, `Exit code`, `Output:`).
   - Keep the `memory_quality_score()` helper but apply it at ingestion time, not search time.
   - The `is_weak_memory_match()` function at search time stays as-is for now (address search relevance properly in Phase 5).

2. **`memory_ranking.py`** — add `LOW_QUALITY_PENALTY = -8.0` for entries that fail the strengthened gate, applied at classification time rather than search time.

3. **`tests/test_memory_ranking.py`** — test the new rejection patterns against known low-quality entries.

### Acceptance criteria

- A query like "npm install ran with exit code 0" is rejected from preflight context
- A query like "Decided to use hybrid search because BM25 missed semantic matches" passes through
- Existing 873 tests still pass

---

## Phase 1: `knowledge_add_memory` Tool

### Goal

Give agents a lightweight way to persist a memory inline during active work, without waiting for session end.

### What to change

1. **`tools/lifecycle.py`** — add new MCP tool registration:
   ```python
   @mcp_tool("knowledge_add_memory")
   def knowledge_add_memory(
       memory_type: Literal["decision", "observation", "preference", "failure", "workaround"],
       content: str,
       scope: Literal["project", "user", "session"] = "project",
       confidence: int = 3,
       evidence: str = "",
   ) -> dict:
       ...
   ```

2. **`services/reflection.py`** — add `add_inline_memory()` method:
   - Construct a `KnowledgeEvent` from the arguments
   - Route through `_validate_and_normalize_event()` for sanitization
   - Add to `seen` set for deduplication
   - Assign `source: "inline_agent"` tag
   - Append to `events.jsonl` under lock
   - If `confidence >= 3`, auto-accept; otherwise, flag for review
   - Return the created event ID and whether it was auto-accepted
   - Add `scope` field to `KnowledgeEvent` model (not just `ConceptData`). Default value `"project"`. This is critical: inline memories persist via events, so events must carry scope.
   - Add rate limiting: max 20 inline memories per session. Enforce in `add_inline_memory()`.
   - Route inline memories through the same `is_weak_memory_match()` quality gate that preflight uses. Reject inline memories that fail the gate (return error to agent).
   - Require `evidence` to be non-empty when `confidence >= 3`. Reject auto-accept with empty evidence.
   - Add these tests to Phase 1 test plan.

3. **`models.py`** — add `scope` field to `ConceptData`:
   ```python
   scope: Literal["project", "user", "session"] = "project"
   ```

4. **`memory_ranking.py`** — extend `classify_memory_type()` with:
   - `preference` pattern: `r"\b(prefer|like|always|never|don't|hate|style|convention)\b"`
   - `BOOST_PREFERENCE = 4.0` for user-scoped preference memories
   - `BOOST_USER_SCOPE = 3.0` when a memory's scope matches the current user
   - `episodic` pattern: `r"\b(last time|previously|tried|attempted|before)\b"`

5. **`tests/test_lifecycle.py`** — test:
   - Inline memory creation succeeds and returns event ID
   - Deduplication works (same content twice → one event)
   - Low-confidence entries are flagged
   - `scope` field persists and round-trips
   - Routing through validation rejects malformed input

### Acceptance criteria

- Agent can call `knowledge_add_memory("decision", "Use uv over pip", scope="user", confidence=4)`
- The memory appears in `knowledge_search` results immediately
- Duplicate content is deduplicated
- Low-confidence entries (confidence < 3) are stored but flagged
- Existing tests pass

---

## Phase 2: Session-End Pipeline Decomposition

### Goal

Break the 500-line monolith (`engine.py:644-1151`) into named phases with explicit rollback on failure.

### What to change

1. **New file: `runtime/commit_pipeline.py`** — the pipeline orchestrator:
   ```python
   class CommitPipeline:
       phases = ["reflect", "validate", "append_events", "write_report", "materialize", "index", "cleanup"]
       
        def run(self, session_state, conversation_text, events):
            staging = StagingArea()
            for phase in self.phases:
                try:
                    result = self._run_phase(phase, staging)
                    staging.commit_phase(phase, result)
                except Exception as e:
                    staging.rollback_to(phase)
                    raise CommitRollbackError(phase, e)

    **Crash recovery (write-ahead intent log):**
    - Before the pipeline runs, write a write-ahead intent log at `.oem/.staging/intent.json` recording the current phase.
    - After each phase completes, update the intent log.
    - On pipeline start, check for uncompleted intent from a prior crash. If found, resume from the recorded phase or rollback.
    - The `events.jsonl` file is append-only. The `StagingArea` approach using atomic renames does NOT apply here. Instead, after `append_events`, write a marker in the intent log recording the byte offset written. On resume, truncate to that offset if needed.
   ```

2. **`engine.py`** — replace the monolithic `session_commit()` with:
   - Construct a `CommitPipeline`
   - Run it under existing `FileLock`
   - Return phase timings and any rollback info
   - On rollback, return a `status: "recovered"` response with the phase that failed

3. **`services/state.py`** — add:
   - `StagingArea` class: writes to `.oem/.staging/` before committing
   - `commit_phase()`: atomically rename staging files to final location
   - `rollback_to()`: remove all staging files for phases after the failed one

4. **`tests/test_engine.py`** — test:
   - Full pipeline succeeds end-to-end
   - Failure in `materialize` phase rolls back events
   - Failure in `index` phase leaves materialized concepts intact
   - Phase timings are reported correctly
   - Concurrent sessions don't collide (existing FileLock behavior)

### Acceptance criteria

- If `materialize` fails, `events.jsonl` is not corrupted
- If `index` fails, previously materialized concepts are still searchable via fallback BM25
- Phase timings are still returned in the `knowledge_session_end` response
- All existing tests pass

6. **`services/reflection.py`** — refactor `reflect_session()` (lines ~912-1038) to use the `CommitPipeline`. Currently `reflect_session()` duplicates session_end logic. After Phase 2, `reflect_session()` must route through the same pipeline or be deprecated in favor of calling `session_end()`.

---

## Phase 3: Post-Session "Dream" — Consolidation + Decay

### Goal

Run consolidation and decay in-process after `knowledge_session_end`, not as a background daemon.

### What to change

1. **`runtime/commit_pipeline.py`** — add optional `dream` phase after cleanup:
   - Triggered by `auto_dream: true` in reflection config
   - Runs `consolidate()` from `state.py`
   - Runs `apply_decay()` from `evolution.py`
   - Records dream actions in `state/dream_log.jsonl`

2. **`services/state.py`** — enhance `consolidate()`:
   - Merge concepts with `similarity >= 0.9` (tighter threshold than current `0.85`)
   - Promote concepts with `evidence_count >= 5` and `sessions >= 3` from `emerging` to `validated`
   - Archive concepts untouched for 30+ sessions to `needs_review`
   - Never merge user-confirmed concepts without review flag

3. **`services/evolution.py`** — add `apply_decay()`:
   - Compute `decay_score = confidence * exp(-0.693 * days_since_last_evidence / half_life)`
   - `half_life` configurable (default 30 days)
   - Apply decay to `confidence` field (floor at 1)
   - Log decay actions in dream log
   - `apply_decay()` must run under the same `FileLock` as session_end, OR under a separate `dream_lock`. It writes back to `state/registry.json` via `state.update_concept()`.
   - Define the registry write path explicitly: after computing decay scores, call `self.state.update_concept(concept_id, confidence=decayed_confidence)` for each affected concept.
   - The dream phase re-acquires the lock if released, or runs inline before lock release.

4. **`models.py`** — add fields to `ConceptData`:
   ```python
   last_accessed_at: float = Field(default_factory=time.time)
   access_count: int = 0
   ```

5. **`reflection.yml`** — add config:
   ```yaml
   auto_dream:
     enabled: false
     half_life_days: 30
     consolidate_threshold: 0.9
     promote_threshold:
       evidence_count: 5
       session_count: 3
   ```

6. **`tests/test_evolution.py`** — test:
   - Decay reduces confidence over time for untouched concepts
   - Consolidation merges near-duplicate concepts
   - Promotion fires when thresholds are met
   - Dream log records all actions
   - User-confirmed concepts are not auto-merged

### Acceptance criteria

- After enabling `auto_dream: true`, running two sessions produces consolidation actions
- A concept untouched for 60 days has its confidence decayed from 5 to ~1
- The dream log shows what was merged, promoted, or archived
- User-confirmed concepts (scope: user) are never auto-merged

---

## Phase 4: User Preference Tracking + Scope Model

### Goal

Introduce user-level memory tracking and scope-aware retrieval.

### What to change

1. **`models.py`** — extend `scope` field:
   - Already added in Phase 1; here we add `agent_scope` for multi-agent scenarios
   - Add `created_by: str | None = Field(default=None, description="Agent identifier that created this concept")`

2. **`memory_ranking.py`** — add scope-aware boosting:
   - `BOOST_USER_SCOPE = 3.0` — memories scoped to current user get priority in user-initiated queries
   - `BOOST_SESSION_SCOPE = 2.0` — recent session memories for continuity
   - `BOOST_PROJECT_SCOPE = 1.0` — default, no boost

3. **`services/state.py`** — scope-aware resolution:
   - `_resolve_concept()` accepts optional `scope_filter` parameter
   - User registry at `~/.config/openempiric/users.json` (out of project, not committed)
   - User identity detection: `OEM_USER_ID` env var, MCP session metadata, or git `user.email`. 
   - Precedence rule: `OEM_USER_ID` env var > MCP session metadata > git `user.email`. 
   - When sources disagree, log a warning at WARNING level with all conflicting values.
   - Add test: `test_user_identity_precedence` that simulates conflicting sources and verifies correct precedence.
   - Note: `git user.email` is machine-specific. This is a known limitation documented in the code.

4. **`tools/lifecycle.py`** — `knowledge_add_memory` accepts `scope: "user"`:
   - User-scoped events MUST live in a separate file: `~/.config/openempiric/user_events.jsonl`
   - Project-scoped events stay in project's `events.jsonl`.
   - `_compile_oem_context()` loads from BOTH files and merges them, marking user-scoped entries distinctly ("User preference" vs "Project context").
   - This ensures project isolation: working on Project A does not inject Project A decisions into Project B.

5. **`runtime/context.py`** — `_compile_oem_context()`:
   - Include user-scoped concepts when available
   - Mark them distinctly so agents know "this is about the user, not this project"

6. **`server.py`** — detect agent identity:
   - Read `OEM_AGENT_ID` env var or MCP session metadata
   - Pass to engine for `created_by` tracking

7. **`tests/test_models.py`** — scope field backward compatibility:
   - Concepts without `scope` field default to `project`
   - Round-trip serialization preserves scope

### Acceptance criteria

- User-scoped memories (`scope: "user"`) appear in preflight for any project the user works on
- Project-scoped memories remain isolated to their project
- User identity detection works via env var, git config, or MCP metadata
- Existing concepts without `scope` field default to `project` and work unchanged

---

## Phase 5: Source Code Embedding + Search Quality Improvements

### Goal

Add dense embeddings to source code index, improve chunk quality, add temporal filters, and make the embedding model switchable.

### What to change

1. **`services/source_corpus.py`** — add embedding generation:
   - When `[semantic]` extra is installed, generate embeddings for source chunks
   - Reuse `vector_store.py` infrastructure with a separate `source_code` collection/table
   - Graceful fallback to BM25 if embedding model unavailable

2. **`services/search.py`** — add filter parameters to `search()`:
   ```python
   def search(
       query: str,
       scope: str | None = None,          # "project" | "user" | "session"
       memory_type: str | None = None,    # "decision" | "failure" | "preference" | ...
       since: str | None = None,          # ISO 8601 timestamp
       until: str | None = None,
       limit: int = 10,
   ) -> list[SearchResult]:
   ```

3. **`memory_ranking.py`** — search quality refinements:
   - `BOOST_TEMPORAL_MATCH = 2.0` — recency-weighted boost for entries matching the `since` window
   - Reduce `PENALTY_COMMAND_LOG` from `-5.0` to `-3.0` when the command log matches an exact identifier in the query (it might be relevant)
   - Add `BOOST_EXACT_DECISION_PHRASE = 5.0` for entries matching "we decided to X" patterns
   - Store `memory_type` in the chunk metadata JSON at index time (not computed at query time). This is required for the `search()` filter parameters (`memory_type` filter) to work.
   - Add a migration: existing chunks without `memory_type` metadata get recomputed on first search and cached.

4. **`tools/concepts.py`** — add filter parameters to `knowledge_search` tool:
   - Expose `scope`, `memory_type`, `since`, `until` as optional tool parameters

5. **`engine.py`** — add `oem config embedding set-model <model>`:
   - Switch embedding model with automatic re-indexing
   - Store `model_version` in vector store metadata
   - Re-indexing must be a background operation, not blocking `session_end`.
   - Add `oem config embedding set-model --dry-run` that reports how many chunks would be re-embedded.
   - Add progress reporting (checkpoint file with current/total).
   - Add confirmation prompt for interactive use.

6. **`tests/test_source_corpus.py`** — test:
   - Source code embedding generation works
   - Hybrid search (BM25 + dense) returns better results than BM25 alone for semantic queries
   - Graceful fallback when embedding model unavailable
   - Temporal filters correctly exclude entries outside the window

### Acceptance criteria

- Source code search returns semantically relevant results even when the query doesn't contain exact identifiers
- `knowledge_search("how do we handle auth?", memory_type="decision")` returns only decision entries
- `knowledge_search("recent changes", since="2026-07-01")` returns only entries after that date
- Switching embedding model triggers re-index without data loss
- Existing tests pass

---

## Reviewer Findings Incorporated

These issues were raised by the reviewer council and addressed:

| Issue ID | Severity | Finding | Resolution |
|----------|----------|---------|------------|
| P1 | blocking | No crash recovery for append-only events.jsonl | Added write-ahead intent log to Phase 2 |
| P2 | blocking | scope missing from KnowledgeEvent | Added scope to KnowledgeEvent in Phase 1 |
| P3 | blocking | apply_decay() no persistence path | Defined registry write path + lock design in Phase 3 |
| R2 | blocking | No user identity precedence | Added OEM_USER_ID > MCP > git precedence rule in Phase 4 |
| R3 | blocking | Auto re-index foot-gun | Added background operation, --dry-run, confirmation in Phase 5 |
| R4 | blocking | Auto-accept enables memory flood | Added quality gate routing, rate limiting, evidence requirement in Phase 1 |
| S1 | blocking | User events in same jsonl breaks isolation | User events now live in ~/.config/openempiric/user_events.jsonl |
| S2 | blocking | reflect_session() bypasses pipeline | Phase 2 now refactors reflect_session() to use CommitPipeline |
| S4 | blocking | memory_type not stored at index time | Store memory_type in chunk metadata in Phase 5; add migration path |
| R1 | declined | More regex on is_weak_memory_match() | Replaced with ingestion-time filtering instead |
| S3 | declined | Three different merge thresholds | Different contexts (auto-merge vs manual) justify different thresholds |

---

## What We Will NOT Do

| Item | Reason |
|------|--------|
| Enable dense LLM extraction by default | Degrades experience for users without LLM providers; creates unexpected costs. Instead: `oem setup llm` wizard (out of scope). |
| Generate AGENTS.md automatically | OEM already generates `current_directives.md`. A second auto-generated file creates a feedback loop with the instruction ledger. |
| Build a background daemon for "auto-dream" | No process infrastructure exists. Consolidation runs in-process at session end. |
| Replace the existing memory type system | Extend `classify_memory_type()` with new types rather than building a parallel taxonomy. |
| Add multi-agent shared memory with a shared vector store | Concurrency hazards, poisoning risk, provenance complexity. Keep memory scoped per-project-per-user until explicit sharing is needed. |

---

## Verification Strategy

Each phase includes:

1. **Tests written first** — unit tests for new models, integration tests for pipeline behavior
2. **Existing test suite must stay green** — 873 tests, regression protection
3. **Manual smoke test** — run `oem knowledge add-memory`, verify it survives session end/start cycle
4. **Preflight quality audit** — after each phase, run preflight on known tasks and verify relevance improved

---

## Timeline Estimate

| Phase | Scope | Approximate effort |
|-------|-------|-------------------|
| 0 | Strengthen quality gate | 1 session |
| 1 | `knowledge_add_memory` + quality gate | 2-3 sessions |
| 2 | Session-end pipeline decomposition | 2-3 sessions |
| 3 | Post-session dream | 2-3 sessions |
| 4 | User preferences + scope | 2-3 sessions |
| 5 | Source embedding + search quality | 2-3 sessions |

**Total: ~11-16 sessions for the full plan.** Each phase is independently shippable.

---

## Competitive Landscape Reference

| Feature | Claude Code | Mem0 | OEM (current) | OEM (after plan) |
|---------|-------------|------|---------------|------------------|
| Auto-inject context | ✅ CLAUDE.md + MEMORY.md | ✅ add() + search() | ✅ current_directives.md | ✅ Enhanced |
| Inline memory creation | ✅ Auto-memory | ✅ add() | ❌ | ✅ Phase 1 |
| Memory type taxonomy | ✅ user/feedback/project/reference | ✅ implicit via add() | ✅ 7 types (hidden) | ✅ Explicit with scope |
| User preference tracking | ✅ Auto-memory | ✅ user-scoped add() | ❌ | ✅ Phase 4 |
| Consolidation / forgetting | ✅ Auto-dream | ✅ update() | ✅ Manual consolidate() | ✅ Automated in Phase 3 |
| Search quality | ✅ LLM-ranked | ✅ Hybrid vector | ✅ BM25 + hybrid | ✅ Enhanced in Phase 5 |
| AGENTS.md integration | ✅ Reads CLAUDE.md | ❌ | ❌ | ✅ Richer directives |
| Source code memory | ❌ | ❌ | ✅ BM25 only | ✅ Hybrid in Phase 5 |
| Local-first | ✅ Filesystem | ✅ Self-hosted option | ✅ Always | ✅ Always |
