# Agent-First Roadmap

## Philosophy

The agent is the primary consumer of OEM. Every feature exists to make the agent
smarter, faster, and more autonomous. The user should never need to think about
OEM — they talk to their agent, and OEM handles the rest.

Milestones are ordered by agent autonomy gain. Each milestone gives the agent a
new capability that reduces user friction or increases knowledge quality.

---

## Milestone A: Survival (Zero-Touch Setup)

**Goal**: First-time user runs `oem run opencode` and everything works —
no init, no warmup, no manual steps.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Auto-init | Agent detects missing `.oem/` and bootstraps it | One command to start |
| Auto-warmup | Agent pre-downloads embedding model on first run | No `oem warmup` needed |
| Auto-plugin-link | Agent symlinks the TypeScript plugin | No manual plugin install |
| Auto-recovery | Agent detects stale sessions and recovers silently | No `oem recover` needed |
| Plugin health-check | Agent verifies plugin link before each session | No silent failures |

**Agent instructions added**: None yet — this is all infrastructure.

**Files touched**:
- `runner.py` — add auto-init/warmup/health-check before agent spawn
- `adapters/base.py` — add `verify_health()` method
- `engine.py` — make `init_project` idempotent, add `warmup_if_needed`

---

## Milestone B: Context-Aware (Proactive Knowledge)

**Goal**: Agent enters every session knowing what the project knows, and
retrieves relevant knowledge without being asked.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Pre-search | Agent auto-retrieves 3-5 relevant concepts at session start | Answers are grounded from first message |
| Memory-aware instructions | Agent understands OEM as its long-term memory | No "search before every response" overhead |
| Retrieval when relevant | Agent searches when it needs info it lacks | Smarter retrieval, less noise |

**Agent instructions added**:
- `## Memory Context` section in session context: "OEM is your long-term memory
  for this project. Use knowledge_search when you need details. You do not need
  to search before every response — only when you lack information."
- `## OEM as Memory` best practice in skill YAML.

**Files touched**:
- `context.py` / `ContextAssembler` — add memory context blob
- `adapters/opencode/adapter.py` — update skill YAML with memory framing
- `plugins/openempiric.ts` — update `config` hook to inject memory context

---

## Milestone C: Self-Improving (Quality Reflection)

**Goal**: Agent produces high-quality knowledge events automatically, without
needing structured prefixes or manual effort.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Structured format guidance | Agent instructions teach the reflection format | Better concept extraction |
| Code diff extraction | Agent can extract concepts from actual code changes | Concepts from code, not just chat |
| Cross-session correlation | Agent learns from all sessions, not just latest | No lost knowledge from backlog |

**Agent instructions added**:
- `## Knowledge Capture` section: "Record decisions, failures, and validations
  using structured format."
- `## Reflection Format` examples in the system prompt.

**Files touched**:
- `services/reflection.py` — add structured format instructions, add diff
  observation patterns, improve fallback extraction
- `services/materialization.py` — process all unprocessed sessions
- `runner.py` — inject reflection guidelines in temp instructions

---

## Milestone D: Resilient (Graceful Degradation)

**Goal**: Everything works even when components fail. Agent falls back
gracefully and recovers partial work.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Context fallback | If context compilation fails, agent gets minimal context | No blank session |
| Search fallback | If vector DB is down, agent gets registry-only results | No "search failed" errors |
| Session crash recovery | Agent recovers partial transcripts | No lost work on crash |
| Plugin error isolation | Plugin failure doesn't crash the session | No "OEM crashed" sessions |

**Files touched**:
- `runner.py` — wrap each lifecycle step in try/except with fallback
- `cli.py` — auto-recover on `oem run` instead of warning
- `services/search.py` — add registry-only fallback mode
- `plugins/openempiric.ts` — wrap `config` hook in try/catch

---

## Milestone E: Measurable (Outcome-Driven)

**Goal**: Agent knows whether its sessions are succeeding, and adjusts behavior
based on outcomes.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Granular outcome tracking | Agent records goal satisfaction, not just success/failure | Better fitness metrics |
| Feedback-aware retrieval | Agent prioritizes concepts validated by past success | Smarter search ranking |
| Outcome correlation | Agent identifies which concepts drive success | Actionable knowledge fitness |

**Files touched**:
- `services/state.py` — add goal satisfaction tracking to outcomes
- `models.py` — add `goal_satisfaction`, `concept_effectiveness` fields
- `services/fitness.py` — weight outcomes by goal satisfaction
- `adapters/opencode/adapter.py` — add outcome guidelines to skill YAML

---

## Milestone F: Autonomous (Self-Healing)

**Goal**: Agent independently manages its own knowledge lifecycle — detects
stale concepts, proposes merges, resolves contradictions.

| Capability | What the agent can do now | User impact |
|---|---|---|
| Stale concept detection | Agent identifies concepts not referenced in N sessions | Clean knowledge base |
| Auto-merge proposals | Agent suggests merging duplicate concepts | Less noise |
| Contradiction resolution | Agent detects and flags conflicting knowledge | Higher knowledge integrity |

**Files touched**:
- `services/state.py` — add staleness detection
- `services/evolution.py` — add auto-merge heuristics
- `cli.py` — expose merge/contradiction as agent tools
- `plugins/openempiric.ts` — add `knowledge_health_check` tool

---

## Summary

| Milestone | Autonomy level | Key user-facing change | Est. effort |
|---|---|---|---|
| A: Survival | Zero-touch setup | `oem run` just works | **1-2 days** |
| B: Context-Aware | Proactive knowledge | Agent searches automatically | 2-3 days |
| C: Self-Improving | Quality reflection | Better concepts from less effort | 3-5 days |
| D: Resilient | Graceful degradation | No crashes from OEM failures | 2-3 days |
| E: Measurable | Outcome-driven | Smarter ranking from outcomes | 3-5 days |
| F: Autonomous | Self-healing | Clean knowledge without intervention | 5-7 days |
