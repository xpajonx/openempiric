from __future__ import annotations

import sys
from pathlib import Path

from oem_knowledge.ui import render_panel


def run_clean_command(args) -> None:
    if getattr(args, "dry_run", False) and getattr(args, "apply", False):
        print(
            render_panel(
                "Clean Error",
                ["--dry-run and --apply cannot be used together."],
                status="error",
            )
        )
        sys.exit(2)
    if getattr(args, "backup", None) is False and not getattr(args, "apply", False):
        print(
            render_panel(
                "Clean Error",
                ["--no-backup is only valid with --apply."],
                status="error",
            )
        )
        sys.exit(2)

    from oem_knowledge.clean import analyze_cleanliness, apply_cleanups

    project = Path(getattr(args, "project", "") or ".").expanduser().resolve()
    scope = getattr(args, "scope", "all")
    report = analyze_cleanliness(project, scope)

    if getattr(args, "apply", False):
        backup = True if getattr(args, "backup", None) is None else bool(args.backup)
        report = apply_cleanups(project, report, backup=backup)

    status = "error" if report.get("status") == "error" else "ok"
    print(render_panel("OEM Clean", _render_clean_lines(report), status=status))
    if report.get("status") == "error":
        sys.exit(1)


def _render_clean_lines(report: dict) -> list[str]:
    lines = [
        f"Mode: {report.get('mode', 'dry_run')}",
        f"Scope: {report.get('scope', 'all')}",
        f"Project: {report.get('project', '')}",
        f"Status: {report.get('status', '')}",
        f"Backup: {report.get('backup_dir') or 'none'}",
        f"Report: {report.get('report_path') or 'none'}",
        "",
        "Self-ingestion:",
        f"  Suspect events: {report.get('self_ingestion', {}).get('suspect_events', 0)}",
        f"  Suspect concepts: {report.get('self_ingestion', {}).get('suspect_concepts', 0)}",
        "Duplicates:",
        f"  Duplicate runtime events: {report.get('duplicates', {}).get('duplicate_runtime_events', 0)}",
        "Structure:",
        f"  Orphan wiki files: {report.get('structure', {}).get('orphan_wiki_files', 0)}",
        f"  Missing wiki files: {report.get('structure', {}).get('missing_wiki_files', 0)}",
        f"  Duplicate slugs: {report.get('structure', {}).get('duplicate_slugs', 0)}",
        f"  Duplicate canonical names: {report.get('structure', {}).get('duplicate_canonical_names', 0)}",
        f"  Suspicious system concepts: {report.get('structure', {}).get('suspicious_system_concepts', 0)}",
        f"  Concept sources pointing at OEM artifacts: {report.get('structure', {}).get('concept_sources_oem_artifacts', 0)}",
        f"  Legacy harness artifacts: {report.get('structure', {}).get('legacy_harness_artifacts', 0)}",
        f"  Unknown harness files: {report.get('structure', {}).get('unknown_harness_files', 0)}",
        f"Changed files: {len(report.get('changed_files', []))}",
        f"Files backed up: {len(report.get('files_backed_up', []))}",
    ]
    if report.get("changed_files"):
        lines.extend(f"  {path}" for path in report["changed_files"])
    if report.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  {warning}" for warning in report["warnings"])
    return lines
