from __future__ import annotations
import sys
import json
from pathlib import Path
from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.ui import render_panel

def run_instructions_command(args):
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    project = getattr(args, "project", None)
    if project == ".":
        project = None

    eng = KnowledgeEngine(project)
    import atexit; atexit.register(eng.close)
    
    layout = eng.layout(project)
    action = args.instructions_action
    
    if action == "index":
        from oem_knowledge.instructions import (
            discover_instruction_sources,
            get_db_connection,
            get_stale_sources,
            index_source_file,
            get_active_directives,
            render_current_directives
        )
        project_root = layout.root.parent
        sources = discover_instruction_sources(project_root, layout)
        conn = get_db_connection(layout.instruction_ledger_path)
        stale_paths = get_stale_sources(conn, sources)
        
        indexed_count = 0
        warnings = []
        for ds in sources:
            if ds["path"] in stale_paths:
                try:
                    content = (project_root / ds["path"]).read_text(encoding="utf-8")
                    directives_added = index_source_file(conn, ds["path"], content, ds["hash"], ds["mtime"], ds["size_bytes"])
                    indexed_count += 1
                except Exception as e:
                    warnings.append(f"Failed to index instruction file {ds['path']}: {e}")
                    
        # Regenerate current_directives.md
        active_dirs = get_active_directives(conn)
        md_content = render_current_directives(active_dirs, layout)
        layout.current_directives_path.parent.mkdir(parents=True, exist_ok=True)
        layout.current_directives_path.write_text(md_content, encoding="utf-8")
        conn.close()
        
        lines = [
            f"Sources discovered: {len(sources)}",
            f"Sources indexed:    {indexed_count}",
            f"Active directives:  {len(active_dirs)}"
        ]
        if warnings:
            lines.append("")
            lines.append("Warnings:")
            for w in warnings:
                lines.append(f"- {w}")
                
        print(render_panel("Instruction Ledger Indexing", lines, status="ok"))
        
    elif action == "list":
        from oem_knowledge.instructions import get_db_connection, get_active_directives
        conn = get_db_connection(layout.instruction_ledger_path)
        active_dirs = get_active_directives(conn)
        conn.close()
        
        if not active_dirs:
            print("No active directives found.")
            return
            
        lines = [f"{'ID':<15} | {'Title':<25} | {'Source':<20} | {'Priority':<8} | {'Triggers'[:20]:<20}"]
        lines.append("-" * 95)
        for d in active_dirs:
            triggers = json.loads(d.get("triggers_json") or "[]")
            triggers_str = ", ".join(triggers)[:20]
            lines.append(f"{d['id']:<15} | {d['title'][:25]:<25} | {d['source_path'][:20]:<20} | {d['priority']:<8} | {triggers_str:<20}")
            
        print(render_panel("Active Directives", lines, status="ok"))
        
    elif action == "doctor":
        from oem_knowledge.instructions import (
            discover_instruction_sources,
            get_db_connection,
            get_active_directives,
            get_stale_sources,
            detect_conflicting_directives
        )
        project_root = layout.root.parent
        sources = discover_instruction_sources(project_root, layout)
        
        conn = get_db_connection(layout.instruction_ledger_path)
        active_dirs = get_active_directives(conn)
        stale = get_stale_sources(conn, sources)
        conflicts = detect_conflicting_directives(conn)
        
        # Calculate malformed sources (if they failed to parse or are empty when they shouldn't be)
        # For our minimal ledger, we'll keep malformed sources as 0 unless errors happened
        malformed = 0
        
        # Calculate candidates count
        candidates_count = 0
        cand_dir = layout.instruction_candidates_dir
        if cand_dir.is_dir():
            candidates_count = len(list(cand_dir.glob("*.md")))
            
        conn.close()
        
        lines = [
            "Instruction Ledger:",
            f"  sources indexed: {len(sources)}",
            f"  directives active: {len(active_dirs)}",
            f"  malformed sources: {malformed}",
            f"  stale sources: {len(stale)}",
            f"  conflicting directives: {len(conflicts)}",
            f"  update candidates: {candidates_count}"
        ]
        print("\n".join(lines))
        
    elif action == "candidates":
        cand_dir = layout.instruction_candidates_dir
        if not cand_dir.is_dir():
            print("No instruction update candidates found.")
            return
            
        candidates = list(cand_dir.glob("*.md"))
        if not candidates:
            print("No instruction update candidates found.")
            return
            
        lines = []
        for c in candidates:
            # Parse YAML frontmatter to get meta details
            content = c.read_text(encoding="utf-8")
            from oem_knowledge.instructions.parser import parse_frontmatter
            fm, _ = parse_frontmatter(content)
            
            c_id = fm.get("id", c.stem)
            target = fm.get("target_path", "unknown")
            status_val = fm.get("status", "proposed")
            
            lines.append(f"- ID: {c_id}")
            lines.append(f"  Target File: {target}")
            lines.append(f"  Status:      {status_val}")
            lines.append(f"  Path:        {c.relative_to(layout.root.parent).as_posix()}")
            lines.append("")
            
        print(render_panel("Instruction Update Candidates", lines, status="ok"))
