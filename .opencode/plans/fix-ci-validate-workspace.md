# Fix: CI validate-workspace failure

**Commit:** `efc81421c1863a137096298dc3d217db901bd9e2`
**Failing check:** `validate-workspace` (push)

## Root Cause

The `pyproject.toml` at `packages/oem-knowledge/pyproject.toml` added a `[build-system]` section with setuptools, but **did not configure package discovery** for the `src/` layout.

Setuptools defaults to discovering packages at the project root. Since the package lives under `src/oem_knowledge/`, setuptools can't find it, causing `uv sync --all-packages` to fail.

## Fix

Add `[tool.setuptools.packages.find]` to `packages/oem-knowledge/pyproject.toml`:

```toml
[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
oem_knowledge = ["plugins/openempiric.ts"]
```

Replace the current `[tool.setuptools.package-data]` section with the above (adding the `packages.find` block before it).

## Verification

Run `uv sync --all-packages --all-extras --dev` to confirm the build succeeds, then `uv run pytest` to verify tests pass.
