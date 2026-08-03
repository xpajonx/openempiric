"""Tests for the strict fastembed cache validator in oem_knowledge.engine.

Covers KnowledgeEngine.embedding_cache_ready() and
KnowledgeEngine._validate_fastembed_cache_dir() (engine.py ~1983-2079).

The validator is strict: partial or broken fastembed caches must be
detected and rejected. embedding_cache_ready() short-circuits to True when
fastembed.TextEmbedding.__name__ == "MockTextEmbedding" (the test dummy), so
every disk-path test installs a fake fastembed module whose TextEmbedding has
a different __name__ and registers fastembed.common.utils.define_cache_dir,
which the conftest dummy does not provide.
"""

import json
import pathlib
import sys
import types

import pytest

from oem_knowledge.engine import KnowledgeEngine


HF_REPO_DIR = "models--qdrant--bge-small-en-v1.5-onnx-q"
ONNX_PATH = "onnx/model_optimized.onnx"
TOKENIZER_PATH = "tokenizer.json"
DEFAULT_SNAPSHOT = "snap1"
DEFAULT_BLOB_ONNX = "blob-onnx"
DEFAULT_BLOB_TOK = "blob-tok"


# ========== Helpers ==========

def write_hf_cache(root, snapshot_id=DEFAULT_SNAPSHOT,
                   refs_content=DEFAULT_SNAPSHOT + "\n", meta=None,
                   blobs=(), nested_blobs=()):
    """Create a HuggingFace-layout fastembed cache under ``root``.

    Returns (hf_dir, snap_dir). ``blobs`` are written flat at
    ``blobs/<blob_id>``; ``nested_blobs`` are written at
    ``blobs/<2-char-prefix>/<rest>``. ``meta`` defaults to a valid dict-form
    metadata referencing DEFAULT_BLOB_ONNX and DEFAULT_BLOB_TOK.
    """
    hf_dir = root / HF_REPO_DIR
    (hf_dir / "refs").mkdir(parents=True, exist_ok=True)
    (hf_dir / "refs" / "main").write_text(refs_content, encoding="utf-8")
    snap_dir = hf_dir / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    if meta is None:
        meta = {
            ONNX_PATH: {"blob": DEFAULT_BLOB_ONNX},
            TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK},
        }
    (snap_dir / "files_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    for blob_id in blobs:
        blob_path = hf_dir / "blobs" / blob_id
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_text("blob-bytes", encoding="utf-8")
    for blob_id in nested_blobs:
        blob_path = hf_dir / "blobs" / blob_id[:2] / blob_id[2:]
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_text("blob-bytes", encoding="utf-8")
    return hf_dir, snap_dir


def write_legacy_gcs(root, dir_name, onnx_bytes=b"onnx-bytes",
                     tokenizer_bytes=b"tokenizer-bytes"):
    """Create a legacy GCS-layout fastembed cache directory."""
    gcs_dir = root / dir_name
    gcs_dir.mkdir(parents=True, exist_ok=True)
    (gcs_dir / "model.onnx").write_bytes(onnx_bytes)
    (gcs_dir / "tokenizer.json").write_bytes(tokenizer_bytes)
    return gcs_dir


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    """Patch both fastembed cache roots to isolated tmp dirs and install a
    fake fastembed whose TextEmbedding is NOT named MockTextEmbedding, so the
    disk-validation path in embedding_cache_ready() actually runs.

    Returns {"engine", "root_a", "root_b"}: the engine under test, the first
    validated cache root (Path.home() / ".cache" / "fastembed"), and the
    second validated cache root (the result of the patched fastembed
    define_cache_dir). These are the two directories the validator inspects.
    """
    # Construct the engine before patching Path.home so service constructors
    # keep seeing the real home directory.
    engine = KnowledgeEngine()

    root_home = tmp_path / "home-cache"
    root_custom = tmp_path / "custom-cache"

    fake = types.ModuleType("fastembed")

    class FakeTextEmbedding:
        pass

    FakeTextEmbedding.__name__ = "FakeTextEmbedding"
    fake.TextEmbedding = FakeTextEmbedding

    common = types.ModuleType("fastembed.common")
    utils = types.ModuleType("fastembed.common.utils")
    utils.define_cache_dir = lambda cache_dir=None: str(root_custom)
    common.utils = utils
    fake.common = common

    monkeypatch.setitem(sys.modules, "fastembed", fake)
    monkeypatch.setitem(sys.modules, "fastembed.common", common)
    monkeypatch.setitem(sys.modules, "fastembed.common.utils", utils)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: root_home))

    return {"engine": engine, "root_a": root_home / ".cache" / "fastembed",
            "root_b": root_custom}


def install_mock_named_fastembed(monkeypatch, tmp_path):
    """Install a fake fastembed whose TextEmbedding IS named MockTextEmbedding
    and patch Path.home to an empty tmp dir. Returns the engine under test."""
    engine = KnowledgeEngine()

    fake = types.ModuleType("fastembed")

    class MockTextEmbedding:
        pass

    fake.TextEmbedding = MockTextEmbedding

    common = types.ModuleType("fastembed.common")
    utils = types.ModuleType("fastembed.common.utils")
    utils.define_cache_dir = lambda cache_dir=None: str(tmp_path / "custom-cache")
    common.utils = utils
    fake.common = common

    monkeypatch.setitem(sys.modules, "fastembed", fake)
    monkeypatch.setitem(sys.modules, "fastembed.common", common)
    monkeypatch.setitem(sys.modules, "fastembed.common.utils", utils)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home-cache"))

    return engine


# ========== Test 1: Valid HF layout is ready ==========

def test_valid_hf_layout_in_both_roots_is_ready(cache_env):
    """A complete HF layout in both cache roots validates as ready."""
    env = cache_env
    write_hf_cache(env["root_a"], blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    write_hf_cache(env["root_b"], blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    assert env["engine"].embedding_cache_ready() is True


# ========== Test 2: Empty refs/main is rejected ==========

def test_hf_refs_main_empty_is_rejected(cache_env):
    """An empty refs/main yields no snapshot id, so the cache is rejected."""
    env = cache_env
    write_hf_cache(env["root_a"], refs_content="",
                   blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 3: Missing snapshots dir is rejected ==========

def test_hf_snapshot_dir_missing_is_rejected(cache_env):
    """refs/main points at a snapshot dir that does not exist."""
    env = cache_env
    hf_dir = env["root_a"] / HF_REPO_DIR
    (hf_dir / "refs").mkdir(parents=True, exist_ok=True)
    (hf_dir / "refs" / "main").write_text(DEFAULT_SNAPSHOT + "\n", encoding="utf-8")
    # No snapshots/snap1 directory is created.
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 4: Malformed metadata is rejected ==========

def test_hf_metadata_malformed_plain_string_is_rejected(cache_env):
    """files_metadata.json containing a JSON string (not a dict/list)."""
    env = cache_env
    hf_dir = env["root_a"] / HF_REPO_DIR
    snap_dir = hf_dir / "snapshots" / DEFAULT_SNAPSHOT
    (hf_dir / "refs").mkdir(parents=True, exist_ok=True)
    (hf_dir / "refs" / "main").write_text(DEFAULT_SNAPSHOT + "\n", encoding="utf-8")
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "files_metadata.json").write_text(json.dumps("just a string"),
                                                  encoding="utf-8")
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 5: List entry missing path key is rejected ==========

def test_hf_metadata_list_entry_missing_path_is_rejected(cache_env):
    """A metadata list entry without a truthy 'path' key invalidates the cache."""
    env = cache_env
    meta = [
        {"blob": DEFAULT_BLOB_ONNX},
        {"path": TOKENIZER_PATH, "blob": DEFAULT_BLOB_TOK},
    ]
    write_hf_cache(env["root_a"], meta=meta,
                   blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 6: Metadata referencing a missing blob is rejected ==========

def test_hf_metadata_references_missing_blob_is_rejected(cache_env):
    """An entry whose blob file does not exist anywhere in blobs/."""
    env = cache_env
    meta = {
        ONNX_PATH: {"blob": "ghost-blob"},
        TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK},
    }
    write_hf_cache(env["root_a"], meta=meta, blobs=(DEFAULT_BLOB_TOK,))
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 7: Nested blobs/<2-prefix>/<rest> layout is ready ==========

def test_hf_blob_in_nested_prefix_layout_is_ready(cache_env):
    """Blobs stored at blobs/<2-char-prefix>/<rest> are found (string-form
    metadata entries are accepted as blob ids)."""
    env = cache_env
    nested = "abcdef123456"
    meta = {ONNX_PATH: nested, TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK}}
    write_hf_cache(env["root_a"], meta=meta, nested_blobs=(nested,),
                   blobs=(DEFAULT_BLOB_TOK,))
    assert env["engine"].embedding_cache_ready() is True


# ========== Test 8: Missing model_optimized.onnx key is rejected ==========

def test_hf_metadata_missing_onnx_key_is_rejected(cache_env):
    """Metadata lacking a model_optimized.onnx entry is rejected."""
    env = cache_env
    write_hf_cache(env["root_a"], meta={TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK}},
                   blobs=(DEFAULT_BLOB_TOK,))
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 9: Missing tokenizer.json key is rejected ==========

def test_hf_metadata_missing_tokenizer_key_is_rejected(cache_env):
    """Metadata lacking a tokenizer.json entry is rejected."""
    env = cache_env
    write_hf_cache(env["root_a"], meta={ONNX_PATH: {"blob": DEFAULT_BLOB_ONNX}},
                   blobs=(DEFAULT_BLOB_ONNX,))
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 10: Legacy GCS layout is ready ==========

def test_legacy_gcs_bge_small_layout_is_ready(cache_env):
    """Legacy GCS dir bge-small-en-v1.5 with non-empty model.onnx and
    tokenizer.json validates."""
    env = cache_env
    write_legacy_gcs(env["root_a"], "bge-small-en-v1.5")
    assert env["engine"].embedding_cache_ready() is True


# ========== Test 11: Legacy GCS empty files are rejected ==========

def test_legacy_gcs_fast_layout_empty_files_rejected(cache_env):
    """Legacy GCS dir fast-bge-small-en-v1.5 with 0-byte files is rejected."""
    env = cache_env
    write_legacy_gcs(env["root_a"], "fast-bge-small-en-v1.5",
                     onnx_bytes=b"", tokenizer_bytes=b"")
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 12: Any-root semantics across the two cache roots ==========

def test_first_root_valid_second_root_invalid_still_ready(cache_env):
    """The two cache roots are validated with ANY semantics: a valid first
    root (Path.home()/.cache/fastembed) makes the cache ready even when the
    define_cache_dir root is empty/missing. (Verified against the code:
    embedding_cache_ready returns True on the first root that validates.)"""
    env = cache_env
    write_hf_cache(env["root_a"], blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    # env["root_b"] is intentionally left empty.
    assert env["engine"].embedding_cache_ready() is True


def test_second_root_valid_when_first_root_empty_is_ready(cache_env):
    """A valid define_cache_dir root alone makes the cache ready."""
    env = cache_env
    write_hf_cache(env["root_b"], blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    assert env["engine"].embedding_cache_ready() is True


def test_both_roots_invalid_is_rejected(cache_env):
    """When neither cache root validates, embedding_cache_ready() is False."""
    env = cache_env
    assert env["engine"].embedding_cache_ready() is False


# ========== Test 13: MockTextEmbedding shortcut returns ready ==========

def test_mock_named_text_embedding_shortcuts_to_ready(monkeypatch, tmp_path):
    """When fastembed.TextEmbedding.__name__ == 'MockTextEmbedding' the
    validator returns True immediately, even with zero cache dirs on disk."""
    engine = install_mock_named_fastembed(monkeypatch, tmp_path)
    assert engine.embedding_cache_ready() is True


# ========== Extra branch coverage ==========

def test_hf_metadata_list_form_is_ready(cache_env):
    """A list-form metadata (entries with 'path' keys) validates."""
    env = cache_env
    meta = [
        {"path": ONNX_PATH, "blob": DEFAULT_BLOB_ONNX},
        {"path": TOKENIZER_PATH, "blob": DEFAULT_BLOB_TOK},
    ]
    write_hf_cache(env["root_a"], meta=meta,
                   blobs=(DEFAULT_BLOB_ONNX, DEFAULT_BLOB_TOK))
    assert env["engine"].embedding_cache_ready() is True


def test_hf_metadata_empty_dict_is_rejected(cache_env):
    """An empty metadata dict (no files) is rejected."""
    env = cache_env
    write_hf_cache(env["root_a"], meta={})
    assert env["engine"].embedding_cache_ready() is False


def test_hf_metadata_entry_missing_blob_key_is_rejected(cache_env):
    """A dict entry without a truthy 'blob' key is rejected."""
    env = cache_env
    meta = {
        ONNX_PATH: {"path": ONNX_PATH},
        TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK},
    }
    write_hf_cache(env["root_a"], meta=meta, blobs=(DEFAULT_BLOB_TOK,))
    assert env["engine"].embedding_cache_ready() is False


def test_hf_metadata_entry_with_non_mapping_value_is_rejected(cache_env):
    """An entry that is neither a dict nor a string is rejected."""
    env = cache_env
    meta = {ONNX_PATH: 12345, TOKENIZER_PATH: {"blob": DEFAULT_BLOB_TOK}}
    write_hf_cache(env["root_a"], meta=meta, blobs=(DEFAULT_BLOB_TOK,))
    assert env["engine"].embedding_cache_ready() is False


def test_hf_metadata_invalid_json_is_rejected(cache_env):
    """Unparseable files_metadata.json is rejected (ValueError branch)."""
    env = cache_env
    hf_dir = env["root_a"] / HF_REPO_DIR
    snap_dir = hf_dir / "snapshots" / DEFAULT_SNAPSHOT
    (hf_dir / "refs").mkdir(parents=True, exist_ok=True)
    (hf_dir / "refs" / "main").write_text(DEFAULT_SNAPSHOT + "\n", encoding="utf-8")
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / "files_metadata.json").write_text("{not json", encoding="utf-8")
    assert env["engine"].embedding_cache_ready() is False
