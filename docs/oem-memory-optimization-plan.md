# OpenEmpiric memory optimization plan

**Status:** plan only. This document does not change runtime behavior or edit `.oem` state.

**Date:** 2026-09-04

## Outcome

Make OEM a memory layer that an AI coding agent can trust during a live task:

1. Retrieval returns task-relevant decisions, failures, preferences, and handoffs instead of transcript debris.
2. New memories retain their type, scope, project, session, and provenance.
3. Session commits are idempotent and recoverable without losing verified events.
4. User, project, and session memory cannot cross their boundaries by accident.
5. Source-code search stays separate from learned memory and gains semantic recall without weakening exact path lookup.
6. Every change leaves enough telemetry to tell whether agent work improved.

## Current evidence

This is a read-only snapshot of the repository and its local memory state. The data below is not a target for manual cleanup. Any future migration must be an OEM command with a dry run and a reversible backup.

| Area | Current evidence | Consequence |
|---|---|---|
| Event log | 1,676 valid events: 1,295 observations, 335 failures, 35 decisions, 10 outcomes, 1 validation | Capture is dominated by low-value observations. |
| Capture source | 1,592 `opencode_hook`, 83 `agent_structured`, 1 `inline_agent` | Structured, agent-authored memory is rare. |
| Event scope | 1,061 events have no `scope`; 615 have `project`; no user or session events in this project log | New writes must be lossless, while legacy records need an explicit default. |
| Concept registry | 3 concepts, all missing `scope`; statuses are `validated`, `candidate`, and `emerging` | Registry scope cannot be trusted as an isolation boundary yet. |
| Memory vector index | 1,862 chunks; 1,860 have `ingestion_eligible=false`, 2 omit the field; most are `.oem/wiki` chunks | The event log and derived index disagree. Default search currently has stale or ineligible material to consider. |
| Source index | 612 chunks across 200 files in `.oem/indexes/source_index.sqlite` | The source corpus is already a separate store. Keep that boundary. |
| Generated material | 70 wiki files and 31 session reports | Rebuild and report idempotency matter. |
| Ranking | `memory_ranking.py` defines preference and user-scope weights, but type boosting currently covers decision, failure, and outcome only | Declared policy is not the same as applied policy. |
| Source retrieval | `source_corpus.py:1467-1875` uses BM25 and deterministic heuristics only | Semantic source queries can miss relevant code without exact identifiers. |
| Reliability baseline | The latest full run was 1,352 passed and 2 pre-existing adapter failures: AGY context injection and Codex App process isolation | Do not attribute those two failures to later memory changes. |

## Invariants

These rules apply to every phase:

- `events.jsonl` and the user event log are the durable learning record. Registry files, wiki files, vector indexes, and dream logs are derived or audit views.
- Source files and source indexes never become learned memory. Learned memory never becomes source-code retrieval.
- Preflight remains read-only: `auto_index=False`, `record_references=False`, and no writes to memory files or indexes.
- `OEM_USER_ID` remains higher priority than git identity. An unknown user is not silently converted into a project-scoped write.
- Verified committed event bytes are kept during recovery. A checksum mismatch is quarantined, not guessed at.
- Dense LLM extraction stays opt-in and disabled by default. No hosted LLM API is introduced.
- No code path manually edits `.oem` state. Migrations and repairs run through tested commands, produce backups, and are idempotent.
- Auto-dream remains disabled by default until the durability and quality evals pass.

## Contract decisions

### Event versus retrieval type

Keep event lifecycle semantics in `KnowledgeEvent.event_type`. Add a persisted `memory_type` when a record needs a retrieval classification such as `preference`, `workaround`, `technical_handoff`, or `command_log`. Legacy records derive `memory_type` deterministically. The two fields must not silently overwrite one another.

### Canonical record

Make `retrieval.py` the normalization boundary. A normalized record must have:

- stable `id` and source provenance;
- `project_id`, `session_id`, and `scope`;
- `event_type` and `memory_type` when known;
- timestamp and source;
- explicit `ingestion_eligible`;
- enough provenance to cite the event or source path.

Top-level fields win over conflicting metadata. Missing legacy scope maps to `project` only at the compatibility boundary and is marked as legacy in diagnostics.

### Derived-state replay

All registry and index mutations must be reproducible from durable events plus source files. Maintenance actions such as decay, promotion, archive, and merge need durable event records with an operation ID, before/after values, reason, and algorithm/config version.

## P0: corpus hygiene and durable memory contract

**Outcome:** eliminate ineligible transcript material from default retrieval and make every accepted new memory round-trip without semantic loss.

**Dependencies:** none. This phase is the gate for all new ranking, scope, and maintenance behavior.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/models.py:34-64`
  - Add the canonical optional fields needed for `memory_type`, `project_id`, provenance, and a persistent dedupe key.
  - Keep defaults that can read existing events.
- `packages/oem-knowledge/src/oem_knowledge/retrieval.py:6-139`
  - Validate and normalize event/index records in one place.
  - Preserve `scope`, type, timestamps, project, session, source, and eligibility.
  - Return structured diagnostics instead of silently coercing unsupported inline types.
- `packages/oem-knowledge/src/oem_knowledge/services/reflection.py:153-342`
  - Preserve `scope` and `memory_type` from `_validate_and_normalize_event()`.
  - Validate type, scope, confidence, content, and evidence before consuming the inline quota.
  - Make quota accounting per `(project_id, session_id)` and count only accepted writes.
  - Replace the 200-character process-local dedupe key with a normalized content hash persisted in the event contract and checked under the event lock.
- `packages/oem-knowledge/src/oem_knowledge/services/state.py:23-52,316-425`
  - Apply the same quality gate to summary and evidence.
  - Keep append and dedupe atomic under the existing file lock.
  - Give legacy events a compatibility scope without rewriting the source log in place.
- `packages/oem-knowledge/src/oem_knowledge/services/search.py:242-553,555-689,691-955`
  - Exclude `ingestion_eligible=false` records from the default memory index and search path.
  - Filter event chunks by their real scope and type; remove the hard-coded project scope in `index_concept_events()`.
  - Keep an explicit audit mode for inspecting excluded records.
- `packages/oem-knowledge/src/oem_knowledge/preflight/router.py:619-688,770-909`
  - Consume normalized metadata rather than reconstructing it from title and snippet.
  - Preserve the existing weak-match gate, but apply eligibility before ranking and keep preflight read-only.
- `packages/oem-knowledge/src/oem_knowledge/memory_ranking.py:41-73,655-776`
  - Centralize the allowed type vocabulary and use one quality classification path.
  - Do not add more penalties until the corpus gate is measurable.

### Tests and evals

Gate tests, all deterministic and local:

- Extend `tests/test_retrieval_contracts.py` for missing fields, conflicting metadata, eligibility, and provenance.
- Extend `tests/test_search_filters.py`, `tests/test_search_persistence.py`, and `tests/test_user_events_indexing.py` for eligibility and type preservation.
- Add inline cases to `tests/test_lifecycle.py` and `tests/test_reflection_noise.py` for preference/workaround preservation, invalid input, quota accounting, and cross-process dedupe.
- Extend `tests/test_memory_ranking_harness.py` with a command-log evidence-only distractor and a valid structured preference.

Periodic eval:

- Add `evals/memory_corpus_audit.py` to report valid records, missing fields, excluded records, duplicate hashes, source distribution, and index/event disagreement.
- Run it against a read-only copy before and after a migration. The eval must prove that the event log is byte-for-byte unchanged and the derived index is reproducible.

### Acceptance metrics

- 100% of newly accepted inline events retain scope, memory type, project, session, source, and provenance after reload.
- Invalid type, invalid scope, weak command-log content, and high-confidence content without evidence are rejected before quota consumption.
- Repeating the same normalized memory from a second process creates one event, not two.
- A rebuilt default vector index contains zero ineligible chunks and zero command-log chunks. Excluded source records remain available only to an explicit audit path.
- A migration is idempotent, leaves `events.jsonl` unchanged, and reports every excluded or ambiguous record.

### Rollback

Keep the old derived vector database as a timestamped backup until the audit and search eval pass. If the filter removes a valid memory, restore only the derived index and fix the classifier. Never restore by editing or truncating durable event bytes.

## P1: task-aware retrieval, context assembly, and feedback evals

**Outcome:** improve useful memory at the top of preflight and search results, with citations and no read-only side effects.

**Dependencies:** P0 contract and corpus gate.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/memory_ranking.py:527-936`
  - Define a query profile for task intent, paths, identifiers, semantic terms, recency, scope, and project.
  - Apply preference, user-scope, temporal, and status weights only when their eligibility conditions are met.
  - Keep exact path and filename matches ahead of broad topic matches.
  - Cap candidate-pool size before expensive reranking and make tie breaks stable.
- `packages/oem-knowledge/src/oem_knowledge/services/search.py:691-955`
  - Retrieve a wider candidate pool, normalize it once, filter by project/scope/type/time/eligibility, then rerank.
  - Return stable IDs, scores, ranking reasons, and provenance for citations.
  - Ensure fallback search obeys the same contract and filters.
- `packages/oem-knowledge/src/oem_knowledge/preflight/router.py:770-909`
  - Remove the title/snippet-only reconstruction and reuse the normalized retriever output.
  - Preserve `auto_index=False` and `record_references=False` for preflight.
  - Record rejection reasons without writing an index or reference counters.
- `packages/oem-knowledge/src/oem_knowledge/runtime/context.py:11-132`
  - Replace append-order selection of the last five decisions/failures with task-aware retrieval from the active work state.
  - Label project, user, and session memory correctly. Do not label a failure as a user preference.
  - Include event IDs or source paths, confidence, and a short citation for every injected item.
  - Enforce a token/character budget and remove duplicates before injection.
- `packages/oem-knowledge/src/oem_knowledge/models.py:73-123`
  - Extend metrics and result models for candidate count, filtered count, noise reason, citation coverage, retrieval mode, and scope diagnostics.
- `packages/oem-knowledge/src/oem_knowledge/tools/concepts.py:158-230`
  - Expose the normalized result and optional task/retrieval diagnostics without changing the existing filter contract.

### Tests and evals

Gate tests:

- Extend `tests/test_memory_ranking_harness.py`, `tests/test_search_ranking.py`, and `tests/test_preflight_scoring.py` with positive and negative pairs.
- Extend `tests/test_preflight_search_parity.py` to prove preflight and search use the same ranker without duplicate boosts.
- Extend `tests/integration/test_context_injection.py` and `tests/test_auto_init.py` for citations, labels, budgets, and no writes.
- Add a regression to `tests/integration/test_reflection_noise.py` for command/search logs never outranking an exact decision or failure.

Periodic eval:

- Add `evals/retrieval_golden.py` with labeled cases for exact paths, technical failures, workflow rules, preferences, continuation prompts, recent events, and distractor logs.
- Report MRR, nDCG@5, hit@5, exact-path rank, top-5 noise rate, citation coverage, and scope violations for BM25, hybrid, and fallback modes.
- Keep the corpus labels outside `.oem` so evaluation data is not learned memory.

### Acceptance metrics

- Establish a baseline before switching behavior. The new path must improve nDCG@5 by at least 20% relative to that baseline and achieve at least 0.85 hit@5 on the labeled set.
- Command/search/source-dump records are at most 5% of top-five results on the distractor set and never outrank an exact relevant decision or failure.
- Citation coverage for injected context is 100%.
- Scope violations are zero. Preflight produces no changes to event files, registry files, vector files, or reference counters.
- Context stays inside its configured token budget and exact path lookup does not regress.

### Rollback

Put the task-aware ranker and new context compiler behind a runtime flag. Keep the old compiler and the ranking debug report available while the golden eval runs. Disable the new path if hit@5 falls by 10% or more, noise rises by 5 percentage points, or any scope violation appears.

## P2: event-sourced session commit and durable dream mutations

**Outcome:** one session-end transaction model with explicit phase results, safe retry, and replayable maintenance actions.

**Dependencies:** P0 event contract. P1 is not required, but P1 result metadata should be available before final context changes are enabled.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/runtime/commit_pipeline.py:141-279`
  - Turn the current intent/staging helpers into the owner of the full phase sequence.
  - Record session, project, event IDs, byte offsets, expected bytes, phase result, and index generation in the intent log.
  - Use one explicit status matrix for success, warning, partial, failed, and recovered outcomes.
  - Keep the existing byte offset plus expected-byte plus prefix-checksum rule. A verified append is committed and is never rolled back.
- `packages/oem-knowledge/src/oem_knowledge/engine.py:933-1718`
  - Make `session_end()` a thin adapter around the pipeline rather than a long sequence of early returns.
  - Put directive receipts, skill notifications, outcome recording, report writing, materialization, graph updates, indexing, and cleanup in named phases with failure policy.
  - Close the active session only after a terminal result. Keep it open on hard failure so recovery can resume.
  - Ensure indexing failure yields a retriable partial result and does not invalidate durable events or materialized concepts.
- `packages/oem-knowledge/src/oem_knowledge/services/reflection.py:1043-1172`
  - Make `reflect_session()` extraction-only or route it through the same pipeline. It must not have a second persistence implementation.
  - Preserve the public response shape while returning the pipeline phase report.
- `packages/oem-knowledge/src/oem_knowledge/services/state.py:596-784`
  - Make registry rebuild replay durable learning and maintenance events, not just the current derived registry.
  - Keep materialization and index writes rebuildable and idempotent.
- `packages/oem-knowledge/src/oem_knowledge/services/evolution.py:10-124`
  - Record decay, promotion, archive, and merge actions as durable maintenance events with operation IDs and reasons.
  - Base decay on last evidence time, not a registry timestamp that maintenance itself changes.
  - Base stale-session decisions on completed session IDs, not `len(events)` as currently passed at `engine.py:1819`.
  - Prevent `session_end` plus auto-dream from running two full index passes.
- `packages/oem-knowledge/tests/test_commit_pipeline.py` and `tests/test_session_crash_recovery.py`
  - Expand fault injection to every phase and every restart boundary.

### Tests and evals

Gate tests:

- `tests/test_commit_pipeline.py`, `tests/test_session_end_idempotency.py`, `tests/test_recovery_drift.py`, `tests/test_recovery_reflection.py`, and `tests/test_reflection_materialization_persistence.py`.
- `tests/test_dream_integration.py`, `tests/test_evolution.py`, and `tests/integration/test_evolution.py` for maintenance replay.
- `tests/integration/test_session_recovery.py`, `tests/integration/test_commit_visibility.py`, and `tests/integration/test_offline_session_end_eval.py` for process and offline boundaries.
- Verify that a retry with the same session and event IDs writes one event and one report, and that `reflect_session()` and `session_end()` produce equivalent durable state.

Periodic eval:

- Add `evals/session_recovery_matrix.py` with injected crashes before and after each phase, repeated retries, concurrent sessions, corrupt intent files, checksum drift, and interrupted indexing.
- Compare the source event log, replayed registry, materialized concepts, and search results after each recovery. Run at least 100 randomized phase/retry cases before enabling pipeline v2.

### Acceptance metrics

- No malformed or duplicated event lines after any injected failure.
- Verified event appends survive restart; unverified partial writes are either safely truncated after checksum verification or quarantined.
- A materialization failure returns partial and leaves a valid event log. An index failure leaves durable concepts searchable through the fallback path.
- A successful session has one report, one terminal outcome, one cleanup, and at most one index generation.
- Rebuilding from events plus maintenance records reproduces registry status, confidence, aliases, merges, and archive state.

### Rollback

Keep the current session-end adapter behind a compatibility flag until the recovery matrix passes. If pipeline v2 fails, route new sessions through the old adapter and retain intent files for diagnosis. Do not roll back verified events or delete a quarantine without an explicit repair command.

## P3: scope and identity isolation

**Outcome:** project, user, and session memory have enforceable storage and retrieval boundaries.

**Dependencies:** P0 contract and P2 durable commit behavior.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/services/state.py:74-119`
  - Resolve identity with `OEM_USER_ID`, request/session identity when available, then git email.
  - Warn on conflicting identities and reject user-scoped writes when no identity is available.
  - Keep successful git-probe caching and failed-probe retries.
- `packages/oem-knowledge/src/oem_knowledge/storage/user_store.py:16-50`
  - Make `UserStore` the only owner of user-event reads and writes.
  - Store each identity in a hashed identity directory such as `~/.config/openempiric/users/<identity-hash>/events.jsonl` with a lock. Keep the raw identity out of the path.
  - Persist an identity key in event metadata and filter reads to the current identity.
- `packages/oem-knowledge/src/oem_knowledge/models.py:13-64`
  - Add `user_id` or an equivalent stable identity key, `project_id`, and `created_by` where provenance requires them.
  - Treat scope as part of concept identity. Never merge project and user concepts because their names match.
- `packages/oem-knowledge/src/oem_knowledge/services/search.py:555-689,839-955`
  - Index only the current user store for user-scoped retrieval.
  - Require the resolved project ID for project scope and the exact session ID for session scope.
- `packages/oem-knowledge/src/oem_knowledge/runtime/context.py:74-104`
  - Merge user preferences across projects only for the current identity, and label them as user memory.
  - Keep project failures and decisions project-limited. Session memory expires from default context after session close.
- `packages/oem-knowledge/src/oem_knowledge/tools/lifecycle.py:244-297` and `tools/concepts.py:158-230`
  - Reject silent scope fallback and return a structured identity-required error.
  - Keep public filters aligned with the storage boundary.

The existing unscoped `~/.config/openempiric/user_events.jsonl` cannot be assigned safely if it contains records from more than one identity. Migration must be an explicit command with a supplied identity. Ambiguous records are quarantined and not indexed.

### Tests and evals

Gate tests:

- Extend `tests/test_user_identity.py` for env, request/session, git precedence, conflicts, and no-identity behavior.
- Extend `tests/test_user_events_indexing.py`, `tests/test_scope_model.py`, `tests/test_search_filters.py`, and `tests/test_rebuild_registry_idempotency.py` for per-user storage and scope replay.
- Add concurrent two-user and two-project cases to `tests/integration/test_identity.py`, `tests/integration/test_session_boundaries.py`, and `tests/test_cross_agent_concurrency.py`.

Periodic eval:

- Add `evals/scope_isolation.py` that writes matching concepts for two users, two projects, and two sessions, then runs every public search, preflight, context, rebuild, and index path.
- The report must include attempted and successful cross-boundary reads, writes, and injected context items.

### Acceptance metrics

- Zero cross-user, cross-project, or cross-session results in the isolation matrix.
- User writes are impossible without a resolved identity and never become project writes by fallback.
- Two identities can append concurrently without lost lines or mixed indexes.
- Legacy global user records are either explicitly assigned or quarantined; none are silently exposed.

### Rollback

Keep the legacy user file read-only during migration and retain a backup. Disable user-scope writes if identity resolution or isolation evals fail. Project-scoped memory remains usable without user storage.

## P4: source hybrid retrieval and configurable embeddings

**Outcome:** semantic source-code lookup improves without mixing source and memory corpora or making session end depend on a model download.

**Dependencies:** P0 normalized retrieval contract and P1 evaluation harness. P3 is required before user-aware ranking is enabled globally, but source indexing itself remains project-local.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/services/source_corpus.py:571-690,1467-1875`
  - Add a separate source embedding table or collection keyed by source chunk ID and model generation. Do not reuse the learned-memory collection.
  - Run BM25 and dense retrieval in parallel when the local semantic extra and a valid cache are available.
  - Fall back to BM25 with an explicit diagnostic when the model is unavailable.
  - Preserve exact path, filename, symbol-definition, and source-type ranking as hard signals.
- `packages/oem-knowledge/src/oem_knowledge/engine.py:431-465,2055-2196`
  - Replace hard-coded model selection with one embedding configuration object used by memory and source workers.
  - Fix the dry-run count to inspect the actual vector store (`_vector_store`, not `_store`) and report model generation, dimensions, and stale chunks.
  - Load a changed model only after a complete isolated re-index. Store the new generation beside the old one and atomically switch the active pointer.
- `packages/oem-knowledge/src/oem_knowledge/runtime/config.py` and `services/embedding_worker.py`
  - Define model ID, version, dimensions, cache policy, offline policy, and re-index checkpoint in one contract.
  - Keep session-end indexing offline and bounded. Model downloads happen only through an explicit warmup/config operation.
- `packages/oem-knowledge/tests/test_source_corpus.py`, `tests/test_source_search_ranking.py`, and `tests/test_embedding_cache_validity.py`
  - Add semantic and cache-generation coverage without weakening exact lookup tests.

### Tests and evals

Gate tests:

- BM25-only and hybrid source searches return the same exact path and symbol match at the top rank.
- An unavailable or invalid local model returns a successful BM25 result with a diagnostic, not a download attempt.
- Interrupted re-index leaves the old generation queryable and resumes from its checkpoint.
- Memory and source indexes cannot see each other’s IDs.

Periodic eval:

- Add `evals/source_retrieval.py` with labeled exact identifier, semantic description, debug, test, configuration, and unrelated-document queries.
- Compare BM25 and hybrid MRR@5, hit@5, exact-path rank, latency, index size, and fallback rate.

### Acceptance metrics

- Hybrid semantic hit@5 improves by at least 15% over BM25 on the labeled semantic cases.
- Exact path and symbol-definition rank has no regression.
- Model switching has zero event or source-data loss, supports dry-run counts, and leaves the previous generation available until validation completes.
- `session_end` never downloads a model and stays within the existing offline wall-clock contract.

### Rollback

Keep BM25 as the feature-flagged default until the source eval passes. On model-generation failure, atomically keep the prior generation active. Delete no old generation until a later explicit cleanup command.

## P5: observability, performance, and staged rollout

**Outcome:** measure quality and reliability in production-like agent sessions, then enable changes in small, reversible steps.

**Dependencies:** all functional phases. Basic baseline counters can land with P0 and P1.

### Files and changes

- `packages/oem-knowledge/src/oem_knowledge/models.py:73-123` and `tools/metrics.py:7-91`
  - Add counters for accepted/rejected/duplicate inline memories, event types, eligibility, scope filtering, retrieval candidate counts, fallback use, citation coverage, pipeline phase failures/recoveries, dream actions, and index generations.
  - Write metrics atomically under the existing state conventions.
- `packages/oem-knowledge/src/oem_knowledge/services/search.py` and `preflight/router.py`
  - Emit privacy-safe retrieval traces: query hash, project ID hash, scope, mode, candidate counts, result IDs, scores, rejection reasons, latency, and model generation. Do not log raw prompts or evidence by default.
- `packages/oem-knowledge/src/oem_knowledge/engine.py`
  - Surface phase timings, index generation, recovery action, and budget outcome in the existing structured result.
- CLI and docs
  - Add read-only audit/stats views for corpus hygiene, retrieval quality, index generations, and recovery state.
  - Document retention and redaction for traces.

### Tests and evals

Gate tests:

- Extend `tests/test_runtime_observability.py`, `tests/test_session_commit_timing.py`, and `tests/test_runtime_health.py` for counters, atomic writes, privacy fields, and partial results.
- Add a performance smoke test for bounded candidate pools, cached store reuse, and no indexing from preflight.

Periodic eval:

- Run the complete retrieval, scope-isolation, recovery, source, and offline-session suites nightly.
- Compare the current release against the last accepted baseline and publish one report containing quality, p95 latency, write failures, recovery rate, and index size.

### Rollout gates

1. **Baseline:** collect audit and golden-query metrics without changing ranking or context behavior.
2. **P0 shadow:** calculate eligibility and dedupe decisions, but keep the old derived index available until the audit passes.
3. **P1 canary:** enable task-aware ranking and citations for one project; compare old and new rankings in shadow mode.
4. **P2 canary:** enable pipeline v2 with fault injection and recovery matrix complete. Keep auto-dream off.
5. **P3 opt-in:** migrate one explicitly identified user and run the isolation matrix before widening.
6. **P4 opt-in:** enable source hybrid retrieval and model generations per project; keep BM25 fallback active.
7. **Default:** enable only the paths that clear their acceptance metrics. Auto-dream becomes eligible for opt-in after durable replay passes, never as an implicit upgrade.

### Global rollback criteria

Disable the changed feature and keep its old generation or compatibility path if any of these occur:

- any cross-scope result or injected item;
- any lost, duplicated, or malformed durable event;
- retrieval hit@5 down 10% or noise up 5 percentage points;
- p95 session-end latency exceeds 2x baseline or offline wall-clock bounds fail;
- recovery action is ambiguous or a checksum mismatch is not quarantined;
- metrics cannot explain a partial or failed session.

## Dependency order and review gates

| Phase | Must land first | Review gate |
|---|---|---|
| P0 | None | Contract tests, corpus audit, migration dry run |
| P1 | P0 | Golden retrieval eval and read-only preflight proof |
| P2 | P0 | Fault-injection recovery matrix and replay equivalence |
| P3 | P0, P2 | Multi-user/project/session isolation matrix |
| P4 | P0, P1 | Source BM25-versus-hybrid eval and model-generation rollback |
| P5 | Functional phase under rollout | Baseline comparison, p95 budget, privacy review |

Each phase must add its gate tests and periodic eval in the same change set as the runtime change. Run targeted gates before the full suite. Preserve and separately report the two known adapter failures until they are fixed in their own change.

## Explicit non-goals

- Do not turn on dense reflection by default or add a hosted LLM dependency.
- Do not generate `AGENTS.md` from memory.
- Do not build a background dream daemon.
- Do not add another parallel memory taxonomy without first fixing the event/retrieval contract.
- Do not share one mutable vector store across users or projects.
- Do not optimize embedding throughput before the corpus, scope, and replay contracts are correct.

## Working-tree constraint

This plan was prepared without changing the existing uncommitted OpenCode plugin, utility, or adapter/harness test changes. Future implementation slices must not overwrite or reformat those files. No runtime or service restart is required for this plan document.
