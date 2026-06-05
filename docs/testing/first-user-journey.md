# First-Time User Onboarding Checklist

This document details the onboarding journey checks to ensure a friction-free experience for new users starting with OpenEmpiric.

---

## Onboarding Goal
A user who has never encountered the codebase should be able to install and run the tool, e.g.:

```bash
uv tool install "git+https://github.com/xpajonx/openempiric.git#subdirectory=packages/oem-knowledge"
mkdir demo-project
cd demo-project
oem run opencode
```

The onboarding flow must succeed without forcing the user to consult internal code details, database layouts, or manually invoke lifecycle parameters.

---

## Validation Checkpoints

### 1. Zero Manual Setup
- Verify that a fresh directory auto-initializes the `.oem/` layout on `oem run`.
- Confirm the user is never prompted to manually run initialization, setup folders, or start a session.

### 2. Context Transparency
- Ensure the user understands that:
  - OpenEmpiric is active.
  - Context/memory restoration is handled automatically.
  - The agent works as standard.
- The user does not need to learn or use internal runtime commands:
  - `session-start`
  - `session-end`
  - `outcome`
  - `recover`

### 3. Internal Data Abstraction
- The user does not need to understand or inspect low-level databases or metadata logs:
  - `events.jsonl`
  - SQLite/Chroma state files
  - Manual registry rebuild procedures
