"""Offline session-end eval: session_end must never download an embedding
model, must complete within wall-clock bounds, and index_isolated must honor
its wall-clock budget.

Deviation note: session_end with empty events/conversation may return
"success", "warn", or "empty" depending on the reflection outcome in the
non-fatal branch; the contract under test is completion + no download +
session unlinked, so status is asserted against all three values.
"""

import os
import queue
import sys
import time
import types
from unittest.mock import patch

import pytest

import oem_knowledge.engine
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.services.embedding_worker import run_isolated_index


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    engine = KnowledgeEngine(project_dir)
    engine.init_project(str(project_dir))
    return project_dir, engine


def _break_cache(monkeypatch, tmp_path):
    """Point both cache roots at empty dirs (broken fastembed cache layout)."""
    root_a = tmp_path / "cache-a"
    root_b = tmp_path / "cache-b"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    monkeypatch.setattr(
        "pathlib.Path.home", classmethod(lambda cls: root_a)
    )
    monkeypatch.setitem(
        sys.modules,
        "fastembed.common.utils",
        types.SimpleNamespace(define_cache_dir=lambda _: str(root_b)),
    )
    return root_a, root_b


def _run_session_end(engine, project_dir):
    with patch.object(
        KnowledgeEngine, "_download_model",
        side_effect=AssertionError("download attempted"),
    ) as mock_dl:
        t0 = time.monotonic()
        res = engine.session_end(
            project=str(project_dir),
            conversation_text="",
            events=[],
            extraction_mode="auto",
        )
        elapsed = time.monotonic() - t0
    return res, elapsed, mock_dl


def test_session_end_fastembed_import_failure_never_downloads(tmp_path, monkeypatch, temp_project):
    project_dir, engine = temp_project
    monkeypatch.setitem(sys.modules, "fastembed", None)
    _break_cache(monkeypatch, tmp_path)

    engine.session_start(project=str(project_dir))
    res, elapsed, mock_dl = _run_session_end(engine, project_dir)

    assert res["status"] in ("success", "warn", "empty")
    assert not (project_dir / ".oem" / "state" / "active_session.json").exists()
    mock_dl.assert_not_called()
    assert elapsed < 60.0


def test_session_end_broken_cache_never_downloads(tmp_path, monkeypatch, temp_project):
    project_dir, engine = temp_project
    _break_cache(monkeypatch, tmp_path)

    engine.session_start(project=str(project_dir))
    res, elapsed, mock_dl = _run_session_end(engine, project_dir)

    assert res["status"] in ("success", "warn", "empty")
    assert not (project_dir / ".oem" / "state" / "active_session.json").exists()
    mock_dl.assert_not_called()
    assert elapsed < 60.0


class _FakeQueue:
    def get(self, timeout=None):
        raise queue.Empty()


class _FakeProc:
    def __init__(self):
        self._alive = True

    def start(self):
        pass

    def join(self, timeout=None):
        return False

    def is_alive(self):
        return True

    def terminate(self):
        self._alive = False


class _FakeCtx:
    def Queue(self):
        return _FakeQueue()

    def Process(self, target, args):
        return _FakeProc()


def test_index_isolated_budget_timeout_returns_partial_fast(tmp_path, monkeypatch, temp_project):
    project_dir, engine = temp_project
    with patch("multiprocessing.get_context", return_value=_FakeCtx()):
        t0 = time.monotonic()
        res = engine.index_isolated(project_dir=str(project_dir), budget_s=1.0)
        elapsed = time.monotonic() - t0

    assert res["status"] == "partial"
    assert res["error"] == "Indexing budget exceeded"
    assert elapsed < 10.0


def test_run_isolated_index_sets_offline_env_and_succeeds(temp_project):
    project_dir, engine = temp_project
    prev_hf = os.environ.get("HF_HUB_OFFLINE")
    prev_tf = os.environ.get("TRANSFORMERS_OFFLINE")
    try:
        res = run_isolated_index(str(project_dir), budget_s=10.0)
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"
        assert res["status"] == "success"
    finally:
        for key, prev in (("HF_HUB_OFFLINE", prev_hf), ("TRANSFORMERS_OFFLINE", prev_tf)):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


class _FakeErrorQueue:
    def get(self, timeout=None):
        return {"status": "error", "error": "worker boom"}


class _FakeFinishedProc:
    def start(self):
        pass

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False

    def terminate(self):
        pass


class _FakeErrorCtx:
    def Queue(self):
        return _FakeErrorQueue()

    def Process(self, target, args):
        return _FakeFinishedProc()


class _FakeSearchIndex:
    def index_all(self, progress_callback=None, budget_seconds=None):
        raise RuntimeError("boom")


class _FakeEngine:
    def __init__(self, *args, **kwargs):
        self.search = _FakeSearchIndex()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_index_isolated_worker_error_becomes_partial(tmp_path):
    engine = KnowledgeEngine(tmp_path)
    with patch("multiprocessing.get_context", return_value=_FakeErrorCtx()):
        result = engine.index_isolated(project_dir=str(tmp_path), budget_s=1.0)

    assert result["status"] == "partial"
    assert result["error"] == "worker boom"


def test_run_isolated_index_worker_exception_returns_error_dict(tmp_path):
    prev_hf = os.environ.get("HF_HUB_OFFLINE")
    prev_tf = os.environ.get("TRANSFORMERS_OFFLINE")
    try:
        with patch.object(oem_knowledge.engine, "KnowledgeEngine", _FakeEngine):
            result = run_isolated_index(str(tmp_path), budget_s=1.0)
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert "boom" in result["error"]
    finally:
        for key, prev in (("HF_HUB_OFFLINE", prev_hf), ("TRANSFORMERS_OFFLINE", prev_tf)):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
