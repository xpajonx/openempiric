# Fresh-Machine Installation Validation

This document outlines the clean-room validation process for verifying the installer, environment setup, and runtime detection.

---

## Scenario A: Global Tool Installation

This scenario ensures that a user can install the `oem` command globally and execute environment diagnostics.

### Steps
1. Execute the global tool installation via `uv`:
   ```bash
   uv tool install "git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge"
   ```
2. Verify that the `oem` binary is globally available on the environment path:
   ```bash
   oem --version
   ```
3. Run the doctor command to verify environment safety and warmer status without any pre-existing workspace assumptions:
   ```bash
   oem doctor
   ```

### Expectations
- The `oem` command succeeds.
- No trace of local workspace folders is required to perform basic version checks.
- Embedding models and configuration directories are detected/warmed without crashing.

---

## Scenario B: Development Environment Setup

This scenario ensures that contributors can check out, install, and execute tests and diagnostics from source.

### Steps
1. Clone the repository and initialize the workspace:
   ```bash
   git clone https://github.com/xpajonx/openempiric.git
   cd openempiric
   uv sync
   ```
2. Run the environment doctor:
   ```bash
   uv run oem doctor
   ```

### Expectations
- The CLI tool correctly detects development mode.
- Plugin links and workspace paths are verified.
- The environment configuration returns healthy status checks.

---

## Scenario C: Docker container environment

This scenario validates containerized execution and local-first memory state consistency.

### Steps
1. Spawn the environment containers:
   ```bash
   docker compose up -d
   ```
2. Run a managed agent test inside the workspace volume container:
   ```bash
   docker compose exec oem-runtime oem run opencode
   ```

### Expectations
- The containerized oem runtime boots correctly.
- Project-local database paths and `.oem` folders persist within mount volumes.
