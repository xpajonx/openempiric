import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from oem_knowledge.adapters.grok.adapter import GrokAdapter
from oem_knowledge.engine import KnowledgeEngine


def test_grok_parse_transcript_chat_history(tmp_path):
    """Parse a realistic chat_history.jsonl snippet."""
    transcript = tmp_path / "chat_history.jsonl"
    content = (
        '{"type":"system","content":"You are Grok..."}\n'
        '{"type":"user","content":[{"type":"text","text":"Fix the bug in foo.py"}]}\n'
        '{"type":"assistant","content":"I will look at the code."}\n'
    )
    transcript.write_text(content, encoding="utf-8")

    adapter = GrokAdapter()
    parsed = adapter.parse_transcript(transcript)

    assert "User: Fix the bug in foo.py" in parsed
    assert "Agent: I will look at the code." in parsed


def test_grok_discover_and_expected_path(tmp_path, monkeypatch):
    """discover_latest + get_expected respect GROK_HOME override."""
    fake_home = tmp_path / "grokhome"
    sessions = fake_home / "sessions" / "%2Ftmp%2Fproj" / "session-xyz"
    sessions.mkdir(parents=True)
    (sessions / "chat_history.jsonl").write_text('{"type":"user","content":"hi"}\n', encoding="utf-8")
    (sessions / "summary.json").write_text("{}", encoding="utf-8")

    monkeypatch.setenv("GROK_HOME", str(fake_home))

    with patch("pathlib.Path.cwd", return_value=Path("/tmp/proj")):
        adapter = GrokAdapter()
        latest = adapter.discover_latest_transcript()
        assert latest is not None
        assert latest.name == "chat_history.jsonl"

        expected = adapter.get_expected_transcript_path("session-xyz")
        assert "session-xyz" in str(expected)
        assert expected.name == "chat_history.jsonl"


def test_grok_verify_mcp(monkeypatch, tmp_path):
    """verify_mcp returns reasonable result when binary is faked."""
    fake_home = tmp_path / "grokhome"
    bin_dir = fake_home / "bin"
    bin_dir.mkdir(parents=True)
    grok_bin = bin_dir / "grok"
    grok_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    grok_bin.chmod(0o755)

    monkeypatch.setenv("GROK_HOME", str(fake_home))
    with patch("shutil.which", return_value=str(grok_bin)):
        adapter = GrokAdapter()
        assert adapter.verify_mcp() is True
