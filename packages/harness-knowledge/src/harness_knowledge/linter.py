from __future__ import annotations

import asyncio
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Match [[link]] or [[type:link]] or [[type:link|label]]
WIKILINK_PATTERN = re.compile(
    r"\[\[(?:([a-zA-Z0-9_]+):)?(concept_\d{3})(?:\|([^\]]*))?\]\]"
)


def parse_concept_links(file_path: Path) -> list[str]:
    """Parse all target concept IDs from wikilinks in a file."""
    try:
        content = file_path.read_text(encoding="utf-8")
        matches = WIKILINK_PATTERN.findall(content)
        return [target_id for _, target_id, _ in matches]
    except Exception:
        return []


async def audit_file_links(
    file_path: Path, executor: ThreadPoolExecutor
) -> tuple[Path, list[str]]:
    loop = asyncio.get_running_loop()
    links = await loop.run_in_executor(executor, parse_concept_links, file_path)
    return file_path, links


async def run_lint(project_path: Path, max_parallel: int = 4) -> dict:
    concepts_dir = project_path / ".harness" / "directives" / "wiki_concepts"
    index_file = project_path / ".harness" / "directives" / "index.md"

    if not concepts_dir.exists():
        return {
            "status": "error",
            "message": f"Concepts directory does not exist: {concepts_dir}",
            "broken_links": [],
            "orphans": [],
            "files_scanned": 0,
        }

    concept_files = list(concepts_dir.glob("concept_*.md"))
    all_concept_ids = {f.stem for f in concept_files}

    # Extract links in parallel using thread pool
    executor = ThreadPoolExecutor(max_workers=max_parallel)
    tasks = [audit_file_links(f, executor) for f in concept_files]
    results = await asyncio.gather(*tasks)
    executor.shutdown(wait=True)

    broken_links = []
    incoming_links = {cid: set() for cid in all_concept_ids}

    for file_path, target_ids in results:
        source_id = file_path.stem
        for tid in target_ids:
            if tid not in all_concept_ids:
                broken_links.append(
                    {"source": source_id, "target": tid, "file": str(file_path)}
                )
            else:
                incoming_links[tid].add(source_id)

    # Detect orphans (0 incoming links and not link-indexed in index.md)
    index_content = ""
    if index_file.exists():
        try:
            index_content = index_file.read_text(encoding="utf-8")
        except Exception:
            pass

    orphans = []
    for cid in all_concept_ids:
        has_incoming = len(incoming_links[cid]) > 0
        in_index = f"[[{cid}|" in index_content
        if not has_incoming and not in_index:
            orphans.append(cid)

    return {
        "status": "success",
        "broken_links": broken_links,
        "orphans": orphans,
        "files_scanned": len(concept_files),
    }
