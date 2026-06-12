from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
import importlib.resources as pkg_resources

from oem_knowledge.ui import render_panel
from ..helpers import check_mcp_server, Spinner, _strip_jsonc_comments, _update_jsonc_mcp


def cmd_setup_opencode(repair: bool = False) -> None:
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
                local_src = Path(__file__).resolve().parent.parent.parent / "plugins" / "openempiric.ts"
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
        
    mcp_verified = False
    mcp_error = ""
    if plugin_installed and inst_installed and config_verified:
        # Check MCP server status using mcp_config
        cmd = mcp_config.get("command")
        args_list = mcp_config.get("args", [])
        if isinstance(cmd, str):
            mcp_cmd = [cmd] + args_list
        elif isinstance(cmd, list):
            mcp_cmd = cmd + args_list
        else:
            mcp_cmd = []

        if mcp_cmd:
            with Spinner("Verifying registered OEM MCP server..."):
                reachable, functional, num_tools, err = check_mcp_server(mcp_cmd)
                if reachable and functional:
                    mcp_verified = True
                else:
                    mcp_error = err
        else:
            mcp_error = "Could not resolve MCP server configuration command"

    # Report summary
    lines = []
    lines.append("✓ Plugin installed" if plugin_installed else "✗ Plugin installation failed")
    lines.append("✓ Instructions installed" if inst_installed else "✗ Instructions installation failed")
    lines.append("✓ Configuration verified" if config_verified else "✗ Configuration verification failed")
    
    if plugin_installed and inst_installed and config_verified:
        if mcp_verified:
            lines.append("✓ MCP Server reachable and functional")
        else:
            lines.append(f"✗ MCP Server verification failed: {mcp_error}")
            lines.append("  → Verify that 'uv' or 'oem' is in your PATH.")
            lines.append("  → Run 'oem doctor' to troubleshoot environment problems.")
    
    if migrated_plugin:
        lines.append("ℹ Migrated legacy plugin openempiric.ts")
    if migrated_inst:
        lines.append("ℹ Migrated legacy instructions memory-start.md")
    if repair:
        lines.append("ℹ Re-installed all components (--repair)")
        
    if plugin_installed and inst_installed and config_verified and mcp_verified:
        lines.append("")
        lines.append("OpenCode integration ready.")
        print(render_panel("OEM OpenCode Setup", lines, status="ok"))
    else:
        print(render_panel("OEM OpenCode Setup Failed", lines, status="error"))
        sys.exit(1)


def cmd_setup_codex_app(eng, project: str | None = None, repair: bool = False) -> None:
    print("OEM Codex App Setup\n")

    try:
        from oem_knowledge.adapters.codex_app.adapter import CodexAppAdapter

        adapter = CodexAppAdapter(eng, project)
        res = adapter.setup(repair=repair)
    except Exception as e:
        print(render_panel("OEM Codex App Setup Failed", [f"Setup failed: {e}"], status="error"))
        sys.exit(1)

    mcp = res["mcp_config"]
    lines = [
        f"Codex Home: {res['codex_home']}",
        f"Skill:      {res['skill_path']}",
        f"Config:     {res['config_path']}",
        "",
        "MCP Bridge:",
        f"  Command: {mcp['command']}",
        f"  Args:    {' '.join(mcp['args'])}",
        "",
        "Run `oem doctor` to perform a full bridge health check.",
    ]
    if repair:
        lines.append("Re-installed all components (--repair)")

    print(render_panel("OEM Codex App Setup", lines, status="ok"))


def run_system_command(args):
    # Setup deferred logging Configuration
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    
    project = getattr(args, "project", None)
    if project == ".":
        project = None

    # Lazy-load KnowledgeEngine to avoid Pydantic imports on help/setup
    from oem_knowledge.engine import KnowledgeEngine
    eng = KnowledgeEngine(project)
    import atexit; atexit.register(eng.close)

    if args.command == "setup":
        if args.setup_target == "opencode":
            cmd_setup_opencode(repair=args.repair)
        elif args.setup_target == "codex-app":
            cmd_setup_codex_app(eng, project=project, repair=args.repair)

    elif args.command == "warmup":
        res = eng.warmup()
        if res.get("status") == "error":
            print(
                render_panel(
                    "Model Warm-Up Failed",
                    [
                        f"Status: {res.get('status')}",
                        f"Error: {res.get('message')}",
                        "",
                        "Please resolve the issue and try again.",
                    ],
                    status="error",
                )
            )
            sys.exit(1)
        print(render_panel("Model Warm-Up", [f"Status: {res['status']}", f"Model: {res['model']}", "", "Embedding model is now cached globally.", "Run `oem doctor` to verify."], status="ok"))

    elif args.command == "doctor":
        if getattr(args, "fix", False):
            from oem_knowledge.runtime.recovery import cmd_recover
            res = cmd_recover(eng, project, scope="reflection", apply=True, backup=True, rebuild_reports=False)
            print("Doctor fix applied safe reflection recovery.")
            if res and res.get("repairs"):
                print("Repairs applied:")
                for r in res["repairs"]:
                    if r:
                        print(f"  - {r}")
            if res and res.get("backup_dir"):
                p = Path(res["backup_dir"])
                try:
                    rel = p.relative_to(eng._resolve_harness(project).parent)
                except Exception:
                    rel = p
                print(f"Backup: {rel}")
            if res and res.get("report_path"):
                p = Path(res["report_path"])
                try:
                    rel = p.relative_to(eng._resolve_harness(project).parent)
                except Exception:
                    rel = p
                print(f"Report: {rel}")
            print("Skipped destructive repairs: session report rebuild")
            print()

        spinner = Spinner("Running environment and diagnostics checks...")
        spinner.__enter__()
        try:
            from oem_knowledge.health import build_runtime_health
            res = build_runtime_health(project)
        finally:
            spinner.__exit__(None, None, None)

        status_map = {"success": "ok", "warn": "warn", "error": "error"}

        # 1. Environment Check Panel
        env_lines = []
        for c in res["environment"]["checks"]:
            symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
            env_lines.append(f"{symbol} {c['name']}")
        print(render_panel("OEM Environment Check", env_lines, status=status_map.get(res["environment"]["status"], "ok")))

        # 2. OpenCode Panel
        if res["opencode"]["active"]:
            opencode_lines = []
            for c in res["opencode"]["checks"]:
                symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
                opencode_lines.append(f"{symbol} {c['name']}")
            print(render_panel("OpenCode Integration", opencode_lines, status=status_map.get(res["opencode"]["status"], "ok")))

        # 3. Codex App Panel
        if res["codex_app"]["active"]:
            codex_lines = []
            for c in res["codex_app"]["checks"]:
                symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
                codex_lines.append(f"{symbol} {c['name']}")
            print(render_panel("Codex App Integration", codex_lines, status=status_map.get(res["codex_app"]["status"], "ok")))

        # 4. Runtime Health Panel
        runtime_lines = []
        for c in res["runtime"]["checks"]:
            symbol = "✓" if c["status"] == "success" else ("⚠" if c["status"] == "warn" else "✗")
            runtime_lines.append(f"{symbol} {c['name']}")
        
        has_runtime_warning = any(c["status"] == "warn" for c in res["runtime"]["checks"])
        if has_runtime_warning:
            runtime_lines.append("")
            runtime_lines.append("Fallback:")
            runtime_lines.append("  Use structured events or Observation:/Decision:/Outcome: markers.")
            runtime_lines.append("  Run: oem recover --scope reflection")
        
        print(render_panel("Runtime Health", runtime_lines, status=status_map.get(res["runtime"]["status"], "ok")))

        if res["environment"]["status"] == "error" or res["runtime"]["status"] == "error" or (res["codex_app"]["active"] and res["codex_app"]["status"] == "error"):
            sys.exit(1)


        # --- Knowledge Health Dashboard ---
        try:
            fitness_data = eng.fitness.calculate_fitness(project)
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

        if res["environment"]["status"] == "error" or res["runtime"]["status"] == "error" or (res["codex_app"]["active"] and res["codex_app"]["status"] == "error"):
            sys.exit(1)

    elif args.command == "migrate":
        from oem_knowledge.engine import migrate_harness_to_oem
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
            print(oem_todo_advance(args.item_id, args.status or "", project or ""))

    elif args.command == "outcome":
        try:
            resolved_dir = eng._resolve_harness(project)
            outcomes_file = resolved_dir / "state" / "outcomes.jsonl"
        except Exception:
            outcomes_file = Path(project or ".") / ".oem" / "state" / "outcomes.jsonl"

        outcomes_file.parent.mkdir(parents=True, exist_ok=True)
        outcome_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": args.status,
            "referenced_concepts": args.referenced_concepts,
            "reason": args.reason,
            "session_id": args.session_id or "",
            "goal_satisfaction": args.goal_satisfaction,
        }
        try:
            with open(outcomes_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(outcome_entry) + "\n")
            lines = [
                f"Status: {args.status.upper()}",
                f"Concepts: {', '.join(args.referenced_concepts) if args.referenced_concepts else 'None'}",
            ]
            if args.reason:
                lines.append(f"Reason: {args.reason}")
            if args.goal_satisfaction is not None:
                lines.append(f"Goal Satisfaction: {args.goal_satisfaction:.2f}")
            print(render_panel("Outcome Recorded", lines, status="ok"))
        except Exception as e:
            print(render_panel("Outcome Error", [f"Failed to record outcome: {e}"], status="error"))
            sys.exit(1)

    elif args.command == "runtime-summary":
        try:
            resolved_dir = eng._resolve_harness(project)
            sessions_dir = resolved_dir / "sessions"
        except Exception:
            sessions_dir = Path(project or ".") / ".oem" / "sessions"

        if not sessions_dir.exists():
            print(render_panel("Runtime Summary", ["No session history found."], status="info"))
        else:
            try:
                days = args.days
                now = time.time()
                cutoff = now - (days * 86400)
                sessions_count = 0
                success_count = 0
                failure_count = 0
                abandoned_count = 0
                concepts_materialized = set()
                total_duration = 0.0

                for f in sessions_dir.glob("*.md"):
                    try:
                        stat = f.stat()
                        if stat.st_mtime >= cutoff:
                            sessions_count += 1
                            content = f.read_text(encoding="utf-8")
                            if "Outcome: success" in content:
                                success_count += 1
                            elif "Outcome: failure" in content:
                                failure_count += 1
                            elif "Outcome: abandoned" in content:
                                abandoned_count += 1

                            for m in re.finditer(r'-\s+([a-zA-Z0-9_\-\.]+)\s+\(new/updated\)', content):
                                concepts_materialized.add(m.group(1))

                            m_dur = re.search(r'Duration:\s+([0-9\.]+)\s*s', content)
                            if m_dur:
                                total_duration += float(m_dur.group(1))
                    except Exception:
                        pass

                # Calculate schema reflections & materializations from outcomes.jsonl
                reflections = 0
                materializations = 0
                try:
                    resolved_dir = eng._resolve_harness(project)
                    log_file = resolved_dir / "state" / "usage_log.jsonl"
                    if log_file.exists():
                        reflections = len(log_file.read_text(encoding="utf-8").splitlines())
                except Exception:
                    pass

                try:
                    resolved_dir = eng._resolve_harness(project)
                    reg_dir = resolved_dir / "wiki"
                    if reg_dir.exists():
                        materializations = len(list(reg_dir.glob("*.md")))
                except Exception:
                    pass

                lines = [
                    f"Period:              Last {days} days",
                    f"Total Agent Runs:   {sessions_count}",
                    f"  Successes:        {success_count}",
                    f"  Failures:         {failure_count}",
                    f"  Abandoned:        {abandoned_count}",
                    f"Total Time Spent:    {total_duration:.1f}s",
                    f"Concepts Modified:   {len(concepts_materialized)}",
                    f"Reflections:        {reflections}",
                    f"Materializations:   {materializations}",
                ]
                print(render_panel("Runtime Summary", lines, status="stats"))
            except Exception as e:
                print(render_panel("Summary Error", [f"Failed to generate summary: {e}"], status="error"))

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
                    m_lines = log_file.read_text(encoding="utf-8").splitlines()
                    limit = args.usage_log
                    recent = m_lines[-limit:] if limit > 0 else []
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

                    lines = [
                        "Retrieval Performance:",
                        f"  Total Searches:      {retrieval.get('search_count', 0)}",
                        f"  Total Latency:        {retrieval.get('search_latency_total', 0.0):.3f}s",
                        f"  Min Latency:          {retrieval.get('search_latency_min') or 0.0:.3f}s",
                        f"  Max Latency:          {retrieval.get('search_latency_max') or 0.0:.3f}s",
                        f"  Last Search:          {retrieval.get('last_search_latency') or 0.0:.3f}s",
                        f"  Cache Hits:           {retrieval.get('cache_hits', 0)}",
                        f"  Cache Misses:         {retrieval.get('cache_misses', 0)}",
                        f"  Concepts Retrieved:   {retrieval.get('concepts_retrieved', 0)}",
                        "",
                        "Context Generation Performance:",
                        f"  Context Generations:  {context.get('context_count', 0)}",
                        f"  Total Latency:        {context.get('context_latency_total', 0.0):.3f}s",
                        f"  Min Latency:          {context.get('context_latency_min') or 0.0:.3f}s",
                        f"  Max Latency:          {context.get('context_latency_max') or 0.0:.3f}s",
                        f"  Last Context:         {context.get('last_context_latency') or 0.0:.3f}s",
                        "",
                        "Knowledge Usage Summary:",
                        f"  Concepts Injected:   {usage.get('concepts_injected', 0)}",
                        f"  Concepts Referenced: {usage.get('concepts_referenced', 0)}",
                        f"  Concepts Ignored:    {usage.get('concepts_ignored', 0)}",
                        f"  Decisions Aligned:   {usage.get('agent_decisions_aligned', 0)}",
                        f"  Last Report At:      {usage.get('last_report_at') or 'N/A'}",
                    ]
                    print(render_panel("Retrieval Metrics", lines, status="info"))
                except Exception as e:
                    print(render_panel("Metrics Error", [f"Failed to read metrics: {e}"], status="error"))
