from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path


from .ui import render_panel
from .engine import KnowledgeEngine, migrate_harness_to_oem
from .linter import run_lint

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("oem-knowledge")
except Exception:
    _VERSION = "0.9.5"

from .runtime import (
    _OEM_RUNTIME_CONTEXT_PATH,
    _OEM_TEMP_INSTRUCTIONS,
    _OPENCODE_PLUGINS_DIR,
    _compile_oem_context,
    run_agent,
    cmd_recover,
    SessionState,
)



def _strip_jsonc_comments(text: str) -> str:
    """Safely strip JSONC comments without destroying comments/slashes inside string literals (like URLs)."""
    pattern = re.compile(r'("(?:\\.|[^"\\])*")|//[^\r\n]*|/\*[\s\S]*?\*/')
    return pattern.sub(lambda m: m.group(1) if m.group(1) else "", text)


def _update_jsonc_mcp(original_text: str, mcp_config: dict) -> str:
    cleaned = _strip_jsonc_comments(original_text)
    config_data = json.loads(cleaned, strict=False)
    
    # Identify spans of all comments
    comment_spans = []
    for m_comment in re.finditer(r'//[^\r\n]*|/\*[\s\S]*?\*/', original_text):
        comment_spans.append(m_comment.span())
    
    def in_comment(pos):
        return any(start <= pos < end for start, end in comment_spans)
        
    # Check if config already has correct MCP config
    existing_mcp = config_data.get("mcp", {}).get("openempiric")
    if existing_mcp == mcp_config:
        return original_text
        
    new_text = original_text
    
    # 1. Find "mcp" key
    match_mcp = None
    for m in re.finditer(r'"mcp"\s*:\s*\{', original_text):
        if not in_comment(m.start()):
            match_mcp = m
            break
            
    if match_mcp:
        # "mcp" key exists, look for "openempiric"
        has_oe = "openempiric" in config_data.get("mcp", {})
        if has_oe:
            # Replace existing "openempiric" config
            match_oe = None
            for m in re.finditer(r'"openempiric"\s*:\s*\{', original_text):
                if not in_comment(m.start()):
                    match_oe = m
                    break
            if match_oe:
                start_pos = match_oe.start()
                brace_start = original_text.find('{', match_oe.end())
                if brace_start != -1:
                    depth = 1
                    brace_end = -1
                    for idx in range(brace_start + 1, len(original_text)):
                        if original_text[idx] == '{':
                            depth += 1
                        elif original_text[idx] == '}':
                            depth -= 1
                            if depth == 0:
                                brace_end = idx
                                break
                    if brace_end != -1:
                        serialized_oe = f'"openempiric": {json.dumps(mcp_config, indent=4).replace("\n", "\n    ")}'
                        new_text = original_text[:start_pos] + serialized_oe + original_text[brace_end + 1:]
        else:
            # Insert "openempiric" at the start of the "mcp" object
            pos = match_mcp.end()
            serialized_oe = f'\n    "openempiric": {json.dumps(mcp_config, indent=4).replace("\n", "\n    ")},'
            new_text = original_text[:pos] + serialized_oe + original_text[pos:]
    else:
        # "mcp" key does not exist. Append it before the last closing brace
        r_pos = original_text.rfind('}')
        if r_pos != -1:
            before_brace = original_text[:r_pos]
            last_char_match = re.search(r'\S\s*$', before_brace)
            comma = ""
            if last_char_match:
                last_char = last_char_match.group(0).strip()
                if last_char not in ("{", ",", "["):
                    comma = ","
            mcp_serialized = json.dumps({"openempiric": mcp_config}, indent=4).replace("\n", "\n  ")
            new_entry = f'{comma}\n  "mcp": {mcp_serialized}\n'
            new_text = original_text[:r_pos] + new_entry + original_text[r_pos:]
            
    return new_text


def check_mcp_server(command: list[str]) -> tuple[bool, bool, int, str]:
    """Test standard I/O MCP server reachability and functionality.

    Returns:
        (reachable, functional, num_tools, error_message)
    """
    import select
    
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        # 1. Reachability check: Send initialize
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "oem-doctor", "version": "1.0"}
            }
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
        
        ready = select.select([proc.stdout], [], [], 3.0)
        if not ready[0]:
            proc.kill()
            return False, False, 0, "Timeout waiting for initialize response"
            
        init_resp_line = proc.stdout.readline()
        if not init_resp_line:
            proc.kill()
            return False, False, 0, "Empty response on initialize"
            
        # 2. Tool count check: Send tools/list
        tools_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }
        proc.stdin.write(json.dumps(tools_req) + "\n")
        proc.stdin.flush()
        
        ready = select.select([proc.stdout], [], [], 3.0)
        if not ready[0]:
            proc.kill()
            return True, False, 0, "Timeout waiting for tools/list response"
            
        tools_resp_line = proc.stdout.readline()
        if not tools_resp_line:
            proc.kill()
            return True, False, 0, "Empty response on tools/list"
            
        tools_resp = json.loads(tools_resp_line)
        if "error" in tools_resp:
            proc.kill()
            return True, False, 0, f"Error from tools/list: {tools_resp['error']}"
            
        tools_list = tools_resp.get("result", {}).get("tools", [])
        num_tools = len(tools_list)
        
        # 3. Functional check: Send call tool knowledge_stats
        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "knowledge_stats",
                "arguments": {}
            }
        }
        proc.stdin.write(json.dumps(call_req) + "\n")
        proc.stdin.flush()
        
        ready = select.select([proc.stdout], [], [], 3.0)
        if not ready[0]:
            proc.kill()
            return True, False, num_tools, "Timeout waiting for tool call response"
            
        call_resp_line = proc.stdout.readline()
        proc.kill()
        
        if not call_resp_line:
            return True, False, num_tools, "Empty response on tool call"
            
        call_resp = json.loads(call_resp_line)
        if "error" in call_resp:
            return True, False, num_tools, f"Error calling knowledge_stats: {call_resp['error']}"
            
        content = call_resp.get("result", {}).get("content", [])
        if not content:
            return True, False, num_tools, "No content returned from knowledge_stats call"
            
        return True, True, num_tools, ""
    except Exception as e:
        return False, False, 0, str(e)


class Spinner:
    def __init__(self, message="Checking environment..."):
        self.message = message
        self.spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        import threading
        self.stop_running = threading.Event()
        self.thread = None
        self.enabled = sys.stdout.isatty()

    def _spin(self):
        if not self.enabled:
            return
        idx = 0
        while not self.stop_running.is_set():
            char = self.spinner_chars[idx % len(self.spinner_chars)]
            sys.stdout.write(f"\r\033[96m{char}\033[0m {self.message}")
            sys.stdout.flush()
            time.sleep(0.08)
            idx += 1
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def __enter__(self):
        if self.enabled:
            import threading
            self.thread = threading.Thread(target=self._spin, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.enabled:
            self.stop_running.set()
            if self.thread:
                self.thread.join()


def _resolve_project(args) -> str | None:
    """Normalise --project: ``""`` or ``"."`` → ``None`` (cwd)."""
    raw = getattr(args, "project", None)
    if raw and raw != ".":
        return raw
    return None

def _setup_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(description="OpenEmpiric (oem) CLI")
    parser.add_argument("--version", action="version", version=f"oem {_VERSION}", help="Show version and exit")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    sub.add_parser("status", help=argparse.SUPPRESS)
    sub.add_parser("stats", help=argparse.SUPPRESS)

    init_p = sub.add_parser("init", help=argparse.SUPPRESS)
    init_p.add_argument("project", type=str, nargs="?", default=".")

    search_p = sub.add_parser("search", help="[User] Search the project knowledge base")
    search_p.add_argument("query", type=str)
    search_p.add_argument("--k", type=int, default=3)
    search_p.add_argument("--project", type=str, default="")

    rebuild_p = sub.add_parser("rebuild", help="[Advanced] Replay the event store to rebuild the concept registry")
    rebuild_p.add_argument("--project", type=str, default="")

    events_p = sub.add_parser("events", help=argparse.SUPPRESS)
    events_p.add_argument("--project", type=str, default="")
    events_p.add_argument("--concept", type=str, default="")
    events_p.add_argument("--type", type=str, default="")
    events_p.add_argument("--session-id", type=str, default="")

    event_p = sub.add_parser("event", help=argparse.SUPPRESS)
    event_p.add_argument("event_id", type=str)
    event_p.add_argument("--project", type=str, default="")

    explain_p = sub.add_parser("explain", help=argparse.SUPPRESS)
    explain_p.add_argument("type", choices=["concept", "event"])
    explain_p.add_argument("id", type=str)
    explain_p.add_argument("--history", action="store_true", help="Show revision history")
    explain_p.add_argument("--project", type=str, default="")

    vault_p = sub.add_parser("vault", help=argparse.SUPPRESS)
    vault_p.add_argument("action", choices=["sync", "candidates", "promote", "demote"])
    vault_p.add_argument("concept_id", type=str, nargs="?", default="")
    vault_p.add_argument("--project", type=str, default="")

    identity_p = sub.add_parser("identity", help=argparse.SUPPRESS)
    identity_p.add_argument("action", choices=["scan", "review"])
    identity_p.add_argument("concept_a", type=str, nargs="?", default="")
    identity_p.add_argument("concept_b", type=str, nargs="?", default="")
    identity_p.add_argument("--project", type=str, default="")

    concept_p = sub.add_parser("concept", help=argparse.SUPPRESS)
    concept_p.add_argument("action", choices=["evolve", "health", "fitness"])
    concept_p.add_argument("concept_id", type=str, nargs="?", default="")
    concept_p.add_argument("--format", choices=["text", "yaml", "json"], default="text")
    concept_p.add_argument("--project", type=str, default="")

    contradictions_p = sub.add_parser("contradictions", help=argparse.SUPPRESS)
    contradictions_p.add_argument("--project", type=str, default="")

    merge_p = sub.add_parser("merge", help="[Advanced] Merge two duplicate/overlapping registry concepts together")
    merge_p.add_argument("primary_id", type=str)
    merge_p.add_argument("secondary_id", type=str)
    merge_p.add_argument("--auto", action="store_true", help="Automatically merge")
    merge_p.add_argument("--project", type=str, default="")

    lint_p = sub.add_parser("lint", help=argparse.SUPPRESS)
    lint_p.add_argument("--project", type=str, default="")
    lint_p.add_argument("--workers", type=int, default=4)
    lint_p.add_argument("--fix", action="store_true", help="Automatically heal links")

    session_start_p = sub.add_parser("session-start", help="[Internal] Restore pre-injection context and prepare workspace before agent run")
    session_start_p.add_argument("--project", type=str, default="")

    reflect_p = sub.add_parser("reflect", help="[Advanced] Dry-run session transcript reflection and concept extraction")
    reflect_p.add_argument("--chat", type=str, default="")
    reflect_p.add_argument("--debug", action="store_true", help="Show detailed extraction breakdown")
    reflect_p.add_argument("--project", type=str, default="")

    session_end_p = sub.add_parser("session-end", help="[Internal] Finalize context, run extraction, and commit learnings after agent exits")
    session_end_p.add_argument("--project", type=str, default="")
    session_end_p.add_argument("--chat", type=str, default="")
    session_end_p.add_argument("--session-id", type=str, default="")
    session_end_p.add_argument("--verbose", action="store_true", help="Show detailed reflection analysis")

    session_status_p = sub.add_parser("session-status", help=argparse.SUPPRESS)
    session_status_p.add_argument("--project", type=str, default="")

    run_p = sub.add_parser("run", help="[User] Run a managed coding agent session with dynamic config injection")
    run_p.add_argument("agent", type=str, help="opencode, claude-code, cursor, or custom command")
    run_p.add_argument("--project", type=str, default="")

    recover_p = sub.add_parser("recover", help="[Internal] Recover, commit, or abort crashed or unfinished agent sessions")
    recover_p.add_argument("--project", type=str, default="")
    recover_p.add_argument("--abort", action="store_true", help="Abort/discard the unfinished session")
    recover_p.add_argument("--status", action="store_true", help="Print current active session status")


    runtime_summary_p = sub.add_parser("runtime-summary", help=argparse.SUPPRESS)
    runtime_summary_p.add_argument("--days", type=int, default=7)
    runtime_summary_p.add_argument("--project", type=str, default="")

    metrics_p = sub.add_parser("metrics", help=argparse.SUPPRESS)
    metrics_p.add_argument("--project", type=str, default="")
    metrics_p.add_argument("--reset", action="store_true", help="Reset all metrics to default")
    metrics_p.add_argument("--export", type=str, help="Export raw metrics JSON to file path")
    metrics_p.add_argument("--usage-log", type=int, nargs="?", const=10, help="Print recent entries from usage_log.jsonl (default 10)")
    metrics_p.add_argument("--report", action="store_true", help="Report concept usage and decisions")
    metrics_p.add_argument("--used", type=str, default="[]", help="JSON array of referenced concept IDs")
    metrics_p.add_argument("--ignored", type=str, default="[]", help="JSON array of ignored concept IDs")
    metrics_p.add_argument("--decisions", type=str, default="[]", help="JSON array of decisions aligned")

    todo_p = sub.add_parser("todo", help=argparse.SUPPRESS)
    
    outcome_p = sub.add_parser("outcome", help="[Internal] Record manual session outcome status, concepts, and goal satisfaction")
    outcome_p.add_argument("status", choices=["success", "failure", "abandoned"])
    outcome_p.add_argument("referenced_concepts", type=str, nargs="*", default=[])
    outcome_p.add_argument("--reason", type=str, default="")
    outcome_p.add_argument("--session-id", type=str, default="")
    outcome_p.add_argument("--project", type=str, default="")
    outcome_p.add_argument("--goal-satisfaction", type=float, default=None, help="Goal satisfaction rating (0.0 to 1.0)")

    health_p = sub.add_parser("health", help="[User] Scan the workspace for stale concepts, duplicates, and contradicting knowledge")
    health_p.add_argument("--project", type=str, default="")
    health_p.add_argument("--stale-sessions", type=int, default=5, help="Number of sessions to check for staleness")
    health_p.add_argument("--similarity-threshold", type=float, default=0.85, help="Similarity threshold for duplicates")

    todo_sub = todo_p.add_subparsers(dest="todo_action", required=True)

    todo_read_p = todo_sub.add_parser("read")
    todo_read_p.add_argument("--project", type=str, default="")

    todo_write_p = todo_sub.add_parser("write")
    todo_write_p.add_argument("items", type=str)
    todo_write_p.add_argument("--project", type=str, default="")

    todo_advance_p = todo_sub.add_parser("advance")
    todo_advance_p.add_argument("item_id", type=str)
    todo_advance_p.add_argument("--status", type=str, default="")
    todo_advance_p.add_argument("--project", type=str, default="")

    doctor_p = sub.add_parser("doctor", help="[User] Check workspace health, plugin links, and warmer status")
    doctor_p.add_argument("--project", type=str, default="")

    warmup_p = sub.add_parser("warmup", help=argparse.SUPPRESS)
    warmup_p.add_argument("--project", type=str, default="")

    setup_p = sub.add_parser("setup", help="[User] Configure and register OpenCode agent workstation-level integration")
    setup_sub = setup_p.add_subparsers(dest="setup_target", required=True)
    setup_opencode = setup_sub.add_parser("opencode", help="Integrate OpenCode workspace settings and plugins")
    setup_opencode.add_argument("--repair", action="store_true", help="Forcefully overwrite and recreate all integration files")

    migrate_p = sub.add_parser("migrate", help="Migrate legacy .harness directory to .oem format")
    migrate_p.add_argument("--project", type=str, default="")

    config_p = sub.add_parser("config", help="[User] View or set configuration parameters")
    config_sub = config_p.add_subparsers(dest="config_target", required=True)
    config_retrieval = config_sub.add_parser("retrieval", help="View or set the retrieval mode")
    config_retrieval.add_argument("mode", nargs="?", choices=["bm25", "hybrid"], help="Retrieval mode to set")
    config_retrieval.add_argument("--project", type=str, default="")

    sub.add_parser("mcp", help="Start the MCP tool server")

    sub._choices_actions = [a for a in sub._choices_actions if a.help is not argparse.SUPPRESS]
    return parser


def cmd_setup_opencode(repair: bool = False) -> None:
    from oem_knowledge.ui import render_panel
    import importlib.resources as pkg_resources
    
    print("OEM OpenCode Setup\n")
    
    opencode_dir = Path.home() / ".config" / "opencode"
    plugins_dir = opencode_dir / "plugins"
    instructions_dir = opencode_dir / "instructions"
    skills_dir = opencode_dir / "skills"
    
    # 1. Create directories
    try:
        plugins_dir.mkdir(parents=True, exist_ok=True)
        instructions_dir.mkdir(parents=True, exist_ok=True)
        skills_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"✗ Failed to create directories under ~/.config/opencode/: {e}")
        sys.exit(1)
        
    plugin_dest = plugins_dir / "openempiric.ts"
    inst_dest = instructions_dir / "memory-start.md"
    jsonc_file = opencode_dir / "opencode.jsonc"
    
    migrated_plugin = False
    migrated_inst = False
    
    # Check legacy plugin
    if plugin_dest.exists() and not repair:
        try:
            content = plugin_dest.read_text(encoding="utf-8")
            if "knowledge_session_start" in content or "verify plugin array" in content or "session lifecycle is automatic" in content:
                migrated_plugin = True
        except Exception:
            pass

    # Check legacy instructions
    if inst_dest.exists() and not repair:
        try:
            content = inst_dest.read_text(encoding="utf-8")
            if "knowledge_session_start" in content or "knowledge_session_commit" in content or "verify plugin array" in content:
                migrated_inst = True
        except Exception:
            pass

    # 2. Install/update plugin
    plugin_installed = False
    try:
        plugin_src_path = pkg_resources.files("oem_knowledge").joinpath("plugins/openempiric.ts")
        
        should_write_plugin = repair or migrated_plugin or not plugin_dest.exists()
        
        if should_write_plugin:
            if plugin_src_path.exists():
                plugin_dest.write_text(plugin_src_path.read_text(encoding="utf-8"), encoding="utf-8")
                plugin_installed = True
            else:
                local_src = Path(__file__).resolve().parent.parent / "plugins" / "openempiric.ts"
                if local_src.exists():
                    plugin_dest.write_text(local_src.read_text(encoding="utf-8"), encoding="utf-8")
                    plugin_installed = True
                else:
                    print("✗ Failed to locate plugin source file 'plugins/openempiric.ts'.")
        else:
            plugin_installed = True  # Already present and valid
    except Exception as e:
        print(f"✗ Failed to install openempiric.ts plugin: {e}")
        
    # 3. Install instructions
    inst_installed = False
    try:
        should_write_inst = repair or migrated_inst or not inst_dest.exists()
        inst_content = (
            "## OpenEmpiric Session Status\n\n"
            "OpenEmpiric is already active for this session.\n\n"
            "Relevant project memory has been restored automatically.\n\n"
            "### Tool Usage Guidelines:\n"
            "- **Prefer calling OEM MCP tools directly** (e.g. `knowledge_search`) instead of executing shell commands (e.g. running `oem search` or `uv run ... oem search` in bash).\n"
            "- **Do not use shell execution** when a corresponding OEM tool is available.\n"
            "- Refer to active concepts and past failures during planning to align with existing decisions.\n"
            "- Report referenced memory concepts at session end using the `knowledge_usage_report` tool.\n"
            "- Use `knowledge_search` when additional project context is needed (such as reviewing project history, understanding prior decisions, or investigating known failures).\n"
            "- **Fallback Strategy**: If the MCP server is unreachable or a tool call fails, fall back to the OEM CLI (`oem search`), and only fall back to raw shell execution if the CLI is unavailable.\n"
        )
        if should_write_inst:
            inst_dest.write_text(inst_content, encoding="utf-8")
            inst_installed = True
        else:
            inst_installed = True  # Already present and valid
    except Exception as e:
        print(f"✗ Failed to install instructions/memory-start.md: {e}")
        
    # 4. Validate and update opencode.jsonc non-destructively
    config_verified = False
    try:
        inst_path_str = str(inst_dest.resolve())
        
        # Determine if dev workspace
        is_dev_workspace = False
        workspace_root = Path.cwd()
        while workspace_root.parent != workspace_root:
            pyproject_path = workspace_root / "pyproject.toml"
            if pyproject_path.exists():
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if 'name = "oem-mcp"' in content:
                        is_dev_workspace = True
                        break
                except Exception:
                    pass
            workspace_root = workspace_root.parent

        if is_dev_workspace:
            mcp_config = {
                "type": "local",
                "command": "uv",
                "args": [
                    "run",
                    "--directory",
                    str(workspace_root.resolve()),
                    "python",
                    "-m",
                    "oem_knowledge.server"
                ],
                "enabled": True,
                "timeout": 60000
            }
        else:
            mcp_config = {
                "type": "local",
                "command": "oem",
                "args": ["mcp"],
                "enabled": True,
                "timeout": 60000
            }

        if jsonc_file.exists():
            original_text = jsonc_file.read_text(encoding="utf-8")
            cleaned = _strip_jsonc_comments(original_text)
            
            try:
                config_data = json.loads(cleaned, strict=False)
            except Exception as e:
                print(f"✗ Failed to parse existing opencode.jsonc: {e}")
                print("Aborting setup to prevent configuration loss. Please repair or validate your config file manually.")
                sys.exit(1)
            
            inst_list = config_data.get("instructions", [])
            existing_mcp = config_data.get("mcp", {}).get("openempiric")
            
            need_inst_change = inst_path_str not in inst_list
            need_mcp_change = existing_mcp != mcp_config
            
            if not need_inst_change and not need_mcp_change:
                config_verified = True
            else:
                # Backup the file before writing
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup_file = jsonc_file.with_name(f"opencode.jsonc.backup-{timestamp}")
                shutil.copy2(jsonc_file, backup_file)
                
                new_text = original_text
                
                # 1. Update instructions path if needed
                if need_inst_change:
                    comment_spans = []
                    for m_comment in re.finditer(r'//[^\r\n]*|/\*[\s\S]*?\*/', new_text):
                        comment_spans.append(m_comment.span())
                    
                    def in_comment(pos):
                        return any(start <= pos < end for start, end in comment_spans)
                    
                    match = None
                    for m_inst in re.finditer(r'"instructions"\s*:\s*\[', new_text):
                        if not in_comment(m_inst.start()):
                            match = m_inst
                            break
                    
                    if match:
                        pos = match.end()
                        rest = new_text[pos:]
                        next_char_match = re.search(r'\S', rest)
                        if next_char_match and next_char_match.group(0) == ']':
                            new_text = new_text[:pos] + f'\n    "{inst_path_str}"\n  ' + new_text[pos:]
                        else:
                            new_text = new_text[:pos] + f'\n    "{inst_path_str}",' + new_text[pos:]
                    else:
                        r_pos = new_text.rfind('}')
                        if r_pos != -1:
                            before_brace = new_text[:r_pos]
                            last_char_match = re.search(r'\S\s*$', before_brace)
                            comma = ""
                            if last_char_match:
                                last_char = last_char_match.group(0).strip()
                                if last_char not in ("{", ",", "["):
                                    comma = ","
                            new_entry = f'{comma}\n  "instructions": [\n    "{inst_path_str}"\n  ]\n'
                            new_text = new_text[:r_pos] + new_entry + new_text[r_pos:]
                
                # 2. Update mcp server registration if needed
                if need_mcp_change:
                    new_text = _update_jsonc_mcp(new_text, mcp_config)
                
                jsonc_file.write_text(new_text, encoding="utf-8")
                config_verified = True
        else:
            # File doesn't exist, create a new config with both fields
            config_data = {
                "instructions": [inst_path_str],
                "mcp": {
                    "openempiric": mcp_config
                }
            }
            jsonc_file.write_text(json.dumps(config_data, indent=2), encoding="utf-8")
            config_verified = True
    except Exception as e:
        print(f"✗ Failed to validate or update opencode.jsonc: {e}")
        
    # Clear any active cache files in ~/.config/opencode/
    try:
        temp_inst = opencode_dir / "plugins" / ".openempiric_temp_instructions.md"
        if temp_inst.exists():
            temp_inst.unlink()
    except Exception:
        pass
        
    # Report summary
    lines = []
    lines.append("✓ Plugin installed" if plugin_installed else "✗ Plugin installation failed")
    lines.append("✓ Instructions installed" if inst_installed else "✗ Instructions installation failed")
    lines.append("✓ Configuration verified" if config_verified else "✗ Configuration verification failed")
    
    if migrated_plugin:
        lines.append("ℹ Migrated legacy plugin openempiric.ts")
    if migrated_inst:
        lines.append("ℹ Migrated legacy instructions memory-start.md")
    if repair:
        lines.append("ℹ Re-installed all components (--repair)")
        
    if plugin_installed and inst_installed and config_verified:
        lines.append("")
        lines.append("OpenCode integration ready.")
        print(render_panel("OEM OpenCode Setup", lines, status="ok"))
    else:
        print(render_panel("OEM OpenCode Setup Failed", lines, status="error"))
        sys.exit(1)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    parser = _setup_parser()
    args = parser.parse_args()
    project = _resolve_project(args)
    eng = KnowledgeEngine(project)

    # Check for unfinished session for status/stats commands only
    # (run command auto-recovers in runner.py)
    if args.command in ("status", "stats"):
        try:
            harness = eng._resolve_harness(project)
            active_session_file = harness / "state" / "active_session.json"
            session_state = SessionState.load(active_session_file)
            if session_state:
                sid = session_state.session_id
                print(render_panel(
                    "Warning: Unfinished Session Detected",
                    [
                        f"An unfinished session was found (ID: {sid}).",
                        "The agent may have crashed or exited unexpectedly.",
                        "",
                        "To query status:       oem recover --status",
                        "To commit learnings:   oem recover",
                        "To discard session:    oem recover --abort"
                    ],
                    status="warning"
                ))
        except Exception:
            pass



    try:
        if args.command in ("status", "stats"):
            s = eng.search.stats()
            lines = [
                f"Chunks: {s['total_chunks']}",
                f"DB size: {s['db_size_mb']:.2f} MB",
                f"Path: {s['harness_path']}",
            ]
            print(render_panel("Stats", lines, status="stats"))

        elif args.command == "init":
            res = eng.init_project(args.project)
            lines = (
                [res["message"]]
                + [f"  \U0001f4c1 {d}" for d in res.get("created_directories", [])]
                + [f"  \U0001f4c4 {f}" for f in res.get("created_files", [])]
            )
            print(render_panel("Init Complete", lines, status="bootstrap"))

        elif args.command == "search":
            results = eng.search.search(args.query, k=args.k)
            lines = [f'Query: "{args.query}"', f"Results: {len(results)}", ""]
            for idx, r in enumerate(results):
                lines.append(
                    f"{idx + 1}. [{r['metadata'].get('rel_path', 'unknown')}] (score: {r['score']:.4f})"
                )
                lines.append(f"   {r['document'][:150]}...")
                lines.append("")
            if not results:
                lines = [f"No matches for: '{args.query}'"]
            print(render_panel("Search Results", lines, status="search"))

        elif args.command == "session-start":
            res = eng.restore_session_state(project)
            lines = [
                f"Goals: {len(res.get('active_goals', []))}",
                f"Blockers: {len(res.get('blockers', []))}",
                f"Files: {len(res.get('recommended_files', []))}",
                f"Global Concepts: {len(res.get('global_concepts', []))}",
            ]
            print(render_panel("Session Start", lines, status="restore"))

        elif args.command == "session-end":
            session_started_at = None
            try:
                harness = eng._resolve_harness(project)
                active_session_file = harness / "state" / "active_session.json"
                session_state = SessionState.load(active_session_file)
                if session_state:
                    session_started_at = session_state.started_at
            except Exception:
                pass

            commit_start = time.time()
            res = eng.session_commit(
                project,
                args.chat,
                args.session_id,
                session_started_at=session_started_at
            )
            commit_duration = time.time() - commit_start

            from oem_knowledge.runtime.supervisor import render_commit_complete_panel
            report_name = Path(res['report_path']).name
            concepts_count = len(res.get('materialized_log', []))
            exp = res.get("explainability", {})
            obs_count = exp.get("file_observations", 0)

            print(
                render_commit_complete_panel(
                    report_name=report_name,
                    concepts_count=concepts_count,
                    observations_count=obs_count,
                    duration=commit_duration,
                    structured_events=exp.get("structured_events", 0),
                    fallback_concepts=exp.get("fallback_extractions", 0),
                    file_observations=exp.get("file_observations", 0)
                )
            )
            if args.verbose and "explainability" in res:
                exp = res["explainability"]
                debug_lines = [
                    f"Chat Lines Processed:   {exp.get('chat_lines_processed', 0)}",
                    f"Structured Events:      {exp.get('structured_events_found', 0)}",
                    f"Fallback Extraction:    {'Yes' if exp.get('fallback_extraction_used') else 'No'}",
                    f"File Observations:      {exp.get('file_observations_count', 0)}",
                ]
                generated = exp.get("generated_concepts", [])
                if generated:
                    debug_lines.append("")
                    debug_lines.append("Generated Concepts:")
                    for gc in generated:
                        debug_lines.append(f"  \u2022 {gc}")
                print(render_panel("Reflection Analysis", debug_lines, status="info"))

        elif args.command == "reflect":
            from oem_knowledge.services.reflection import ReflectionService
            rs = ReflectionService(eng)
            res = rs.reflect_session(project, args.chat)
            if args.debug and "explainability" in res:
                exp = res["explainability"]
                debug_lines = [
                    f"Chat Lines Processed:   {exp.get('chat_lines_processed', 0)}",
                    f"Structured Events:      {exp.get('structured_events_found', 0)}",
                    f"Fallback Extraction:    {'Yes' if exp.get('fallback_extraction_used') else 'No'}",
                    f"File Observations:      {exp.get('file_observations_count', 0)}",
                ]
                generated = exp.get("generated_concepts", [])
                file_obs = [
                    f"Modified: {e['concept_candidates'][0]}"
                    for e in res.get("canonical_events", [])
                    if e.get("source") == "diff"
                ]
                if generated:
                    debug_lines.append("")
                    debug_lines.append("Generated Concepts:")
                    for gc in generated:
                        debug_lines.append(f"  \u2022 {gc}")
                if file_obs:
                    debug_lines.append("")
                    debug_lines.append("File Observations:")
                    for fo in file_obs:
                        debug_lines.append(f"  \u2022 {fo}")
                print(render_panel("Reflection Analysis", debug_lines, status="info"))
            else:
                events = res.get("knowledge_events", [])
                lines = [f"Total events: {len(events)}"]
                for ev in events[:10]:
                    lines.append(f"  [{ev['type'].upper()}] {ev['concept'][:60]}")
                print(render_panel("Reflection Result", lines, status="ok"))

        elif args.command == "rebuild":
            res = eng.state.rebuild_registry(project)
            print(
                render_panel(
                    "Registry Rebuilt",
                    [
                        res.get("message", ""),
                        f"Materialized concepts: {res.get('materialized', 0)}",
                    ],
                    status="ok",
                )
            )

        elif args.command == "events":
            events = eng.get_events(
                project,
                concept=args.concept,
                event_type=args.type,
                session_id=args.session_id,
            )
            lines = [f"Total: {len(events)}"] + [
                f"  [{ev['event_type'].upper()}] {ev.get('summary', '')[:80]}"
                for ev in events[:20]
            ]
            print(render_panel("Events", lines, status="ok"))

        elif args.command == "event":
            try:
                ev = eng.get_event(project, args.event_id)
                print(
                    render_panel(
                        "Event",
                        [
                            f"Type: {ev['event_type']}",
                            f"Summary: {ev.get('summary', '')}",
                            f"Evidence: {ev.get('evidence', '')}",
                        ],
                        status="ok",
                    )
                )
            except KeyError:
                print(render_panel("Not Found", [f"No event: {args.event_id}"], status="error"))

        elif args.command == "explain":
            if args.type == "concept":
                if args.history:
                    history = eng.materialization.get_concept_history(args.id, project)
                    lines = [f"Revision History for Concept: {args.id}", ""]
                    for entry in history:
                        lines.append(f"\U0001f4c5 [{entry.get('timestamp')}] - File: {entry.get('file_name')}")
                        if entry.get("diff"):
                            lines.append("Diff:")
                            for diff_line in entry.get("diff").splitlines():
                                lines.append(f"  {diff_line}")
                        lines.append("")
                    if not history:
                        lines.append("No revision history found.")
                    print(render_panel("Concept History", lines, status="ok"))
                else:
                    res = eng.state.explain_concept(project, args.id)
                    if res.get("status") == "error":
                        print(render_panel("Concept Not Found", [res.get("message", "")], status="error"))
                    else:
                        cdata = res["explanation"]["concept"]
                        lines = [
                            f"Concept: {cdata.get('canonical_name', '').title()} ({cdata.get('concept_id', '')})",
                            f"Status: {cdata.get('status', '').upper()}",
                            f"Confidence: {cdata.get('confidence', '')}/5",
                            f"Total Events: {res['explanation'].get('total_events', 0)}",
                            f"Aliases: {', '.join(cdata.get('aliases', []))}",
                            "",
                            "Recent Evidence:",
                        ]
                        for ev in res["explanation"].get("recent_evidence", []):
                            lines.append(f"  - {ev}")
                        print(render_panel("Concept Explanation", lines, status="ok"))
            else:
                try:
                    ev = eng.get_event(project, args.id)
                    lines = [
                        f"Event ID: {ev.get('event_id')}",
                        f"Type:     {ev.get('event_type')}",
                        f"Summary:  {ev.get('summary')}",
                        f"Evidence: {ev.get('evidence')}",
                    ]
                    print(render_panel("Event Explanation", lines, status="ok"))
                except KeyError:
                    print(render_panel("Event Not Found", [f"No event: {args.id}"], status="error"))

        elif args.command == "lint":
            target = Path(args.project) if args.project else Path.cwd()
            res = asyncio.run(run_lint(target, max_parallel=args.workers, fix=args.fix))
            if res["status"] == "error":
                print(render_panel("Lint Error", [res["message"]], status="error"))
                sys.exit(1)
            else:
                lines = [
                    f"Files scanned: {res.get('files_scanned', 0)}",
                    f"Broken links:  {len(res.get('broken_links', []))}",
                    f"Healed links:  {len(res.get('healed_links', []))}",
                    f"Orphan nodes:  {len(res.get('orphans', []))}",
                ]
                if args.fix:
                    lines.append(f"Files fixed:   {res.get('fixed_files_count', 0)}")
                lines.append("")

                for bl in res.get("broken_links", []):
                    lines.append(f"  \u274c Broken link: {bl['source']}:{bl['line']} -> {bl['target']}")
                if res.get("healed_links"):
                    action = "Fixed" if args.fix else "Can Heal"
                    lines.append(f"Healed links ({action}):")
                    for hl in res["healed_links"]:
                        lines.append(
                            f"  \u2705 {hl['source']}:{hl['line']} -> resolved to {hl['target_concept']} (originally: {hl['original']})"
                        )
                for op in res.get("orphans", []):
                    lines.append(f"  \u26a0\ufe0f Orphan concept: {op}")
                print(
                    render_panel(
                        "Lint Results",
                        lines,
                        status="error" if res.get("broken_links") else "ok",
                    )
                )
                if res.get("broken_links"):
                    sys.exit(1)

        elif args.command == "vault":
            from oem_knowledge.vault import GlobalVault
            vault = GlobalVault()
            if args.action == "sync":
                try:
                    local_reg = eng.state._load_registry(project)
                    concepts_dir = eng._concepts_dir(project)
                    vault.sync_from_registry(local_reg, concepts_dir)
                    print(render_panel("Vault Sync", ["Global vault synchronized successfully."], status="ok"))
                except Exception as e:
                    print(render_panel("Vault Sync Failure", [f"Error: {e}"], status="error"))
            elif args.action == "candidates":
                candidates = vault.vault_candidates(project)
                lines = [f"Candidates: {len(candidates)}", ""]
                for c in candidates:
                    lines.append(f"  - {c['concept_id']} ({c['canonical_name']}) - Evidences: {c['evidence_count']}, Occurrences: {c['project_occurrences']}")
                print(render_panel("Global Vault Candidates", lines, status="ok"))
            elif args.action == "promote":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for promotion."], status="error"))
                else:
                    try:
                        vault.promote_to_global(args.concept_id, project)
                        print(render_panel("Vault Promotion", [f"Successfully promoted {args.concept_id} to Global Vault."], status="ok"))
                    except Exception as e:
                        print(render_panel("Error", [f"Promotion failed: {e}"], status="error"))
            elif args.action == "demote":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for demotion."], status="error"))
                else:
                    try:
                        vault.demote_from_global(args.concept_id, project)
                        print(render_panel("Vault Demotion", [f"Successfully demoted {args.concept_id} from Global Vault."], status="ok"))
                    except Exception as e:
                        print(render_panel("Error", [f"Demotion failed: {e}"], status="error"))

        elif args.command == "identity":
            from oem_knowledge.identity_resolver import SemanticIdentityResolver
            resolver = SemanticIdentityResolver(eng)
            if args.action == "scan":
                duplicates = resolver.scan_duplicates(project)
                lines = [f"Potential duplicates found: {len(duplicates)}", ""]
                for d in duplicates:
                    lines.append(f"  - Pair: {d['concept_a']} & {d['concept_b']}")
                    lines.append(f"    Names: {d['name_a']} | {d['name_b']}")
                    lines.append(f"    Similarity: {d['similarity']:.4f}")
                    lines.append("")
                print(render_panel("Identity Scan", lines, status="ok"))
            elif args.action == "review":
                if not args.concept_a or not args.concept_b:
                    print(render_panel("Error", ["Two concept IDs required for review."], status="error"))
                else:
                    registry = eng.state._load_registry(project)
                    if args.concept_a not in registry or args.concept_b not in registry:
                        print(render_panel("Error", ["One or both concepts not found in registry."], status="error"))
                    else:
                        lines = [
                            f"Reviewing similarity for {args.concept_a} and {args.concept_b}:",
                            f"  Concept A: {registry[args.concept_a].get('canonical_name')}",
                            f"  Concept B: {registry[args.concept_b].get('canonical_name')}",
                        ]
                        print(render_panel("Identity Review", lines, status="ok"))

        elif args.command == "concept":
            if args.action == "evolve":
                if not args.concept_id:
                    print(render_panel("Error", ["Concept ID required for evolution."], status="error"))
                else:
                    from oem_knowledge.evolution import ConceptEvolutionEngine
                    evolve_engine = ConceptEvolutionEngine(eng)
                    res = evolve_engine.evolve_concept(args.concept_id, project)
                    if res.get("status") == "error":
                        print(render_panel("Evolution Failure", [res.get("message", "")], status="error"))
                    else:
                        print(render_panel("Concept Evolved", [res.get("message", "")], status="ok"))
            elif args.action == "health":
                registry = eng.state._load_registry(project)
                from oem_knowledge.health import calculate_concept_health
                if args.concept_id:
                    if args.concept_id not in registry:
                        print(render_panel("Error", [f"Concept {args.concept_id} not found."], status="error"))
                    else:
                        cdata = registry[args.concept_id]
                        score = calculate_concept_health(cdata)
                        lines = [
                            f"Concept: {cdata.get('canonical_name')} ({args.concept_id})",
                            f"Health Score: {score}/100",
                            f"  Confidence: {cdata.get('confidence', 1)}/5",
                            f"  Evidence Count: {cdata.get('evidence_count', 0)}",
                            f"  Failure Count: {cdata.get('failure_count', 0)}",
                            f"  Status: {cdata.get('status', 'candidate')}",
                        ]
                        print(render_panel("Concept Health Breakdown", lines, status="ok"))
                else:
                    lines = [f"Total concepts scanned: {len(registry)}", ""]
                    for cid, cdata in registry.items():
                        score = calculate_concept_health(cdata)
                        lines.append(f"  - {cid} ({cdata.get('canonical_name')}) -> Health: {score}/100 (Status: {cdata.get('status')})")
                    print(render_panel("System Health Summary", lines, status="ok"))

            elif args.action == "fitness":
                def dict_to_yaml(d: dict, indent: int = 0) -> str:
                    lines_yaml = []
                    for k, v in d.items():
                        prefix = " " * indent
                        if isinstance(v, dict):
                            lines_yaml.append(f"{prefix}{k}:")
                            lines_yaml.append(dict_to_yaml(v, indent + 2))
                        elif isinstance(v, list):
                            lines_yaml.append(f"{prefix}{k}:")
                            for item in v:
                                lines_yaml.append(f"{prefix}- {item}")
                        else:
                            if v is None:
                                lines_yaml.append(f"{prefix}{k}: null")
                            elif isinstance(v, bool):
                                lines_yaml.append(f"{prefix}{k}: {str(v).lower()}")
                            else:
                                lines_yaml.append(f"{prefix}{k}: {v}")
                    return "\n".join(lines_yaml)

                fitness_data = eng.calculate_fitness(project)
                report = {}
                for cid, fit in fitness_data.items():
                    report[cid] = {
                        "retrieved": fit.retrieved,
                        "referenced": fit.referenced,
                        "ignored": fit.ignored,
                        "successful_sessions": fit.successful_sessions,
                        "failed_sessions": fit.failed_sessions,
                        "evidence_count": fit.evidence_count,
                        "fitness_score": fit.fitness_score,
                    }

                if args.concept_id:
                    if args.concept_id not in report:
                        resolved_id = eng.fitness._find_concept_id(args.concept_id, eng.state._load_registry(project))
                        if resolved_id in report:
                            report = {resolved_id: report[resolved_id]}
                        else:
                            print(render_panel("Error", [f"Concept '{args.concept_id}' not found in fitness statistics."], status="error"))
                            sys.exit(1)
                    else:
                        report = {args.concept_id: report[args.concept_id]}

                if args.format == "json":
                    print(json.dumps(report, indent=2))
                elif args.format == "yaml":
                    print(dict_to_yaml(report))
                else:
                    lines = [
                        "Note: Outcome metrics indicate correlation, not direct causation.",
                        "Concepts sorted by active usage count (referenced sessions).",
                        "",
                        f"{'Concept Name (ID)':<30} | {'Retr':<5} | {'Ref':<5} | {'Ign':<5} | {'Succ':<5} | {'Fail':<5} | {'Evid':<5} | {'Fitness':<7}",
                        "-" * 88
                    ]
                    sorted_concepts = sorted(
                        report.items(),
                        key=lambda x: (x[1]["referenced"], x[1]["retrieved"]),
                        reverse=True
                    )
                    registry = eng.state._load_registry(project)
                    for cid, m in sorted_concepts:
                        name = registry.get(cid, {}).get("canonical_name", cid)
                        label = f"{name} ({cid})"
                        if len(label) > 30:
                            label = label[:27] + "..."
                        lines.append(
                            f"{label:<30} | {m['retrieved']:<5} | {m['referenced']:<5} | {m['ignored']:<5} | {m['successful_sessions']:<5} | {m['failed_sessions']:<5} | {m['evidence_count']:<5} | {m['fitness_score']:.4f}"
                        )
                    print(render_panel("Knowledge Fitness Telemetry", lines, status="stats"))

        elif args.command == "contradictions":
            from oem_knowledge.evolution import ContradictionDetector
            detector = ContradictionDetector(eng)
            contradictions = detector.detect_contradictions(project)
            lines = [f"Contradictions detected: {len(contradictions)}", ""]
            for c in contradictions:
                lines.append(f"  \u274c Conflict between {c['concept_a']} and {c['concept_b']}")
                lines.append(f"     Names: {c['name_a']} | {c['name_b']}")
                lines.append(f"     Description: {c['description']}")
                lines.append("")
            print(render_panel("Contradiction Scan", lines, status="error" if contradictions else "ok"))

        elif args.command == "health":
            stale = eng.state.detect_stale_concepts(args.stale_sessions, project)
            merges = eng.propose_merges(args.similarity_threshold, project)
            conflicts = eng.detect_contradictions(project)
            
            lines = []
            
            # Stale concepts section
            lines.append("Stale Concepts:")
            if stale:
                for s in stale:
                    lines.append(f"  ○ {s['canonical_name']} ({s['concept_id']}) - untouched for {s['sessions_since_reference']} sessions")
            else:
                lines.append("  None")
            lines.append("")
            
            # Merge proposals section
            lines.append("Duplicate Merge Proposals:")
            if merges:
                for m in merges:
                    lines.append(f"  ✦ Suggest merging {m['secondary_name']} ({m['secondary_id']}) into {m['primary_name']} ({m['primary_id']})")
                    lines.append(f"    Reason: {m['reason']}")
            else:
                lines.append("  None")
            lines.append("")
            
            # Contradictions section
            lines.append("Contradictions Detected:")
            if conflicts:
                for c in conflicts:
                    lines.append(f"  ✗ Conflict between {c['name_a']} ({c['concept_a']}) and {c['name_b']} ({c['concept_b']})")
                    lines.append(f"    Description: {c['description']}")
            else:
                lines.append("  None")
                
            print(render_panel("Knowledge Health Scan", lines, status="stats"))

        elif args.command == "merge":
            res = eng.state.merge_concepts(project, args.primary_id, args.secondary_id)
            if res.get("status") == "error":
                print(render_panel("Merge Failure", [res.get("message", "")], status="error"))
            else:
                print(render_panel("Concepts Merged", [res.get("message", "")], status="ok"))

        elif args.command == "session-status":
            harness = eng._resolve_harness(project)
            active_session_file = harness / "state" / "active_session.json"
            session_state = SessionState.load(active_session_file)

            if not session_state:
                print(render_panel("Session Status", ["No active session found."], status="info"))
            else:
                import datetime
                started_str = datetime.datetime.fromtimestamp(session_state.started_at).isoformat() if session_state.started_at else "unknown"

                # Check context injection
                context_exists = False
                if session_state.context_path:
                    context_exists = Path(session_state.context_path).exists()

                # Read knowledge injection count from session_state.json
                session_state_file = harness / "state" / "session_state.json"
                injected_count = 0
                if session_state_file.exists():
                    try:
                        sdata = json.loads(session_state_file.read_text(encoding="utf-8"))
                        injected_count = len(sdata.get("last_injected_concepts", []))
                    except Exception:
                        pass

                # Determine reflection/materialization/outcome status
                is_running = session_state.status in ("started", "running")
                reflection_status = "Pending" if is_running else "Complete"
                materialization_status = "Pending" if is_running else "Complete"
                outcome_status = "Not Recorded" if is_running else "Recorded"

                lines = [
                    f"Session ID:      {session_state.session_id}",
                    f"State:           {session_state.status}",
                    f"Agent:           {session_state.agent}",
                    f"Started At:      {started_str}",
                    f"Project:         {session_state.project}",
                    "",
                    f"Context Injection: {'✓' if context_exists else '✗'}",
                    f"Knowledge Retrieved: {injected_count}",
                    f"Reflection:      {reflection_status}",
                    f"Materialization: {materialization_status}",
                    f"Outcome:         {outcome_status}",
                ]
                print(render_panel("Session Status", lines, status="stats"))

        elif args.command == "run":
            run_agent(args.agent, eng, project)

        elif args.command == "recover":
            cmd_recover(eng, project, abort=args.abort, status=args.status)

        elif args.command == "setup":
            if args.setup_target == "opencode":
                cmd_setup_opencode(repair=args.repair)

        elif args.command == "mcp":
            from oem_knowledge.server import main as run_server
            run_server()


        elif args.command == "todo":
            from oem_knowledge.tools.todos import oem_todo_read, oem_todo_write, oem_todo_advance
            if args.todo_action == "read":
                print(oem_todo_read(project or ""))
            elif args.todo_action == "write":
                print(oem_todo_write(args.items, project or ""))
            elif args.todo_action == "advance":
                print(oem_todo_advance(args.item_id, args.status, project or ""))

        elif args.command == "outcome":
            referenced = args.referenced_concepts if args.referenced_concepts else None
            reason = args.reason if args.reason else None
            session_id = args.session_id if args.session_id else None
            res = eng.record_outcome(
                args.status,
                referenced_concepts=referenced,
                reason=reason,
                session_id=session_id,
                project=project,
                goal_satisfaction=args.goal_satisfaction,
            )
            lines = [
                f"Session ID:   {res['session_id']}",
                f"Outcome:      {res['outcome'].upper()}",
                f"Satisfaction: {res['goal_satisfaction']:.2f}",
                f"Concepts:     {', '.join(res['referenced_concepts']) if res['referenced_concepts'] else 'None'}",
            ]
            if res["reason"]:
                lines.append(f"Reason:      {res['reason']}")
            lines.append("")
            lines.append(f"Metrics (Injected/Referenced): {res['metrics']['concepts_injected']}/{res['metrics']['concepts_referenced']}")
            print(render_panel("Outcome Logged", lines, status="ok"))

        elif args.command == "runtime-summary":
            harness = eng._resolve_harness(project)
            metrics_file = harness / "state" / "metrics.json"
            outcomes_file = harness / "state" / "outcomes.jsonl"

            metrics_data = {}
            if metrics_file.exists():
                try:
                    metrics_data = json.loads(metrics_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            runtime = metrics_data.get("runtime", {})
            outcomes = []
            if outcomes_file.exists():
                try:
                    now = time.time()
                    cutoff = now - (args.days * 86400)
                    for line in outcomes_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            ts = entry.get("timestamp", "")
                            if ts:
                                import datetime
                                entry_time = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").timestamp()
                                if entry_time >= cutoff:
                                    outcomes.append(entry)
                        except Exception:
                            pass
                except Exception:
                    pass

            sessions_started = runtime.get("sessions_started", 0)
            sessions_completed = runtime.get("sessions_completed", 0)
            sessions_failed = runtime.get("sessions_failed", 0)
            sessions_recovered = runtime.get("sessions_recovered", 0)

            outcome_success = sum(1 for o in outcomes if o.get("outcome") == "success")
            outcome_failure = sum(1 for o in outcomes if o.get("outcome") == "failure")
            outcome_abandoned = sum(1 for o in outcomes if o.get("outcome") == "abandoned")

            concepts_generated = (
                metrics_data.get("reflection", {}).get("structured_events", 0)
                + metrics_data.get("reflection", {}).get("fallback_extractions", 0)
            )
            search_queries = metrics_data.get("retrieval", {}).get("search_count", 0)
            reflections = runtime.get("reflections", 0)
            materializations = runtime.get("materializations", 0)

            lines = [
                f"Period: Last {args.days} day(s)",
                "",
                "Sessions (aggregate metrics):",
                f"  Started:     {sessions_started}",
                f"  Completed:   {sessions_completed}",
                f"  Failed:      {sessions_failed}",
                f"  Recovered:   {sessions_recovered}",
                "",
                f"Outcomes (last {args.days}d):",
                f"  Successful:  {outcome_success}",
                f"  Failed:      {outcome_failure}",
                f"  Abandoned:   {outcome_abandoned}",
                f"  Total:       {len(outcomes)}",
                "",
                "Pipeline Activity:",
                f"  Concepts Generated: {concepts_generated}",
                f"  Search Queries:     {search_queries}",
                f"  Reflections:        {reflections}",
                f"  Materializations:   {materializations}",
            ]
            print(render_panel("Runtime Summary", lines, status="stats"))

        elif args.command == "migrate":
            p = Path(project or ".").resolve()
            migrate_harness_to_oem(p)
            print(render_panel("Migration Complete", [f"Legacy .harness checked and migrated for: {p}"], status="ok"))

        elif args.command == "config":
            if args.config_target == "retrieval":
                if args.mode:
                    eng.search.set_retrieval_mode(args.mode)
                    print(render_panel("Config Updated", [f"Retrieval mode set to: '{args.mode}'"], status="ok"))
                else:
                    current_mode = eng.search.get_retrieval_mode()
                    print(render_panel("Retrieval Config", [f"Current retrieval mode: '{current_mode}'"], status="info"))

        elif args.command == "metrics":
            if getattr(args, "report", False):
                from oem_knowledge.tools.metrics import report_usage
                try:
                    used = json.loads(args.used)
                    ignored = json.loads(args.ignored) if args.ignored else None
                    decisions = json.loads(args.decisions) if args.decisions else None
                except Exception as e:
                    print(render_panel("Report Error", [f"Invalid arguments: {e}"], status="error"))
                    sys.exit(1)
                
                print(report_usage(used, ignored, decisions, project))
                return

            metrics_file = eng._resolve_harness(project) / "state" / "metrics.json"
            if args.usage_log is not None:
                try:
                    resolved_dir = eng._resolve_harness(project)
                    log_file = resolved_dir / "state" / "usage_log.jsonl"
                except Exception:
                    log_file = Path(project or ".") / ".oem" / "state" / "usage_log.jsonl"

                if not log_file.exists():
                    print(render_panel("Usage Log", ["No usage log records found yet."], status="info"))
                else:
                    try:
                        lines = log_file.read_text(encoding="utf-8").splitlines()
                        limit = args.usage_log
                        recent = lines[-limit:] if limit > 0 else []
                        log_lines = []
                        for r in recent:
                            try:
                                entry = json.loads(r)
                                ts = entry.get("timestamp", "N/A")
                                used = entry.get("concepts_used", [])
                                ignored = entry.get("concepts_ignored", [])
                                decs = entry.get("decisions", [])
                                log_lines.append(f"[{ts}]")
                                log_lines.append(f"  Used:    {', '.join(used) if used else 'None'}")
                                log_lines.append(f"  Ignored: {', '.join(ignored) if ignored else 'None'}")
                                if decs:
                                    log_lines.append(f"  Decisions: {'; '.join(decs)}")
                            except Exception:
                                pass
                        if not log_lines:
                            log_lines = ["No valid entries found."]
                        print(render_panel("Recent Usage Log", log_lines, status="info"))
                    except Exception as e:
                        print(render_panel("Log Error", [f"Failed to read usage log: {e}"], status="error"))
            elif args.reset:
                if metrics_file.exists():
                    try:
                        metrics_file.unlink()
                    except Exception:
                        pass
                try:
                    resolved_dir = eng._resolve_harness(project)
                    log_file = resolved_dir / "state" / "usage_log.jsonl"
                    if log_file.exists():
                        log_file.unlink()
                except Exception:
                    pass
                try:
                    resolved_dir = eng._resolve_harness(project)
                    session_state_file = resolved_dir / "state" / "session_state.json"
                    if session_state_file.exists():
                        session_state_file.unlink()
                except Exception:
                    pass

                empty_metrics = {
                    "retrieval": {
                        "search_count": 0,
                        "search_latency_total": 0.0,
                        "search_latency_min": None,
                        "search_latency_max": None,
                        "last_search_latency": None,
                        "last_search_at": None,
                        "cache_hits": 0,
                        "cache_misses": 0,
                        "concepts_retrieved": 0
                    },
                    "context": {
                        "context_count": 0,
                        "context_latency_total": 0.0,
                        "context_latency_min": None,
                        "context_latency_max": None,
                        "last_context_latency": None,
                        "last_context_at": None
                    },
                    "knowledge_usage": {
                        "concepts_injected": 0,
                        "concepts_referenced": 0,
                        "concepts_ignored": 0,
                        "agent_decisions_aligned": 0,
                        "last_report_at": None
                    }
                }
                try:
                    metrics_file.parent.mkdir(parents=True, exist_ok=True)
                    metrics_file.write_text(json.dumps(empty_metrics, indent=2), encoding="utf-8")
                except Exception as e:
                    print(render_panel("Reset Error", [f"Failed to reset metrics: {e}"], status="error"))
                    sys.exit(1)
                print(render_panel("Metrics Reset", ["All retrieval and context metrics have been reset to zero."], status="ok"))
            elif args.export:
                if not metrics_file.exists():
                    print(render_panel("Export Error", ["No metrics found to export."], status="error"))
                    sys.exit(1)
                try:
                    dest = Path(args.export)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(metrics_file, dest)
                    print(render_panel("Metrics Exported", [f"Raw metrics exported successfully to: {dest}"], status="ok"))
                except Exception as e:
                    print(render_panel("Export Error", [f"Failed to export metrics: {e}"], status="error"))
                    sys.exit(1)
            else:
                if not metrics_file.exists():
                    print(render_panel("Retrieval Metrics", ["No metrics recorded yet."], status="info"))
                else:
                    try:
                        data = json.loads(metrics_file.read_text(encoding="utf-8"))
                        retrieval = data.get("retrieval", {})
                        context = data.get("context", {})
                        usage = data.get("knowledge_usage", {})

                        search_count = retrieval.get("search_count", 0)
                        search_total = retrieval.get("search_latency_total", 0.0)
                        search_min = retrieval.get("search_latency_min")
                        search_max = retrieval.get("search_latency_max")
                        search_last = retrieval.get("last_search_latency")
                        search_last_at = retrieval.get("last_search_at")
                        search_avg = (search_total / search_count) if search_count > 0 else 0.0
                        concepts_retrieved = retrieval.get("concepts_retrieved", 0)

                        hits = retrieval.get("cache_hits", 0)
                        misses = retrieval.get("cache_misses", 0)
                        total_lookups = hits + misses
                        hit_rate = (hits / total_lookups * 100) if total_lookups > 0 else 0.0

                        context_count = context.get("context_count", 0)
                        context_total = context.get("context_latency_total", 0.0)
                        context_min = context.get("context_latency_min")
                        context_max = context.get("context_latency_max")
                        context_last = context.get("last_context_latency")
                        context_last_at = context.get("last_context_at")
                        context_avg = (context_total / context_count) if context_count > 0 else 0.0

                        lines = [
                            "Retrieval Search Metrics:",
                            f"  Total Searches:     {search_count}",
                            f"  Concepts Retrieved: {concepts_retrieved}",
                            f"  Avg Latency:        {search_avg:.2f} ms",
                            f"  Min/Max Latency:    {f'{search_min:.2f}/{search_max:.2f}' if search_min is not None else 'N/A'} ms",
                            f"  Last Latency:       {f'{search_last:.2f}' if search_last is not None else 'N/A'} ms",
                            f"  Last Run:           {search_last_at or 'N/A'}",
                            "",
                            "Cache (RegistryCache) Metrics:",
                            f"  Cache Hits:         {hits}",
                            f"  Cache Misses:       {misses}",
                            f"  Cache Hit Rate:     {hit_rate:.1f}%",
                            "",
                            "Context Metrics:",
                            f"  Context Loads:      {context_count}",
                            f"  Avg Latency:        {context_avg:.2f} ms",
                            f"  Min/Max Latency:    {f'{context_min:.2f}/{context_max:.2f}' if context_min is not None else 'N/A'} ms",
                            f"  Last Latency:       {f'{context_last:.2f}' if context_last is not None else 'N/A'} ms",
                            f"  Last Run:           {context_last_at or 'N/A'}",
                            "",
                            "Knowledge Attribution Metrics (Self-Reported):",
                            f"  Concepts Injected:   {usage.get('concepts_injected', 0)}",
                            f"  Concepts Referenced: {usage.get('concepts_referenced', 0)}",
                            f"  Concepts Ignored:    {usage.get('concepts_ignored', 0)}",
                            f"  Decisions Aligned:   {usage.get('agent_decisions_aligned', 0)}",
                            f"  Last Report At:      {usage.get('last_report_at') or 'N/A'}",
                        ]
                        print(render_panel("Retrieval Metrics", lines, status="info"))
                    except Exception as e:
                        print(render_panel("Metrics Error", [f"Failed to read metrics: {e}"], status="error"))
        elif args.command == "warmup":
            res = eng.warmup()
            print(render_panel("Model Warm-Up", [f"Status: {res['status']}", f"Model: {res['model']}", "", "Embedding model is now cached globally.", "Run `oem doctor` to verify."], status="ok"))

        elif args.command == "doctor":
            spinner = Spinner("Running environment and diagnostics checks...")
            spinner.__enter__()
            try:
                resolved_dir = eng._resolve_harness(project)
                workspace_root = resolved_dir
            except Exception:
                workspace_root = Path(project or ".")

            # Walk up to find workspace root containing pyproject.toml
            while workspace_root.parent != workspace_root:
                if (workspace_root / "pyproject.toml").exists():
                    break
                workspace_root = workspace_root.parent

            pyproject_path = workspace_root / "pyproject.toml"
            root_venv_path = workspace_root / ".venv"
            
            # Detect if this is the OpenEmpiric development workspace
            is_dev_workspace = False
            if pyproject_path.exists():
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if 'name = "oem-mcp"' in content:
                        is_dev_workspace = True
                except Exception:
                    pass

            lines = []
            status = "ok"

            if is_dev_workspace:
                # 1. Root workspace check
                if pyproject_path.exists():
                    lines.append("✓ Root workspace detected")
                else:
                    lines.append("✗ Root workspace pyproject.toml not found")
                    status = "error"

                # 2. Root venv check
                if root_venv_path.exists():
                    lines.append("✓ Root .venv exists")
                else:
                    lines.append("✗ Root .venv not found")
                    status = "error"

                # 3. UV workspace health check
                try:
                    content = pyproject_path.read_text(encoding="utf-8")
                    if "[tool.uv.workspace]" in content:
                        lines.append("✓ UV workspace healthy")
                    else:
                        lines.append("✗ [tool.uv.workspace] missing in root pyproject.toml")
                        status = "error"
                except Exception as e:
                    lines.append(f"✗ Failed to read root pyproject.toml: {e}")
                    status = "error"

                # 4. Nested virtualenvs scan
                nested_venvs = []
                packages_dir = workspace_root / "packages"
                if packages_dir.exists() and packages_dir.is_dir():
                    for p in packages_dir.iterdir():
                        if p.is_dir():
                            sub_venv = p / ".venv"
                            if sub_venv.exists():
                                nested_venvs.append(str(sub_venv.relative_to(workspace_root)))

                if nested_venvs:
                    status = "error"
                    for nv in nested_venvs:
                        lines.append(f"✗ Nested virtualenv detected: {nv}")
                    lines.append("")
                    lines.append("Suggested Fix:")
                    lines.append(f"  rm -rf {Path(packages_dir.relative_to(workspace_root)) / '*/.venv'}")
                    lines.append("  uv sync")
                else:
                    lines.append("✓ No nested virtualenvs detected")
            else:
                lines.append("✓ Running as globally installed user tool")
                lines.append(f"✓ Project directory: {workspace_root.resolve()}")
                if shutil.which("oem"):
                    lines.append("✓ OEM executable available")
                else:
                    lines.append("⚠ OEM executable not found in PATH — install via `uv tool install oem-knowledge`")
                try:
                    import oem_knowledge  # noqa: F401
                    lines.append("✓ Package importable")
                except ImportError:
                    lines.append("✗ Package not importable")
                    status = "error"
                lines.append("⚠ Development workspace not detected")

            # OpenCode Workstation Integration Checks
            opencode_dir = Path.home() / ".config" / "opencode"
            plugin_dest = opencode_dir / "plugins" / "openempiric.ts"
            inst_dest = opencode_dir / "instructions" / "memory-start.md"
            jsonc_file = opencode_dir / "opencode.jsonc"

            # Check plugin
            if not plugin_dest.exists():
                lines.append("⚠ OpenCode Plugin not installed (missing plugins/openempiric.ts) — run 'oem setup opencode'")
            else:
                try:
                    p_content = plugin_dest.read_text(encoding="utf-8")
                    if "knowledge_session_start" in p_content or "verify plugin array" in p_content or "session lifecycle is automatic" in p_content:
                        lines.append("⚠ OpenCode Plugin is legacy/outdated — run 'oem setup opencode --repair'")
                    else:
                        lines.append("✓ OpenCode Plugin installed")
                except Exception as e:
                    lines.append(f"⚠ Failed to read openempiric.ts plugin: {e}")

            # Check instructions
            if not inst_dest.exists():
                lines.append("⚠ OpenCode Instructions not installed (missing instructions/memory-start.md) — run 'oem setup opencode'")
            else:
                try:
                    i_content = inst_dest.read_text(encoding="utf-8")
                    if "knowledge_session_start" in i_content or "knowledge_session_commit" in i_content or "verify plugin array" in i_content:
                        lines.append("⚠ OpenCode Instructions are legacy/outdated — run 'oem setup opencode --repair'")
                    else:
                        lines.append("✓ OpenCode Instructions installed")
                except Exception as e:
                    lines.append(f"⚠ Failed to read memory-start.md instructions: {e}")

            # Check config
            mcp_registered = False
            mcp_cmd = []
            if not jsonc_file.exists():
                lines.append("⚠ OpenCode Config missing (missing opencode.jsonc) — run 'oem setup opencode'")
            else:
                try:
                    text = jsonc_file.read_text(encoding="utf-8")
                    cleaned = _strip_jsonc_comments(text)
                    config_data = json.loads(cleaned, strict=False)
                    inst_path_str = str(inst_dest.resolve())
                    inst_list = config_data.get("instructions", [])
                    if inst_path_str not in inst_list:
                        lines.append("⚠ OpenCode Config does not register memory-start.md instruction — run 'oem setup opencode'")
                    else:
                        lines.append("✓ OpenCode Config verified")

                    mcp_config = config_data.get("mcp", {}).get("openempiric")
                    if mcp_config:
                        mcp_registered = True
                        cmd = mcp_config.get("command")
                        args = mcp_config.get("args", [])
                        if isinstance(cmd, str):
                            mcp_cmd = [cmd] + args
                        elif isinstance(cmd, list):
                            mcp_cmd = cmd + args
                        lines.append("✓ OEM MCP Server registered in OpenCode config")
                    else:
                        lines.append("✗ OEM MCP Server not registered in OpenCode config — run 'oem setup opencode'")
                        status = "error"
                except Exception as e:
                    lines.append(f"⚠ OpenCode Config validation failed: {e} — run 'oem setup opencode'")
                    status = "error"

            # Check MCP Server Reachability and Functionality
            if mcp_registered and mcp_cmd:
                reachable, functional, num_tools, err = check_mcp_server(mcp_cmd)
                if reachable:
                    lines.append("✓ OEM MCP Server reachable")
                    if functional:
                        lines.append("✓ OEM MCP Server functional (stats call succeeded)")
                    else:
                        lines.append(f"✗ OEM MCP Server functional check failed: {err}")
                        status = "error"
                    lines.append(f"✓ {num_tools} tools available")
                else:
                    lines.append(f"✗ OEM MCP Server unreachable: {err}")
                    status = "error"

            # 5. Events log schema version check
            try:
                schema_status = eng.event_migrator.get_schema_status(project)
                if schema_status["status"] == "up_to_date":
                    lines.append(f"✓ Events schema up to date ({schema_status['message']})")
                elif schema_status["status"] == "outdated":
                    lines.append(f"✗ Events schema outdated: {schema_status['message']}")
                    status = "error"
                else:
                    lines.append(f"✗ Events schema check: {schema_status.get('message')}")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Events schema check failed: {e}")
                status = "error"

            # 6. Skill installation check & adapter detection
            adapter_name = "opencode"
            try:
                h_dir = eng._resolve_harness(project)
                skills_file = h_dir / "skills" / "openempiric.yaml"
                if skills_file.exists():
                    lines.append("✓ OEM Skill Installed")
                    try:
                        import yaml
                        with open(skills_file, "r", encoding="utf-8") as f:
                            data = yaml.safe_load(f)
                            if data and "adapter" in data:
                                adapter_name = data["adapter"]
                    except Exception:
                        pass
                else:
                    lines.append("✗ OEM Skill not installed (missing skills/openempiric.yaml)")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to verify OEM Skill installation: {e}")
                status = "error"

            # 7. MCP Registered check (adapter-aware)
            try:
                from oem_knowledge.adapters import get_adapter
                adapter = get_adapter(adapter_name, eng, project)
                if adapter.verify_mcp():
                    lines.append("✓ MCP Registered")
                else:
                    lines.append(f"✗ MCP not registered (for adapter: {adapter_name})")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to verify MCP registration: {e}")
                status = "error"

            # 8. Embedding Cache Ready check
            try:
                if eng.embedding_cache_ready():
                    lines.append("✓ Embedding Cache Ready")
                else:
                    lines.append("✗ Embedding Cache not ready")
                    lines.append("  → Run `oem warmup` once per machine to pre-download")
                    status = "error"
            except Exception as e:
                lines.append(f"✗ Failed to check Embedding Cache: {e}")
                status = "error"

            # 9. Context Injection Working check
            try:
                _ = _compile_oem_context(eng)
                if adapter_name == "opencode":
                    context_dir = _OEM_RUNTIME_CONTEXT_PATH.parent
                elif adapter_name in ("agy", "antigravity"):
                    from oem_knowledge.adapters import get_adapter
                    adapter = get_adapter(adapter_name, eng, project)
                    context_dir = adapter.get_app_data_dir()
                else:
                    context_dir = Path.home() / ".config" / "opencode" / "plugins"
                
                context_dir.mkdir(parents=True, exist_ok=True)
                test_file = context_dir / ".oem_doctor_write_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
                lines.append("✓ Context Injection Working")
            except Exception as e:
                lines.append(f"✗ Context Injection not working: {e}")
                status = "error"

            # 10. Managed Runtime Available check
            try:
                bin_name = "opencode"
                if adapter_name == "opencode":

                    bin_name = "opencode"
                elif adapter_name in ("agy", "antigravity"):
                    bin_name = "agy"
                else:
                    bin_name = adapter_name
                
                if shutil.which(bin_name):
                    lines.append("✓ Managed Runtime Available")
                else:
                    lines.append(f"⚠ Managed Runtime not available (executable '{bin_name}' not found in PATH)")
            except Exception as e:
                lines.append(f"✗ Failed to check Managed Runtime: {e}")
                status = "error"

            # 11. Search Pipeline Available check
            try:
                _ = eng.search.stats()
                eng.search.search("test", k=1)
                lines.append("✓ Search Pipeline Available")
            except Exception as e:
                lines.append(f"✗ Search Pipeline not available: {e}")
                status = "error"

            # --- Runtime Health Checks ---
            runtime_lines = []

            # 12. Session Recovery Ready
            try:
                active_file = resolved_dir / "state" / "active_session.json"
                _ = SessionState.load(active_file)
                runtime_lines.append("✓ Session Recovery Ready")
            except Exception as e:
                runtime_lines.append(f"✗ Session Recovery not ready: {e}")

            # 13. Reflection Pipeline Ready
            try:
                rs = eng.reflection
                res = rs.reflect_session(project, conversation_text="")
                if res.get("status") == "success":
                    runtime_lines.append("✓ Reflection Pipeline Ready")
                else:
                    runtime_lines.append("✗ Reflection Pipeline not ready")
            except Exception as e:
                runtime_lines.append(f"✗ Reflection Pipeline not ready: {e}")

            # 14. Materialization Pipeline Ready
            try:
                mat_res = eng.materialization.materialize_concepts(project)
                if mat_res.get("status") == "success":
                    runtime_lines.append("✓ Materialization Pipeline Ready")
                else:
                    runtime_lines.append("✗ Materialization Pipeline not ready")
            except Exception as e:
                runtime_lines.append(f"✗ Materialization Pipeline not ready: {e}")

            # 15. Outcome Tracking Ready
            try:
                outcomes_file = resolved_dir / "state" / "outcomes.jsonl"
                outcomes_file.parent.mkdir(parents=True, exist_ok=True)
                from oem_knowledge.services.state import StateService
                _ = StateService
                runtime_lines.append("✓ Outcome Tracking Ready")
            except Exception as e:
                runtime_lines.append(f"✗ Outcome Tracking not ready: {e}")

            spinner.__exit__(None, None, None)
            print(render_panel("OEM Environment Check", lines, status=status))


            if any("✗" in l for l in runtime_lines):
                print(render_panel("Runtime Health", runtime_lines, status="error"))
            else:
                print(render_panel("Runtime Health", runtime_lines, status="ok"))

            # --- Knowledge Health Dashboard ---
            try:
                fitness_data = eng.calculate_fitness(project)
                registry = eng.state._load_registry(project)

                tested = []
                untested = []

                for cid, fit in fitness_data.items():
                    conf = registry.get(cid, {}).get("confidence", 1)
                    entry = {
                        "id": cid,
                        "name": fit.canonical_name.replace("-", " ").title(),
                        "fitness": fit.fitness_score,
                        "evidence": fit.evidence_count,
                        "referenced": fit.referenced,
                        "successful": fit.successful_sessions,
                        "failed": fit.failed_sessions,
                        "confidence": conf,
                    }
                    if fit.referenced > 0:
                        # Composite score: fitness weighted by log of evidence
                        # Concepts with more evidence are more reliable signals
                        import math
                        entry["composite"] = fit.fitness_score * (1.0 + 0.3 * math.log1p(fit.evidence_count))
                        tested.append(entry)
                    else:
                        untested.append(entry)

                tested_by_composite = sorted(tested, key=lambda x: x["composite"], reverse=True)
                top = tested_by_composite[:5]
                bottom = [x for x in tested_by_composite[::-1] if x["fitness"] < 1.0][:5]

                dash_lines = [
                    "⚠  Scores are outcome correlations, not causation.",
                    "   Ranked by: Fitness × Evidence (composite score).",
                    "",
                ]

                if not tested:
                    dash_lines.append("No session outcome data yet.")
                    dash_lines.append("Run sessions and record outcomes with:")
                    dash_lines.append("  oem outcome success")
                    dash_lines.append("  oem outcome failure")
                else:
                    dash_lines.append("Top Concepts:")
                    for c in top:
                        sessions = c["successful"] + c["failed"]
                        label = c["name"][:28]
                        dash_lines.append(
                            f"  ✦ {label:<28}  Fitness: {c['fitness'] * 100:.0f}%"
                            f"  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5"
                            f"  ({c['successful']}/{sessions} sessions)"
                        )

                    if bottom and bottom != top[:len(bottom)]:
                        dash_lines.append("")
                        dash_lines.append("Underperforming Concepts:")
                        for c in bottom:
                            sessions = c["successful"] + c["failed"]
                            label = c["name"][:28]
                            dash_lines.append(
                                f"  ✗ {label:<28}  Fitness: {c['fitness'] * 100:.0f}%"
                                f"  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5"
                                f"  ({c['successful']}/{sessions} sessions)"
                            )

                if untested:
                    dash_lines.append("")
                    dash_lines.append(f"Untested Concepts ({len(untested)} total — no session outcomes):")
                    for c in untested[:5]:
                        dash_lines.append(f"  ○ {c['name'][:28]:<28}  Evidence: {c['evidence']}  Confidence: {c['confidence']}/5")
                    if len(untested) > 5:
                        dash_lines.append(f"  … and {len(untested) - 5} more")

                print(render_panel("Knowledge Health Dashboard", dash_lines, status="stats"))

            except Exception as e:
                print(render_panel("Knowledge Health Dashboard", [f"Could not compute: {e}"], status="error"))

            if status == "error":
                sys.exit(1)

    except Exception as e:
        logging.exception("Unhandled error in command '%s'", args.command)
        print(render_panel("Error", [str(e)], status="error"))
        sys.exit(1)


if __name__ == "__main__":
    main()
