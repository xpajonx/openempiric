from __future__ import annotations

import tempfile
import shutil
import json
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from oem_knowledge.engine import KnowledgeEngine, OEM_DIR
from oem_knowledge.cli import run_agent


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def mock_home(tmp_proj, monkeypatch):
    fake_home = Path(tmp_proj) / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return fake_home


def test_oem_runtime_context_injection(tmp_proj, mock_home):
    eng = KnowledgeEngine(tmp_proj)
    eng.init_project(tmp_proj)

    # 1. Seed local registry
    registry = eng._load_registry(tmp_proj)
    registry["concept_001"] = {
        "concept_id": "concept_001",
        "canonical_name": "database-guidelines",
        "status": "canonical",
        "confidence": 4,
        "evidence_count": 5
    }
    eng._save_registry(registry, tmp_proj)

    concepts_dir = Path(tmp_proj) / OEM_DIR / "wiki"
    wiki_file = concepts_dir / "concept_001.md"
    wiki_file.write_text("---\nstatus: canonical\n---\n# Database Guidelines\nUse PostgreSQL for storage.", encoding="utf-8")

    # 2. Seed events
    eng._append_event({
        "event_id": "dec-1",
        "timestamp": "2026-06-03T12:00:00Z",
        "project": tmp_proj,
        "session_id": "session-1",
        "event_type": "decision",
        "concept_candidates": ["database"],
        "summary": "Use PostgreSQL",
        "evidence": "Use PostgreSQL for storage.",
        "confidence": 1,
        "source": "chat",
        "schema_version": 1
    }, tmp_proj)

    eng._append_event({
        "event_id": "fail-1",
        "timestamp": "2026-06-03T12:05:00Z",
        "project": tmp_proj,
        "session_id": "session-1",
        "event_type": "failure",
        "concept_candidates": ["database"],
        "summary": "Connection timeout",
        "evidence": "Do not set timeout too low.",
        "confidence": 1,
        "source": "chat",
        "schema_version": 1
    }, tmp_proj)

    # 3. Seed handoff for open questions
    handoff_file = Path(tmp_proj) / OEM_DIR / "session-handoff.md"
    handoff_file.write_text("# Session Handoff\n\n## Next Action\nImplement security keys.\n", encoding="utf-8")

    # 4. Create dummy plugins folder in fake home
    plugins_dir = mock_home / ".config" / "opencode" / "plugins"
    plugins_dir.mkdir(parents=True)

    # 5. Mock subprocess.run and capture context file state
    from oem_knowledge.cli import _OEM_RUNTIME_CONTEXT_PATH, _OEM_TEMP_INSTRUCTIONS
    context_file = _OEM_RUNTIME_CONTEXT_PATH

    with patch("subprocess.run") as mock_run:
        def capture_context(*args, **kwargs):
            assert context_file.exists()
            with open(context_file) as f:
                oem = json.load(f)

            assert len(oem["active_concepts"]) == 1
            assert oem["active_concepts"][0]["id"] == "concept_001"
            assert oem["active_concepts"][0]["name"] == "database-guidelines"
            assert oem["active_concepts"][0]["description"] == "Use PostgreSQL for storage."

            assert oem["active_decisions"] == ["Use PostgreSQL for storage."]
            assert oem["relevant_failures"] == ["Do not set timeout too low."]
            assert oem["open_questions"] == ["Implement security keys."]

        mock_run.side_effect = capture_context
        run_agent("opencode", eng)
        mock_run.assert_called_once()

    # 6. Verify cleanup – transient files should be removed
    assert not context_file.exists()
    assert not _OEM_TEMP_INSTRUCTIONS.exists()
