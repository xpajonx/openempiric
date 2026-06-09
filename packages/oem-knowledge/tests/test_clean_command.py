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


def _write_polluted_materialization_fixture(project: Path) -> Path:
    oem = _init_oem(project)
    runtime_events = oem / "runtime_events.jsonl"
    lines = [
        "Ingest | Materialized concept concept_023 (runtime-events) as validated",
        "Ingest | Materialized concept concept_023 (runtime-events) as validated",
        "Ingest | Materialized concept concept_024 (adapter-config) as validated",
        "Ingest | Materialized concept concept_023 (runtime-events) as validated",
    ]
    runtime_events.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return runtime_events


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
    assert not (tmp_path / ".oem" / "reports").exists()


def test_clean_detects_duplicate_materialization_events(tmp_path):
    _write_polluted_materialization_fixture(tmp_path)

    report = analyze_cleanliness(tmp_path, "duplicates")

    assert report["duplicates"]["duplicate_runtime_events"] == 2
    assert report["status"] == "issues_found"
    assert len(report["duplicates"]["duplicate_runtime_event_details"]) == 2


def test_clean_detects_self_ingestion_sources(tmp_path):
    oem = _init_oem(tmp_path)
    events = oem / "runtime_events.jsonl"
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


def test_clean_detects_suspicious_system_concepts(tmp_path):
    oem = _init_oem(tmp_path)
    registry = {
        "concept_023": {
            "concept_id": "concept_023",
            "canonical_name": "concept_023",
        },
        "concept_safe": {
            "concept_id": "concept_safe",
            "canonical_name": "User Authored Project Convention",
        },
        "concept_schema": {
            "concept_id": "concept_schema",
            "canonical_name": "schema",
        },
    }
    (oem / "concept_registry.json").write_text(json.dumps(registry), encoding="utf-8")

    report = analyze_cleanliness(tmp_path, "self-ingestion")

    assert report["system_concepts"]["suspicious_concepts"] == 2
    assert report["system_concepts"]["suspicious_concept_ids"] == [
        "concept_023",
        "concept_schema",
    ]
    assert report["status"] == "issues_found"


def test_clean_dry_run_does_not_modify_files(tmp_path):
    runtime_events = _write_polluted_materialization_fixture(tmp_path)
    before = runtime_events.read_text(encoding="utf-8")

    report = analyze_cleanliness(tmp_path, "all")

    assert report["mode"] == "dry_run"
    assert runtime_events.read_text(encoding="utf-8") == before
    assert not (tmp_path / ".oem" / "reports").exists()


def test_clean_apply_creates_backup(tmp_path):
    runtime_events = _write_polluted_materialization_fixture(tmp_path)
    before = runtime_events.read_text(encoding="utf-8")

    report = analyze_cleanliness(tmp_path, "all")
    applied = apply_cleanups(tmp_path, report)

    assert applied["mode"] == "apply"
    backup_dir = Path(applied["backup_dir"])
    assert backup_dir.is_dir()
    assert backup_dir.parent == tmp_path / ".oem" / "backups"
    assert (backup_dir / "runtime_events.jsonl").read_text(encoding="utf-8") == runtime_events.read_text(encoding="utf-8")


def test_clean_apply_creates_backup_dir(tmp_path):
    oem = _init_oem(tmp_path)
    (oem / "runtime_events.jsonl").write_text("{}\n", encoding="utf-8")

    applied = apply_cleanups(tmp_path, analyze_cleanliness(tmp_path, "all"))

    assert Path(applied["backup_dir"]).is_dir()
    assert Path(applied["backup_dir"]).name.startswith("clean-")


def test_clean_backup_preserves_relative_paths(tmp_path):
    oem = _init_oem(tmp_path)
    wiki_file = oem / "wiki" / "nested" / "concept_001.md"
    wiki_file.parent.mkdir()
    wiki_file.write_text("# Concept 001\n", encoding="utf-8")

    backup = create_clean_backup(tmp_path, "20260609-120000")

    assert (backup.backup_dir / "wiki" / "nested" / "concept_001.md").read_text(encoding="utf-8") == "# Concept 001\n"
    assert not (backup.backup_dir / "concept_001.md").exists()


def test_clean_backup_skips_missing_files_gracefully(tmp_path):
    (tmp_path / ".oem").mkdir()

    backup = create_clean_backup(tmp_path, "20260609-120001")

    assert backup.backup_dir.is_dir()
    assert any("concept_registry.json" in warning for warning in backup.warnings)
    assert any("runtime_events.jsonl" in warning for warning in backup.warnings)
    assert any("outcomes.jsonl" in warning for warning in backup.warnings)
    assert any("wiki" in warning for warning in backup.warnings)


def test_clean_apply_deduplicates_runtime_events(tmp_path):
    runtime_events = _write_polluted_materialization_fixture(tmp_path)

    report = analyze_cleanliness(tmp_path, "all")
    applied = apply_cleanups(tmp_path, report)

    lines = runtime_events.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "Ingest | Materialized concept concept_023 (runtime-events) as validated",
        "Ingest | Materialized concept concept_024 (adapter-config) as validated",
    ]
    assert applied["duplicates"]["removed_duplicate_runtime_events"] == 2
    assert str(runtime_events) in applied["changed_files"]


def test_clean_no_backup_without_apply_errors():
    with pytest.raises(SystemExit):
        _setup_parser().parse_args(["clean", "--no-backup"])


def test_clean_apply_writes_report(tmp_path):
    _init_oem(tmp_path)

    applied = apply_cleanups(tmp_path, analyze_cleanliness(tmp_path, "all"))

    report_path = Path(applied["report_path"])
    assert report_path.is_file()
    assert report_path.parent == tmp_path / ".oem" / "reports"
    assert "# OEM Clean Report" in report_path.read_text(encoding="utf-8")


def test_clean_report_contains_generated_by_metadata(tmp_path):
    _init_oem(tmp_path)

    applied = apply_cleanups(tmp_path, analyze_cleanliness(tmp_path, "all"))

    contents = Path(applied["report_path"]).read_text(encoding="utf-8")
    assert "generated_by: openempiric" in contents
    assert "source_type: oem_generated" in contents
    assert "command: oem clean" in contents
    assert "mode: apply" in contents


def test_clean_backup_failure_aborts_apply(tmp_path, monkeypatch):
    _init_oem(tmp_path)

    def fail_backup(harness, timestamp):
        raise OSError("disk full")

    monkeypatch.setattr(clean_module, "create_clean_backup", fail_backup)

    applied = apply_cleanups(tmp_path, analyze_cleanliness(tmp_path, "all"), backup=True)

    assert applied["status"] == "error"
    assert applied["report_path"] is None
    assert "disk full" in "\n".join(applied["warnings"])
    assert not (tmp_path / ".oem" / "reports").exists()


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


def test_clean_rejects_apply_and_dry_run_together():
    with pytest.raises(SystemExit):
        _setup_parser().parse_args(["clean", "--dry-run", "--apply"])


def test_clean_no_backup_only_valid_with_apply():
    with pytest.raises(SystemExit):
        _setup_parser().parse_args(["clean", "--no-backup"])

    args = _setup_parser().parse_args(["clean", "--apply", "--no-backup"])
    assert args.apply is True
    assert args.backup is False
