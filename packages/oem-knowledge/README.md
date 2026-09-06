# oem-knowledge

This package is a workspace member managed from the repository root.

**Do not run `uv sync` or `uv venv` in this directory.**

To install dependencies or run commands, run them from the repository root workspace:
```bash
# Correct setup at the root
cd ../../
uv sync
uv run pytest
```

## No-config debugging

The package exposes debugpy wrappers for attaching a debugger without creating
an editor launch configuration:

```bash
uv run oem-debug doctor
uv run oem-debug --listen 127.0.0.1:8765 preflight "continue working"
uv run oem-debug-server
```

By default these commands listen on `127.0.0.1:5678` and wait for a debugger to
attach. Use `--no-wait` to start immediately or `OEM_DEBUG_LISTEN=[host:]port`
to change the default address.
# Embedding model configuration

The selected embedding model is stored in `.oem/config/embedding_model.json`.
Missing, malformed, or blank configuration falls back to the default model.
After switching models, run `oem warmup` and explicitly re-index (`oem index
--reindex`) so stored vectors match the new model and dimension. Mixed, stale,
or legacy vectors are excluded from dense scoring; BM25 remains available as a
safe fallback.
