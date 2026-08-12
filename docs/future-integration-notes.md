# Future Integration Architecture Notes

This document defines the separation of concerns in OpenEmpiric (OEM) between workstation-level settings, project-level state, and the runtime supervisor.

## 1. Workstation-Level Configuration
Workstation-level configuration targets the specific agent host environment (e.g., the developer's laptop) and resides in the user's home directory.

* **Path**: `~/.config/opencode/`
* **Managed Resources**:
  - `plugins/openempiric.ts`: Registers the OEM tools and lifecycle hook points.
  - `instructions/memory-start.md`: Injects default instructions telling the agent that memory is active.
  - `opencode.jsonc`: Workstation-wide settings for the OpenCode agent.
* **Commands**:
  - `oem setup opencode`: Configures these workstation paths, registers the memory instruction, and verifies validity.
  - `oem setup opencode --repair`: Re-installs and overwrites all workstation configuration files.

## 2. Project-Level State
Project-level configuration targets the specific project repository being worked on and resides inside the repository root.

* **Path**: `<project_root>/.oem/`
* **Managed Resources**:
  - `concepts/`: Localized registry of concepts.
  - `state/`: Session tracking, active session files, metrics, and outcomes.
  - `events.jsonl`: The event log recording knowledge additions and modifications.
* **Commands**:
  - `oem init`: Initializes the `.oem/` configuration and directory structures for the current repository.

## 3. Runtime Supervisor
The runtime supervisor runs during active agent execution, acting as an overseer to inject context dynamically, manage state, and handle recovery/commits.

* **Command**: `oem run opencode`
* **Responsibilities**:
  - Compiles the final dynamic instruction and knowledge context payload before the agent runs.
  - Monitors the agent process and handles recovery (via `oem recover`) if the agent crashes or terminates unexpectedly.
  - Invokes reflection and commits new learnings upon successful termination.

## OpenCode remember skill and dream subagent (Wave 1)

- Packaged assets: `src/oem_knowledge/skills/remember/SKILL.md` and `src/oem_knowledge/agent/dream.md` ship in the wheel (package-data) and are installed by `oem setup opencode`.
- Install locations (XDG-resolved): `skills/remember/SKILL.md` and `agent/dream.md` under the OpenCode config dir.
- Ownership policy: a file is OEM-managed only when `openempiric-manifest.json` records its path AND the recorded sha256 matches the file. Verified-managed files upgrade on any setup run. Marker-only or tampered files are preserved on normal setup and even under `--repair`; `--force-assets` replaces user-owned files (regular files get a `.oem.bak` backup; symlinks are replaced without backup).
- The dream subagent is OpenCode-only and hidden; it is activated by delegation (`plan` -> dream_start, `orchestrator` -> dream_end) and routes through the remember skill. It is never registered as a visible agent and no `agent` key is written to opencode.jsonc.
- Onboarding: after running `oem setup opencode`, restart the OpenCode client so the skill and agent directories are picked up. The remember skill then activates on memory language; the dream agent is available for explicit delegation.
- The tracked `instructions/memory-start.md` and `instructions/memory-start-agy.md` are canonical copies of `OEM_MEMORY_INSTRUCTIONS` and must be kept in sync with `src/oem_knowledge/runtime/instructions.py`.
