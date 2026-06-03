from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from oem_tui.panels import render_panel


def migrate_from_central(central_path: str | Path = "", dry_run: bool = True) -> str:
    central = Path(central_path or "~/.config/opencode/memory").expanduser()
    if not central.exists():
        return render_panel(
            "Migration", [f"Central memory path not found: {central}"], status="error"
        )

    projects_dir = Path.home() / "projects"
    if not projects_dir.exists():
        return render_panel(
            "Migration",
            [f"Projects directory not found: {projects_dir}"],
            status="error",
        )

    registry_file = central / "wiki_registry.json"
    if not registry_file.exists():
        return render_panel(
            "Migration",
            ["No wiki_registry.json found in central memory."],
            status="info",
        )

    try:
        registry = json.loads(registry_file.read_text())
    except Exception as e:
        return render_panel(
            "Migration", [f"Error reading registry: {e}"], status="error"
        )

    project_files: dict[str, list[str]] = {}
    for path_str in registry:
        p = Path(path_str)
        try:
            rel = p.relative_to(projects_dir)
            proj = rel.parts[0]
            project_files.setdefault(proj, []).append(path_str)
        except Exception:
            project_files.setdefault("_orphaned", []).append(path_str)

    actions = []
    total_copied = 0

    for proj, files in sorted(project_files.items()):
        proj_dir = projects_dir / proj
        if not proj_dir.exists():
            actions.append(f"SKIP {proj}: project dir not found")
            continue

        harness_dir = proj_dir / ".harness"
        if dry_run:
            actions.append(
                f"WOULD MIGRATE {proj}: {len(files)} files → {harness_dir / 'directives/'}"
            )
            continue

        for src_path in files:
            src = Path(src_path)
            if not src.exists():
                continue
            try:
                rel = src.relative_to(proj_dir)
                dst = harness_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                total_copied += 1
            except Exception as e:
                actions.append(f"  ERROR {src.name}: {e}")

        actions.append(f"OK {proj}: {len(files)} files")

    if dry_run:
        lines = (
            [
                f"Central memory found at {central}",
                f"Registry has {len(registry)} files across {len(project_files)} projects",
                "",
                "Would migrate to per-project .harness/:",
            ]
            + [f"  {a}" for a in actions]
            + [
                "",
                "Run with dry_run=False to execute migration.",
            ]
        )
        return render_panel("Migration Preview", lines, status="info")
    else:
        lines = [
            f"Migration complete: {total_copied} files copied.",
        ] + [f"  {a}" for a in actions if not a.startswith("WOULD")]
        return render_panel("Migration Complete", lines, status="ok")


def main():
    dry_run = "--no-dry-run" not in sys.argv
    central = next(
        (
            a
            for a in sys.argv[1:]
            if not a.startswith("--") and "/" in a or "memory" in a.lower()
        ),
        "",
    )
    print(migrate_from_central(central, dry_run=dry_run))


if __name__ == "__main__":
    main()
