from __future__ import annotations
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .readiness import ReadinessCheck

def render_supervisor_panel(project: str | None, agent_name: str, checks: list[ReadinessCheck]) -> str:
    proj_dir = Path(project or ".").resolve()
    project_name = proj_dir.name
    
    agent_display = agent_name
    if agent_name == "opencode":
        agent_display = "OpenCode"
    elif agent_name == "claude-code":
        agent_display = "Claude Code"
    elif agent_name == "cursor":
        agent_display = "Cursor"
    elif agent_name in ("agy", "antigravity"):
        agent_display = "Antigravity"
    else:
        agent_display = agent_name.title()

    border_top = "╔════════════════ OEM Runtime ════════════════╗"
    border_mid = "╠═════════════════════════════════════════════╣"
    border_bot = "╚═════════════════════════════════════════════╝"
    
    lines = [border_top]
    lines.append(f"║ Project     {project_name:<30} ║")
    lines.append(f"║ Agent       {agent_display:<30} ║")
    lines.append(border_mid)
    
    has_failure = False
    has_warning = False
    suggestions = []
    
    for check in checks:
        if check.status == "success":
            symbol = "✓"
        elif check.status == "warning":
            symbol = "⚠"
            has_warning = True
            if check.suggestion:
                suggestions.append(f"  • {check.name}: {check.suggestion}")
        else:
            symbol = "✗"
            has_failure = True
            if check.suggestion:
                suggestions.append(f"  • {check.name}: {check.suggestion}")
        
        line_content = f"{symbol} {check.name}"
        lines.append(f"║ {line_content:<43} ║")
        
    lines.append(border_mid)
    
    # Append final Runtime ready step
    if has_failure:
        ready_symbol = "✗"
        ready_name = "Runtime not ready"
    elif has_warning:
        ready_symbol = "⚠"
        ready_name = "Runtime degraded"
    else:
        ready_symbol = "✓"
        ready_name = "Runtime ready"
        
    ready_line = f"{ready_symbol} {ready_name}"
    lines.append(f"║ {ready_line:<43} ║")
    lines.append(border_bot)
    
    # Render troubleshooting suggestions if relevant
    if suggestions:
        lines.append("\nSuggested Repair Actions:")
        lines.extend(suggestions)
        lines.append("")
        
    return "\n".join(lines)

def print_project_memory_summary(context: dict, agent_display: str, duration: float):
    concepts_count = len(context.get("active_concepts", []))
    decisions_count = len(context.get("recent_decisions", context.get("active_decisions", [])))
    failures_count = len(context.get("relevant_failures", []))
    questions_count = len(context.get("open_questions", []))
    
    print("\nProject Memory Summary")
    print("┌─────────────────────────────────────────────┐")
    print(f"│  Concepts: {concepts_count:<10} │  Decisions: {decisions_count:<10} │")
    print(f"│  Failures: {failures_count:<10} │  Questions: {questions_count:<10} │")
    print("└─────────────────────────────────────────────┘")
    print(f"Startup duration: {duration:.2f}s")
    print(f"Launching {agent_display}...\n")
