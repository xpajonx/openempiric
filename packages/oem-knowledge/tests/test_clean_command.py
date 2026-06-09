from __future__ import annotations

import json
from pathlib import Path

import pytest

from oem_knowledge.clean import analyze_cleanliness, analyze_project, apply_cleanups
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
    assert (backup_dir / ".oem" / "events.jsonl").read_text(
        encoding="utf-8"
    ) == events.read_text(encoding="utf-8")


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
        forbidden_path.write_text(
            f"do not touch: {forbidden_path.name}", encoding="utf-8"
        )
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


def test_clean_runtime_events_apply_deduplicates_and_audits(tmp_path):
    oem = _init_oem(tmp_path)
    legacy_events = oem / "events.jsonl"
    legacy_events.unlink(missing_ok=True)
    runtime_events = oem / "runtime_events.jsonl"
    event = {
        "event_id": "first",
        "timestamp": "2026-01-01T00:00:00Z",
        "event_type": "observation",
        "summary": "same",
        "evidence": "same",
        "source": "chat",
    }
    runtime_events.write_text(
        json.dumps(event) + "\n" + json.dumps(event | {"event_id": "second"}) + "\n",
        encoding="utf-8",
    )

    report = analyze_cleanliness(tmp_path, "duplicates")
    applied = apply_cleanups(tmp_path, report, backup=True)

    assert applied["duplicates"]["duplicate_runtime_events"] == 0
    assert runtime_events.read_text(encoding="utf-8") == json.dumps(event) + "\n"
    audit_path = (
        Path(applied["backup_dir"]) / "audit" / "removed_duplicate_runtime_events.jsonl"
    )
    assert audit_path.exists()
    assert "second" in audit_path.read_text(encoding="utf-8")


def test_analyze_project_flags_suspicious_system_and_registry_consistency(tmp_path):
    oem = _init_oem(tmp_path)
    (oem / "wiki" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    registry = {
        "schema": {
            "concept_id": "schema",
            "canonical_name": "Schema",
            "slug": "schema",
            "source_path": ".oem/wiki/index.md",
        },
        "missing": {"concept_id": "missing", "canonical_name": "Missing"},
        "dup_a": {"concept_id": "dup_a", "canonical_name": "Same Name", "slug": "same"},
        "dup_b": {"concept_id": "dup_b", "canonical_name": "Same Name", "slug": "same"},
    }
    (oem / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    report = analyze_project(tmp_path, "all")

    assert report["structure"]["wiki_without_registry"] == 1
    assert report["structure"]["registry_without_wiki"] == 4
    assert report["structure"]["duplicate_slugs"] == 1
    assert report["structure"]["duplicate_canonical_names"] == 1
    assert report["structure"]["suspicious_system_concepts"] == 1
    assert report["structure"]["concept_sources_oem_artifacts"] == 1
    assert any(
        finding.code == "suspicious_system_concept" for finding in report.findings
    )
