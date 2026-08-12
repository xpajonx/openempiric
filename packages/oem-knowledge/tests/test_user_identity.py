"""Wave 3 follow-up: git identity probing is cached on success, retried on failure."""


def test_successful_git_probe_cached(monkeypatch):
    from oem_knowledge.services import state
    from unittest.mock import MagicMock
    state._GIT_IDENTITY_CACHE.update({"checked": False, "value": None})
    monkeypatch.delenv("OEM_USER_ID", raising=False)
    calls = []
    fake = MagicMock()
    fake.stdout = "dev@example.com\n"
    fake.returncode = 0
    def fake_run(*args, **kwargs):
        calls.append(args)
        return fake
    # subprocess is imported function-locally in state.resolve_user_identity,
    # so patch the global subprocess module rather than state.subprocess.
    monkeypatch.setattr("subprocess.run", fake_run)
    assert state.resolve_user_identity() == "dev@example.com"
    assert state.resolve_user_identity() == "dev@example.com"
    assert len(calls) == 1, "successful probe must be cached"


def test_failed_git_probe_not_cached(monkeypatch):
    from oem_knowledge.services import state
    from unittest.mock import MagicMock
    state._GIT_IDENTITY_CACHE.update({"checked": False, "value": None})
    monkeypatch.delenv("OEM_USER_ID", raising=False)
    calls = []
    fake = MagicMock()
    fake.stdout = ""
    fake.returncode = 1
    def fake_run(*args, **kwargs):
        calls.append(args)
        return fake
    monkeypatch.setattr("subprocess.run", fake_run)
    assert state.resolve_user_identity() is None
    assert state.resolve_user_identity() is None
    assert len(calls) == 2, "failed probe must be retried, not cached"


def test_env_var_takes_precedence_and_is_not_cached(monkeypatch):
    from oem_knowledge.services import state
    from unittest.mock import MagicMock
    state._GIT_IDENTITY_CACHE.update({"checked": False, "value": None})
    monkeypatch.setenv("OEM_USER_ID", "env@example.com")
    calls = []
    fake = MagicMock()
    fake.stdout = "git@example.com\n"
    fake.returncode = 0
    def fake_run(*args, **kwargs):
        calls.append(args)
        return fake
    monkeypatch.setattr("subprocess.run", fake_run)
    assert state.resolve_user_identity() == "env@example.com"
    assert state.resolve_user_identity() == "env@example.com"
    assert calls == [], "env var path must never spawn a subprocess"
