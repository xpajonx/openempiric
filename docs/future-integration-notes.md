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
