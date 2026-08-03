# OpenEmpiric Best Practices

OpenEmpiric is an **agent-first knowledge runtime**. It learns by listening to how you work with coding agents, capturing decisions, failures, tradeoffs, experiments, and outcomes from your conversations and converting them into structured, reusable knowledge.

Because the agent is the primary consumer of this knowledge runtime, it uses these accumulated learnings to proactively avoid repeating past errors, align with project architecture choices, and build on validated concepts. The quality of what the agent learns and retrieves depends on how you communicate. This guide explains what conversation patterns produce valuable knowledge — without requiring any special syntax or tags.

---

## What Creates Valuable Knowledge?

OEM learns best from explicit reasoning:

| Signal | Why It Matters |
|---|---|
| **Decisions** | Why a choice was made — not just what was chosen |
| **Failures** | What went wrong and why — not just that it broke |
| **Tradeoffs** | What alternatives were considered and why one was selected |
| **Experiments** | What was tried, how it was measured, and what the results were |
| **Outcomes** | What changed as a result — concrete impact |

Each signal becomes a knowledge event that OEM can index, cross-reference, and surface in future sessions.

---

## High-Value Conversation Examples

### Decisions

#### Good

> "We decided to use TypeScript for the hot path because Python startup latency caused MCP timeouts exceeding the 60-second limit. The runtime overhead of spawning a Python process for each request was about 500ms, compared to under 10ms for TypeScript."

#### Bad

> "Use TypeScript."

OEM registers the **rationale** (latency, timeout threshold, measured overhead), not just the choice.

---

### Failures

#### Good

> "The batch processing job failed because the pagination cursor was not being reset between retries. Each retry started from the last cursor position instead of the beginning, causing it to skip items 50-100 on every attempt."

#### Bad

> "Pagination is broken."

OEM captures the **root cause** (cursor state leak), not just the symptom.

---

### Tradeoffs

#### Good

> "We chose a client-side cache over server-side caching because the data is read-heavy with low update frequency. The tradeoff is that stale data may serve for up to 5 minutes, but we avoid adding Redis as a dependency and the deployment complexity that comes with it."

#### Bad

> "Let's go with client cache."

OEM learns the **evaluation criteria** (read vs write pattern, staleness tolerance, operational complexity).

---

### Experiments

#### Good

> "We tested three retrieval strategies on a dataset of 1000 queries: pure BM25, dense vector search, and hybrid fusion. The hybrid approach scored 23% higher on recall@5 and 15% higher on precision@3. We adopted hybrid as the default."

#### Bad

> "Hybrid search works better."

OEM captures the **methodology** (dataset size, metrics, comparison) and the **result** (specific improvements).

---

### Outcomes

#### Good

> "This change reduced retrieval latency from 5.5 seconds to under 500 milliseconds — a 91% improvement. The component now consistently scores in the 99th percentile for response time."

#### Bad

> "Looks better now."

OEM captures **measurable impact** (before/after, percentage, percentile), enabling future comparison.

---

## Working With Coding Agents

### Recommended Patterns

- **Explain why a decision was made** — State the constraint, the alternatives, and the reasoning.
- **Explain why an approach failed** — Describe the root cause, not just the error message.
- **Summarize experiments** — Include setup, methodology, results, and conclusion.
- **Capture lessons learned** — What would you do differently next time?
- **State success criteria** — Define what "good" looks like before evaluating results.

### Avoid

- **One-word approvals** — "Looks good" or "Approved" without context.
- **Context-free instructions** — "Change the caching layer" without explaining why.
- **Unexplained reversals** — "Actually, let's use Redis instead" without mentioning what changed your mind.
- **Terse confirmations** — "Yes" or "OK" when a decision was made.

---

## Example OEM Session

Here is a realistic conversation that produces reusable knowledge across multiple signals:

**Problem**: The deployment pipeline is timing out on large projects.

> "Our CI pipeline is failing for repositories with more than 500 files. The `lint-staged` step is timing out at 10 minutes. Let me investigate what's happening."

**Investigation**:

> "I added instrumentation to the lint step. The bottleneck is not file count but dependency resolution — each run is downloading node_modules fresh instead of using the workspace cache. The download takes 8 of the 10 minutes."

**Failure**:

> "Tried to enable workspace caching by setting `cache-dependency-path` in CI config, but the action ignores it when `working-directory` is set at the job level. The cache key is never generated."

**Decision**:

> "We'll restructure the CI matrix to run lint and test in separate jobs. Lint can share the workspace-level cache. The tradeoff is slightly longer total CI time (parallel jobs vs sequential) but each individual job stays under the timeout."

**Implementation**:

> "Split the CI config into two job definitions. Lint uses the root `package-lock.json` for its cache key. Test uses the per-package lock files. Both now complete in under 4 minutes."

**Outcome**:

> "The lint job now takes 2.5 minutes instead of timing out. The test job runs in parallel, so total wall-clock time is 4 minutes instead of 12. No more timeout alerts."

### Why OEM Learns From This

The conversation naturally contains:

- **A decision** with rationale (restructure matrix, share cache)
- **A failure** with root cause (cache key not generated when `working-directory` is set)
- **A tradeoff** (parallel jobs vs sequential, slightly more total time)
- **An outcome** with concrete metrics (2.5 min vs timeout, 4 min total vs 12)

OEM indexes all of these without needing a single tag, syntax marker, or structured format.

---

## Summary

Write for a future collaborator (or a future version of yourself) who needs to understand *why* something was done, not just *what* was done. That is the conversation OEM can learn from.

- State **reasons**, not just results.
- Describe **root causes**, not just symptoms.
- Compare **alternatives**, not just the chosen path.
- Measure **outcomes**, not just effort.

---

## Session finalization guarantees

Session finalization (`session_end` / `oem session end`) is built around three guarantees: it never downloads an embedding model, it never hangs, and it never loses knowledge.

- **No embedding-model downloads.** Finalization never downloads an embedding model. Only an explicit `oem warmup` does.
- **Bounded indexing.** Finalization indexing runs spawn-isolated in a subprocess with a hard wall-clock budget (default 10 seconds). Exceeding the budget yields a `partial` result with error `"Indexing budget exceeded"`; run `oem index --project <dir>` to rebuild the derived search index.
- **Events are persisted first.** Events and memory are always written before indexing begins, so a partial index never loses knowledge — it only leaves the derived search index stale until rebuilt.
- **Broken cache fails fast.** A corrupt or unavailable embedding cache makes indexing fail fast as `partial` — it never hangs and never triggers a download. Run `oem warmup` to restore the cache.

### Recovering a stale session

If a previous session ended without cleanly unlinking its active session file:

1. Back up `.oem/state/active_session.json`.
2. Dry-run first: `uv run oem recover --abort --project <dir> --dry-run`.
3. Then run `uv run oem recover --abort --project <dir>`.

Never delete `events.jsonl` or `concept_registry.json`, and never delete the fastembed cache.
