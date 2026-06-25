from __future__ import annotations
import os
import hashlib
from pathlib import Path
from oem_knowledge.project_layout import ProjectLayout

MAX_FILE_SIZE_BYTES = 500 * 1024  # 500 KB limit

def get_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""

def discover_instruction_sources(project_root: Path, layout: ProjectLayout) -> list[dict]:
    project_root = Path(project_root).resolve()
    
    # Define exact project-root files to check
    root_files = [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".github/copilot-instructions.md",
    ]
    
    found_paths: list[Path] = []
    
    # 1. Check exact root files
    for rf in root_files:
        p = project_root / rf
        if p.is_file():
            found_paths.append(p)
            
    # 2. Recurse .cursor/rules/**
    cursor_rules_dir = project_root / ".cursor" / "rules"
    if cursor_rules_dir.is_dir():
        for p in cursor_rules_dir.rglob("*.md"):
            if p.is_file():
                found_paths.append(p)
                
    # 3. Recurse docs/** for workflow*.md and instructions*.md
    docs_dir = project_root / "docs"
    if docs_dir.is_dir():
        for p in docs_dir.rglob("*.md"):
            if p.is_file():
                name_lower = p.name.lower()
                if name_lower.startswith("workflow") or name_lower.startswith("instructions"):
                    found_paths.append(p)
                    
    # 4. Recurse .oem/skills/** and .oem/workflows/**
    oem_skills_dir = layout.skills_dir
    if oem_skills_dir.is_dir():
        for p in oem_skills_dir.rglob("*.md"):
            if p.is_file():
                found_paths.append(p)
                
    oem_workflows_dir = layout.root / "workflows"
    if oem_workflows_dir.is_dir():
        for p in oem_workflows_dir.rglob("*.md"):
            if p.is_file():
                found_paths.append(p)

    # Filter, build metadata and check rules
    results = []
    seen = set()
    
    for path in found_paths:
        try:
            resolved_path = path.resolve()
        except Exception:
            resolved_path = path
            
        if resolved_path in seen:
            continue
        seen.add(resolved_path)
        
        # Check if the file is inside ignored directories
        try:
            rel_to_layout = resolved_path.relative_to(layout.root)
            # If it is inside layout.root, we ONLY allow skills and workflows directories
            parts = rel_to_layout.parts
            if parts and parts[0] not in ("skills", "workflows"):
                continue
        except ValueError:
            # Not under layout.root, perfectly fine
            pass
            
        if not path.exists():
            continue
            
        try:
            stat = path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            continue
            
        # Ignore files that are too large
        if size > MAX_FILE_SIZE_BYTES:
            continue
            
        try:
            rel_path = path.relative_to(project_root)
            rel_path_str = rel_path.as_posix()
        except ValueError:
            rel_path_str = path.as_posix()
            
        file_hash = get_sha256(path)
        if not file_hash:
            continue
            
        results.append({
            "path": rel_path_str,
            "hash": file_hash,
            "mtime": mtime,
            "size_bytes": size,
            "status": "active"
        })
        
    return results
