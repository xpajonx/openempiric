from __future__ import annotations

import tempfile
import shutil
import json
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from harness_knowledge.engine import KnowledgeEngine, HARNESS_DIR
from harness_knowledge.cli import run_agent


@pytest.fixture
def tmp_proj():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture
def mock_home(tmp_proj, monkeypatch):
    # Mock Path.home() to return a temporary directory instead of the actual user home
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

    # Write a wiki markdown file to verify description extraction
    concepts_dir = Path(tmp_proj) / HARNESS_DIR / "wiki"
    wiki_file = concepts_dir / "concept_001.md"
    wiki_file.write_text("---\nstatus: canonical\n---\n# Database Guidelines\nUse PostgreSQL for storage.", encoding="utf-8")

    # 2. Seed events (decisions and failures)
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
    handoff_file = Path(tmp_proj) / HARNESS_DIR / "session-handoff.md"
    handoff_file.write_text("# Session Handoff\n\n## Next Action\nImplement security keys.\n", encoding="utf-8")

    # 4. Create dummy opencode.jsonc in fake home
    opencode_dir = mock_home / ".config" / "opencode"
    opencode_dir.mkdir(parents=True)
    config_file = opencode_dir / "opencode.jsonc"
    config_file.write_text('// some comment\n{\n  "instructions": []\n}', encoding="utf-8")

    # Create dummy plugins folder for temp inst file validation
    plugins_dir = opencode_dir / "plugins"
    plugins_dir.mkdir(parents=True)

    # Mock subprocess.run so we don't start a real process
    with patch("subprocess.run") as mock_run:
        # We need to capture the state of opencode.jsonc during subprocess.run
        def capture_config(*args, **kwargs):
            assert config_file.exists()
            config_data = json.loads(config_file.read_text(encoding="utf-8"))
            assert "openempiric" in config_data["mcp"]
            assert "env" in config_data["mcp"]["openempiric"]
            assert "OEM_RUNTIME_CONTEXT" in config_data["mcp"]["openempiric"]["env"]
            
            oem = json.loads(config_data["mcp"]["openempiric"]["env"]["OEM_RUNTIME_CONTEXT"])
            # Verify active concepts
            assert len(oem["active_concepts"]) == 1
            assert oem["active_concepts"][0]["id"] == "concept_001"
            assert oem["active_concepts"][0]["name"] == "database-guidelines"
            assert oem["active_concepts"][0]["description"] == "Use PostgreSQL for storage."

            # Verify decisions & failures
            assert oem["active_decisions"] == ["Use PostgreSQL for storage."]
            assert oem["relevant_failures"] == ["Do not set timeout too low."]

            # Verify open questions
            assert oem["open_questions"] == ["Implement security keys."]

        mock_run.side_effect = capture_config

        # Run the agent
        run_agent("opencode", tmp_proj, eng)

        # Assert subprocess was called
        mock_run.assert_called_once()

    # 5. Verify cleanup
    # Config file restored to original comments
    assert config_file.read_text(encoding="utf-8") == '// some comment\n{\n  "instructions": []\n}'
    # Transient instructions file deleted
    temp_inst = plugins_dir / ".openempiric_temp_instructions.md"
    assert not temp_inst.exists()
