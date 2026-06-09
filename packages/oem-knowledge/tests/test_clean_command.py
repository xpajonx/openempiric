from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_knowledge.clean import analyze_cleanliness, apply_cleanups
from oem_knowledge.cli.parser import _setup_parser


def _write_event(path: Path, **overrides):
    event = {
        "event_id": overrides.pop("event_id", "event-1"),
        "timestamp": "2026-01-01T00:00:00Z",
        "project": "test",
        "session_id": "session-1",
        "event_type": "observation",
        "concept_candidates": ["concept_a"],
        "summary": "summary",
        "evidence": "evidence",
        "confidence": 1,
        "source": "chat",
        "schema_version": 1,
    }
    event.update(overrides)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _init_oem(project: Path) -> Path:
    oem = project / ".oem"
    (oem / "wiki").mkdir(parents=True)
    (oem / "sessions").mkdir()
    (oem / "state").mkdir()
    (oem / "graph").mkdir()
    (oem / "skills").mkdir()
    (oem / "concept_registry.json").write_text("{}", encoding="utf-8")
    return oem


def test_clean_default_is_dry_run(tmp_path):
    args = _setup_parser().parse_args(["clean", "--project", str(tmp_path)])

    assert args.command == "clean"
    assert args.scope == "all"
    assert args.apply is False
    assert args.dry_run is False
    assert args.backup is None

    report = analyze_cleanliness(tmp_path, args.scope)
    assert report["mode"] == "dry_run"
    assert report["changed_files"] == []


def test_clean_rejects_apply_and_dry_run_together():
    with pytest.raises(SystemExit):
        _setup_parser().parse_args(["clean", "--dry-run", "--apply"])


def test_clean_apply_creates_backup_by_default(tmp_path):
    oem = _init_oem(tmp_path)
    events = oem / "events.jsonl"
    _write_event(events)

    report = analyze_cleanliness(tmp_path, "all")
    applied = apply_cleanups(tmp_path, report)

    assert applied["mode"] == "apply"
    assert applied["backup_dir"] is not None
    backup_dir = Path(applied["backup_dir"])
    assert backup_dir.is_dir()
    assert (backup_dir / ".oem" / "events.jsonl").read_text(encoding="utf-8") == events.read_text(encoding="utf-8")


def test_clean_no_backup_only_valid_with_apply():
    with pytest.raises(SystemExit):
        _setup_parser().parse_args(["clean", "--no-backup"])

    args = _setup_parser().parse_args(["clean", "--apply", "--no-backup"])
    assert args.apply is True
    assert args.backup is False


def test_clean_dry_run_does_not_modify_files(tmp_path):
    oem = _init_oem(tmp_path)
    events = oem / "events.jsonl"
    _write_event(events)
    before = events.read_text(encoding="utf-8")

    report = analyze_cleanliness(tmp_path, "all")

    assert report["mode"] == "dry_run"
    assert events.read_text(encoding="utf-8") == before


def test_clean_never_touches_adapter_config_paths(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    oem = _init_oem(tmp_path / "project")
    project = tmp_path / "project"
    events = oem / "events.jsonl"
    _write_event(events)

    forbidden_paths = [
        fake_home / ".config" / "opencode" / "settings.json",
        fake_home / ".codex" / "config.toml",
        project / "opencode.jsonc",
        project / ".codex" / "config.toml",
        project / ".opencode" / "plugin" / "plugins" / "openempiric.ts",
    ]
    before = {}
    for forbidden_path in forbidden_paths:
        forbidden_path.parent.mkdir(parents=True, exist_ok=True)
        forbidden_path.write_text(f"do not touch: {forbidden_path.name}", encoding="utf-8")
        before[forbidden_path] = forbidden_path.read_text(encoding="utf-8")

    report = analyze_cleanliness(project, "all")
    apply_cleanups(project, report, backup=True)

    for forbidden_path, content in before.items():
        assert forbidden_path.read_text(encoding="utf-8") == content


def test_clean_detects_duplicate_runtime_events(tmp_path):
    oem = _init_oem(tmp_path)
    events = oem / "events.jsonl"
    _write_event(events, event_id="first")
    _write_event(events, event_id="second")

    report = analyze_cleanliness(tmp_path, "duplicates")

    assert report["duplicates"]["duplicate_runtime_events"] == 1
    assert report["status"] == "issues_found"


def test_clean_detects_self_ingestion_suspects(tmp_path):
    oem = _init_oem(tmp_path)
    events = oem / "events.jsonl"
    _write_event(events, source=".oem/wiki/concept_a.md")
    registry = {
        "concept_a": {
            "concept_id": "concept_a",
            "canonical_name": "Concept A",
            "source": ".oem/sessions/session_report.md",
        }
    }
    (oem / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    report = analyze_cleanliness(tmp_path, "self-ingestion")

    assert report["self_ingestion"]["suspect_events"] == 1
    assert report["self_ingestion"]["suspect_concepts"] == 1
    assert report["status"] == "issues_found"
