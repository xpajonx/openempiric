import os
import sys
import json
import subprocess
import importlib
import pytest

SENSITIVE_ENV_KEYS = {
    "CUDA_VISIBLE_DEVICES",
    "TOKENIZERS_PARALLELISM",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "PYTORCH_ENABLE_MPS_FALLBACK",
    "PYTHONWARNINGS",
}


def snapshot_sensitive_env() -> dict[str, str | None]:
    return {key: os.environ.get(key) for key in SENSITIVE_ENV_KEYS}


def assert_sensitive_env_unchanged(before: dict[str, str | None]) -> None:
    after = snapshot_sensitive_env()
    assert after == before


@pytest.fixture
def controlled_sensitive_env(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "true")
    monkeypatch.setenv("OMP_NUM_THREADS", "8")
    monkeypatch.setenv("MKL_NUM_THREADS", "8")
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    monkeypatch.setenv("PYTHONWARNINGS", "default")
    return snapshot_sensitive_env()


def run_import_check_in_subprocess(module_name: str, controlled_env: dict[str, str]) -> subprocess.CompletedProcess:
    # Build complete environment dict for the subprocess by copying os.environ
    env = os.environ.copy()
    env.update(controlled_env)

    code = f"""
import os, json, importlib
keys = {sorted(SENSITIVE_ENV_KEYS)!r}
before = {{k: os.environ.get(k) for k in keys}}
importlib.import_module({module_name!r})
after = {{k: os.environ.get(k) for k in keys}}
if before != after:
    print(json.dumps({{"before": before, "after": after}}, sort_keys=True))
    raise SystemExit(1)
"""
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_import_oem_knowledge_root_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Import package root in-process and verify it does not mutate env."""
    import oem_knowledge
    assert_sensitive_env_unchanged(controlled_sensitive_env)


def test_import_search_service_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Import search service in an isolated subprocess and verify it does not mutate env."""
    result = run_import_check_in_subprocess("oem_knowledge.services.search", controlled_sensitive_env)
    assert result.returncode == 0, f"Subprocess output:\nStdout: {result.stdout}\nStderr: {result.stderr}"


def test_import_vector_store_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Import vector store in-process and verify it does not mutate env."""
    import oem_knowledge.vector_store
    assert_sensitive_env_unchanged(controlled_sensitive_env)


@pytest.mark.xfail(
    reason="CRIT-04: engine/server import mutates sensitive environment variables at import time",
    strict=True,
)
def test_import_engine_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Import engine in an isolated subprocess and verify it does not mutate env."""
    result = run_import_check_in_subprocess("oem_knowledge.engine", controlled_sensitive_env)
    assert result.returncode == 0, f"Subprocess output:\nStdout: {result.stdout}\nStderr: {result.stderr}"


@pytest.mark.xfail(
    reason="CRIT-04: engine/server import mutates sensitive environment variables at import time",
    strict=True,
)
def test_import_server_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Import server in an isolated subprocess and verify it does not mutate env."""
    result = run_import_check_in_subprocess("oem_knowledge.server", controlled_sensitive_env)
    assert result.returncode == 0, f"Subprocess output:\nStdout: {result.stdout}\nStderr: {result.stderr}"


def test_reload_search_related_module_does_not_mutate_sensitive_env(controlled_sensitive_env):
    """Reload search related module and verify env remains unchanged."""
    import oem_knowledge.services.search as search_module
    importlib.reload(search_module)
    assert_sensitive_env_unchanged(controlled_sensitive_env)
