import json
import sys
import types
from pathlib import Path

import pytest

from oem_knowledge.engine import DEFAULT_EMBEDDING_MODEL, KnowledgeEngine


def install_recording_fastembed(monkeypatch, dimension=3, fail_local=False):
    calls = []

    class RecordingTextEmbedding:
        def __init__(self, *args, **kwargs):
            calls.append(dict(kwargs))
            self.model_name = kwargs.get("model_name")
            if fail_local and kwargs.get("local_files_only"):
                raise RuntimeError("local cache unavailable")

        def embed(self, texts):
            return [[1.0] + [0.0] * (dimension - 1) for _ in texts]

    module = types.ModuleType("fastembed")
    module.TextEmbedding = RecordingTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    return calls


def embedding_config_path(engine):
    return engine._resolve_harness() / "config" / "embedding_model.json"


def test_missing_embedding_config_uses_default(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    assert engine.resolve_embedding_model() == DEFAULT_EMBEDDING_MODEL


@pytest.mark.parametrize(
    "payload",
    ["not-json", {"embedding_model": None}, {"embedding_model": "   "}, []],
)
def test_invalid_embedding_config_falls_back_to_default(tmp_path, payload):
    engine = KnowledgeEngine(project_path=tmp_path)
    path = embedding_config_path(engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    assert engine.resolve_embedding_model() == DEFAULT_EMBEDDING_MODEL


def test_selected_model_is_used_after_restart(tmp_path, monkeypatch):
    calls = install_recording_fastembed(monkeypatch)
    engine = KnowledgeEngine(project_path=tmp_path)
    selected = "BAAI/bge-large-en-v1.5"

    result = engine.config_embedding_set_model(selected)
    fresh = KnowledgeEngine(project_path=tmp_path)
    loaded = fresh._load_local_model()

    assert result["status"] == "success"
    assert fresh.resolve_embedding_model() == selected
    assert loaded.model_name == selected
    assert calls[-1]["model_name"] == selected
    assert calls[-1]["local_files_only"] is True


def test_set_model_reloads_a_changed_model(tmp_path, monkeypatch):
    install_recording_fastembed(monkeypatch)
    engine = KnowledgeEngine(project_path=tmp_path)
    old_model = engine._load_local_model()

    engine.config_embedding_set_model("BAAI/bge-large-en-v1.5")
    new_model = engine._load_local_model()

    assert old_model.model_name == DEFAULT_EMBEDDING_MODEL
    assert new_model.model_name == "BAAI/bge-large-en-v1.5"
    assert new_model is not old_model


def test_set_model_rejects_blank_or_non_string(tmp_path):
    engine = KnowledgeEngine(project_path=tmp_path)
    with pytest.raises(ValueError, match="non-blank string"):
        engine.config_embedding_set_model("   ")
    with pytest.raises(ValueError, match="non-blank string"):
        engine.config_embedding_set_model(None)


def test_dry_run_reports_open_store_without_writing_or_resetting(tmp_path, monkeypatch):
    install_recording_fastembed(monkeypatch)
    engine = KnowledgeEngine(project_path=tmp_path)
    loaded = engine._load_local_model()
    store = engine.search.vector_store
    store.upsert("dry-run", "stored text", {"source": "test"}, None)
    path = embedding_config_path(engine)

    result = engine.config_embedding_set_model("BAAI/bge-large-en-v1.5", dry_run=True)

    assert result["status"] == "dry_run"
    assert result["chunks_to_reindex"] == 1
    assert not path.exists()
    assert engine.model is loaded


def test_warmup_if_needed_uses_selected_model_cache_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    cache_path = home / ".cache" / "fastembed" / "models--qdrant--bge-large-en-v1.5-onnx-q"
    cache_path.mkdir(parents=True)
    install_recording_fastembed(monkeypatch, fail_local=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.config_embedding_set_model("BAAI/bge-large-en-v1.5")

    result = engine.warmup_if_needed()

    assert result == {"status": "skipped", "reason": "local_cache_invalid"}


def test_indexed_embeddings_record_model_and_dimension(tmp_path, monkeypatch):
    install_recording_fastembed(monkeypatch, dimension=3)
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.search.set_retrieval_mode("hybrid")
    monkeypatch.setattr(engine, "embedding_cache_ready", lambda: True)
    wiki = engine._resolve_harness() / "wiki" / "note.md"
    wiki.parent.mkdir(parents=True, exist_ok=True)
    wiki.write_text("# Note\n\nTarget memory text.", encoding="utf-8")

    result = engine.search.index_all(force=True, quiet=True)
    rows = engine.search.vector_store.all_chunks()
    row = next(row for row in rows if row["metadata"].get("source", "").endswith("note.md"))

    assert result["status"] == "success"
    assert row["metadata"]["embedding_model"] == DEFAULT_EMBEDDING_MODEL
    assert row["metadata"]["embedding_dimension"] == 3


def test_incompatible_and_legacy_vectors_keep_sparse_search(tmp_path, monkeypatch, caplog):
    install_recording_fastembed(monkeypatch, dimension=3)
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.search.set_retrieval_mode("hybrid")
    monkeypatch.setattr(engine.search, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    store = engine.search.vector_store
    base = {"source": "test", "created_at": "0", "importance": "medium"}
    store.upsert(
        "good",
        "target compatible",
        {**base, "embedding_model": DEFAULT_EMBEDDING_MODEL, "embedding_dimension": 3},
        [1.0, 0.0, 0.0],
    )
    store.upsert(
        "wrong-model",
        "target wrong model",
        {**base, "embedding_model": "BAAI/other-model", "embedding_dimension": 3},
        [1.0, 0.0, 0.0],
    )
    store.upsert(
        "wrong-dimension",
        "target wrong dimension",
        {**base, "embedding_model": DEFAULT_EMBEDDING_MODEL, "embedding_dimension": 2},
        [1.0, 0.0],
    )
    store.upsert("legacy", "target legacy", base, [1.0, 0.0, 0.0])

    candidates = engine.search._collect_raw_candidates("target", auto_index=False)
    by_id = {candidate["id"]: candidate for candidate in candidates}

    assert set(by_id) == {"good", "wrong-model", "wrong-dimension", "legacy"}
    assert by_id["good"]["score"] > by_id["wrong-model"]["score"]
    assert by_id["good"]["score"] > by_id["wrong-dimension"]["score"]
    assert by_id["good"]["score"] > by_id["legacy"]["score"]
    assert "incompatible or legacy vectors" in caplog.text


def test_concept_event_embeddings_record_model_and_dimension(tmp_path, monkeypatch):
    install_recording_fastembed(monkeypatch, dimension=3)
    engine = KnowledgeEngine(project_path=tmp_path)
    engine.search.set_retrieval_mode("hybrid")
    monkeypatch.setattr(
        engine.state,
        "get_events",
        lambda **kwargs: [{"event_id": "e1", "summary": "Target event", "event_type": "decision"}],
    )

    assert engine.search.index_concept_events("concept-1") == 1
    row = engine.search.vector_store.all_chunks()[0]

    assert row["metadata"]["embedding_model"] == DEFAULT_EMBEDDING_MODEL
    assert row["metadata"]["embedding_dimension"] == 3
