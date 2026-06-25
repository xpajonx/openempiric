from __future__ import annotations
import os
from pathlib import Path

def get_relative_source_link(source_path: str, line_start: int, line_end: int, layout: ProjectLayout) -> str:
    # layout.root / ".runtime" is the directory where current_directives.md is written.
    # We want a relative link from layout.root / ".runtime" to the project_root / source_path
    runtime_dir = layout.root / ".runtime"
    project_root = layout.root.parent
    
    source_file_path = project_root / source_path
    try:
        rel = os.path.relpath(source_file_path, runtime_dir)
        # Use forward slashes
        rel_str = Path(rel).as_posix()
    except Exception:
        rel_str = source_path
        
    line_suffix = f"#L{line_start}" if line_start == line_end else f"#L{line_start}-L{line_end}"
    return f"[{source_path} lines {line_start}-{line_end}]({rel_str}{line_suffix})"

def render_current_directives(active_directives: list[dict], layout: ProjectLayout) -> str:
    lines = [
        "# Current OEM Directives",
        "",
        "This file contains active operational directives derived from project instructions.",
        "OEM automatically index and references these during planning (preflight) and commit.",
        "",
    ]
    
    critical_directives = [d for d in active_directives if d.get("priority") == "critical"]
    normal_directives = [d for d in active_directives if d.get("priority") != "critical"]
    
    if critical_directives:
        lines.append("## Required for this session")
        lines.append("")
        for d in critical_directives:
            source_link = get_relative_source_link(d["source_path"], d["line_start"], d["line_end"], layout)
            lines.append(f"### {d['title']}")
            lines.append("")
            lines.append(f"Source: {source_link}")
            lines.append("")
            lines.append("Rule:")
            lines.append(d["rule"])
            lines.append("")
            
            concepts = d.get("related_concepts") or []
            skills = d.get("related_skills") or []
            workflows = d.get("related_workflows") or []
            
            if concepts or skills or workflows:
                lines.append("Related OEM memory:")
                for c in concepts:
                    lines.append(f"- [[{c}]]")
                for s in skills:
                    lines.append(f"- [[{s}]]")
                for w in workflows:
                    lines.append(f"- [[{w}]]")
                lines.append("")
                
            triggers = d.get("triggers") or []
            if triggers:
                lines.append(f"Triggers: {', '.join(triggers)}")
                lines.append("")
                
    if normal_directives:
        lines.append("## Suggested directives")
        lines.append("")
        for d in normal_directives:
            source_link = get_relative_source_link(d["source_path"], d["line_start"], d["line_end"], layout)
            lines.append(f"### {d['title']}")
            lines.append("")
            lines.append(f"Source: {source_link}")
            lines.append("")
            lines.append("Rule:")
            lines.append(d["rule"])
            lines.append("")
            
            concepts = d.get("related_concepts") or []
            skills = d.get("related_skills") or []
            workflows = d.get("related_workflows") or []
            
            if concepts or skills or workflows:
                lines.append("Related OEM memory:")
                for c in concepts:
                    lines.append(f"- [[{c}]]")
                for s in skills:
                    lines.append(f"- [[{s}]]")
                for w in workflows:
                    lines.append(f"- [[{w}]]")
                lines.append("")
                
            triggers = d.get("triggers") or []
            if triggers:
                lines.append(f"Triggers: {', '.join(triggers)}")
                lines.append("")
                
    if not active_directives:
        lines.append("No active directives found.")
        
    return "\n".join(lines)

def render_directive_receipt(
    session_id: str,
    matched_directives: list[dict],
    applications: list[dict],
    drift_proposals: list[dict]
) -> str:
    lines = [
        "# Directive Receipt",
        "",
        f"Session ID: {session_id}",
        "",
        "## Matched Directives",
        ""
    ]
    
    app_map = {a["directive_id"]: a for a in applications}
    
    for md in matched_directives:
        d_id = md["id"]
        app = app_map.get(d_id)
        
        status_box = "[x]" if app and app["status"] == "applied" else "[ ]"
        evidence = app["evidence"] if app else "no evidence of application provided"
        
        lines.append(f"- {status_box} {md['title']}")
        lines.append(f"  - Source: {md['source_path']} lines {md['line_start']}-{md['line_end']}")
        lines.append(f"  - Rule: {md['rule']}")
        lines.append(f"  - Evidence: {evidence}")
        lines.append("")
        
    if not matched_directives:
        lines.append("No directives were matched or checked for this session.")
        lines.append("")
        
    lines.append("## Workflow Drift")
    lines.append("")
    if drift_proposals:
        for dp in drift_proposals:
            lines.append(f"- New recurring rule observed: {dp['reason']}")
    else:
        lines.append("No workflow drift detected.")
    lines.append("")
    
    lines.append("## Instruction Update Candidates")
    lines.append("")
    if drift_proposals:
        for dp in drift_proposals:
            lines.append(f"- {dp['id']}")
    else:
        lines.append("No instruction update candidates proposed.")
    lines.append("")
    
    return "\n".join(lines)
