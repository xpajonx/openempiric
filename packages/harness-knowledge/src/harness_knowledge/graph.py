from __future__ import annotations

import re
from pathlib import Path


WIKILINK_PATTERN = re.compile(r"\[\[([^|]+)(?:\|([^\]]+))?\]\]")


def find_wikilinks(text: str) -> list[tuple[str, str]]:
    matches = WIKILINK_PATTERN.findall(text)
    return [(target, label or target) for target, label in matches]


def add_wikilink(file_path: Path, target_id: str, display_name: str) -> bool:
    if not file_path.exists():
        return False

    content = file_path.read_text()
    link = f"[[{target_id}|{display_name}]]"

    if link in content:
        return False

    content = content.rstrip() + f"\n- {link} — {display_name}\n"
    file_path.write_text(content)
    return True


def update_concept_graph(concepts_dir: Path) -> dict:
    if not concepts_dir.exists():
        return {"status": "error", "message": f"Concepts dir not found: {concepts_dir}", "links_updated": 0}

    md_files = sorted(concepts_dir.rglob("*.md"))
    links_updated = 0

    for file in md_files:
        content = file.read_text()
        links = find_wikilinks(content)
        for target_id, label in links:
            target_file = concepts_dir / f"{target_id}.md"

            target_exists = False
            for f in concepts_dir.rglob("*.md"):
                if f.stem == target_id:
                    target_file = f
                    target_exists = True
                    break

            if target_exists:
                reciprocal = add_wikilink(target_file, file.stem, file.stem.replace("_", " "))
                if reciprocal:
                    links_updated += 1

    return {"status": "ok", "links_updated": links_updated, "files_scanned": len(md_files)}
