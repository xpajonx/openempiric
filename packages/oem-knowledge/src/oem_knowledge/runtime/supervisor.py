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


class CommitProgressSupervisor:
    def __init__(self, width: int = 47, force_tty: bool = False):
        import sys
        self.width = width
        self.steps = [
            {"id": "transcript", "name": "Transcript Loaded", "status": "pending"},
            {"id": "reflection", "name": "Reflection Complete", "status": "pending"},
            {"id": "materialization", "name": "Materialization Complete", "status": "pending"},
            {"id": "index", "name": "Updating Search Index", "status": "pending"},
            {"id": "vault", "name": "Vault Sync Complete", "status": "pending"},
        ]
        self.is_tty = sys.stdout.isatty() or force_tty
        self.started = False
        self.last_lines_count = 0
        self.printed_non_tty_steps = set()

    def start(self):
        self.started = True
        self.render()

    def update_step(self, step_id: str, status: str, detail: str | None = None):
        for s in self.steps:
            if s["id"] == step_id:
                s["status"] = status
                if detail is not None:
                    s["detail"] = detail
                break
        if self.started:
            self.render()

    def render(self):
        import sys
        border_top = "╔" + "═" * (self.width - 2) + "╗"
        border_bot = "╚" + "═" * (self.width - 2) + "╝"
        
        title = " OEM Session Commit "
        padding = (self.width - 2 - len(title)) // 2
        header = "╔" + "═" * padding + title + "═" * (self.width - 2 - padding - len(title)) + "╗"
        
        if self.is_tty:
            lines = [header]
            for s in self.steps:
                if s["status"] == "success":
                    symbol = "✓"
                elif s["status"] == "running":
                    symbol = "⟳"
                elif s["status"] == "failed":
                    symbol = "✗"
                else:
                    symbol = " "
                
                line_text = f" {symbol} {s['name']}"
                lines.append(f"║ {line_text:<{self.width - 4}} ║")
                if "detail" in s and s["status"] == "running":
                    detail_text = f"   {s['detail']}"
                    lines.append(f"║ {detail_text:<{self.width - 4}} ║")
                    
            lines.append(border_bot)
            
            if self.last_lines_count > 0:
                sys.stdout.write(f"\033[{self.last_lines_count}A")
                for _ in range(self.last_lines_count):
                    sys.stdout.write("\033[K\n")
                sys.stdout.write(f"\033[{self.last_lines_count}A")
                
            output = "\n".join(lines)
            sys.stdout.write(output + "\n")
            sys.stdout.flush()
            self.last_lines_count = len(lines)
        else:
            if self.last_lines_count == 0:
                print(header)
                self.last_lines_count = 1
            
            for s in self.steps:
                if s["status"] != "pending":
                    detail_val = s.get("detail")
                    key = (s["id"], s["status"], detail_val)
                    if key not in self.printed_non_tty_steps:
                        self.printed_non_tty_steps.add(key)
                        symbol = "✓" if s["status"] == "success" else ("⟳" if s["status"] == "running" else "✗")
                        detail_str = f" ({detail_val})" if detail_val and s["status"] == "running" else ""
                        print(f"║ {symbol} {s['name']}{detail_str}")
            
            all_done = all(s["status"] in ("success", "failed") for s in self.steps)
            if all_done and not hasattr(self, "non_tty_finished"):
                self.non_tty_finished = True
                print(border_bot)


def render_commit_complete_panel(
    report_name: str,
    concepts_count: int,
    observations_count: int,
    duration: float,
    structured_events: int = 0,
    fallback_concepts: int = 0,
    file_observations: int = 0,
    width: int = 60
) -> str:
    from oem_tui.panels import render_panel
    lines = [
        f"Report: {report_name}",
        f"Concepts Materialized: {concepts_count}",
        f"Commit Time: {duration:.1f}s",
        "",
        "Knowledge Generated:",
        f"  Structured Events: {structured_events}",
        f"  Fallback Concepts: {fallback_concepts}",
        f"  File Observations: {file_observations}"
    ]
    return render_panel("Session End Complete", lines, status="ok", width=width)
