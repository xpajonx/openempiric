from __future__ import annotations

import asyncio
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from harness_knowledge.engine import KnowledgeEngine

# Match [[type:target]] or [[target]] or [[type:target|label]]
WIKILINK_ANY_PATTERN = re.compile(
    r"\[\[(?:([a-zA-Z0-9_]+):)?([^\|\]]+)(?:\|([^\]]*))?\]\]"
)


def parse_concept_links_with_lines(file_path: Path) -> list[dict]:
    """Parse all target concept links from wikilinks in a file line-by-line."""
    results = []
    try:
        content = file_path.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), start=1):
            for match in WIKILINK_ANY_PATTERN.finditer(line):
                link_type, target, label = match.groups()
                results.append(
                    {
                        "raw": match.group(0),
                        "type": link_type,
                        "target": target.strip(),
                        "label": label.strip() if label else None,
                        "line": i,
                        "line_content": line,
                    }
                )
    except Exception:
        pass
    return results


async def audit_file_links(
    file_path: Path, executor: ThreadPoolExecutor
) -> tuple[Path, list[dict]]:
    loop = asyncio.get_running_loop()
    links = await loop.run_in_executor(
        executor, parse_concept_links_with_lines, file_path
    )
    return file_path, links


def normalize(name: str) -> str:
    """Normalize names to alphanumeric lowercase for robust fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def run_lint(
    project_path: Path, max_parallel: int = 4, fix: bool = False
) -> dict:
    concepts_dir = project_path / ".harness" / "directives" / "wiki_concepts"
    index_file = project_path / ".harness" / "directives" / "index.md"

    engine = KnowledgeEngine(project_path)
    sfs = engine._sfs(project_path)

    if not sfs.exists(concepts_dir):
        return {
            "status": "error",
            "message": f"Concepts directory does not exist: {concepts_dir}",
            "broken_links": [],
            "orphans": [],
            "healed_links": [],
            "files_scanned": 0,
        }

    concept_files = list(concepts_dir.glob("concept_*.md"))
    all_concept_ids = {f.stem for f in concept_files}

    # Load registry for alias matching
    registry = engine._load_registry()
    norm_map = {}
    for cid, data in registry.items():
        # Map concept ID
        norm_map[normalize(cid)] = cid
        # Map canonical name
        canon = data.get("canonical_name", "")
        if canon:
            norm_map[normalize(canon)] = cid
            norm_map[normalize(canon.replace("-", " "))] = cid
        # Map aliases
        for alias in data.get("aliases", []):
            norm_map[normalize(alias)] = cid

    # Extract links in parallel using thread pool
    executor = ThreadPoolExecutor(max_workers=max_parallel)
    tasks = [audit_file_links(f, executor) for f in concept_files]
    results = await asyncio.gather(*tasks)
    executor.shutdown(wait=True)

    broken_links = []
    healed_links = []
    incoming_links = {cid: set() for cid in all_concept_ids}

    # Track files we need to write/fix
    files_to_fix = {}

    for file_path, links in results:
        source_id = file_path.stem
        content_changed = False
        try:
            file_content = sfs.read_text(file_path)
        except Exception:
            continue

        for link in links:
            tid = link["target"]
            # Check if target is a valid concept ID
            if tid in all_concept_ids:
                incoming_links[tid].add(source_id)
                continue

            # Target is not a direct concept ID - try to normalize and resolve
            norm_tid = normalize(tid)
            if norm_tid in norm_map:
                resolved_id = norm_map[norm_tid]
                if resolved_id in incoming_links:
                    incoming_links[resolved_id].add(source_id)

                label_text = link["label"] or tid
                type_prefix = f"{link['type']}:" if link["type"] else ""
                corrected = f"[[{type_prefix}{resolved_id}|{label_text}]]"

                healed_links.append(
                    {
                        "source": source_id,
                        "file": str(file_path),
                        "line": link["line"],
                        "original": link["raw"],
                        "resolved": corrected,
                        "target_concept": resolved_id,
                    }
                )

                if fix:
                    file_content = file_content.replace(link["raw"], corrected)
                    content_changed = True
            else:
                # Truly broken link
                broken_links.append(
                    {
                        "source": source_id,
                        "file": str(file_path),
                        "line": link["line"],
                        "target": tid,
                        "content": link["line_content"].strip(),
                    }
                )

        if fix and content_changed:
            files_to_fix[file_path] = file_content

    # Apply fixes
    fixed_count = 0
    if fix and files_to_fix:
        for fpath, new_content in files_to_fix.items():
            try:
                sfs.write_text(fpath, new_content, force_allow_truncation=True)
                fixed_count += 1
            except Exception:
                pass

    # Detect orphans (0 incoming links and not link-indexed in index.md)
    index_content = ""
    if sfs.exists(index_file):
        try:
            index_content = sfs.read_text(index_file)
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
        "healed_links": healed_links,
        "orphans": orphans,
        "files_scanned": len(concept_files),
        "fixed_files_count": fixed_count,
    }
