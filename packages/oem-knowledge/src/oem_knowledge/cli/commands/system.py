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
        enabled_adapters = []
        try:
            h_dir = eng._resolve_harness(project)
            skills_file = h_dir / "skills" / "openempiric.yaml"
            if skills_file.exists():
                try:
                    import yaml
                    with open(skills_file, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        if data:
                            if "adapters" in data:
                                val = data["adapters"]
                                if isinstance(val, list):
                                    enabled_adapters = list(val)
                                else:
                                    enabled_adapters = [val]
                            elif "adapter" in data:
                                enabled_adapters = [data["adapter"]]
                except Exception:
                    pass
                
                adapters_str = ", ".join(enabled_adapters) if enabled_adapters else "none"
                lines.append(f"✓ OEM Skill Installed (enabled adapters: {adapters_str})")
            else:
                lines.append("✗ OEM Skill not installed (missing skills/openempiric.yaml)")
                status = "error"
        except Exception as e:
            lines.append(f"✗ Failed to verify OEM Skill installation: {e}")
            status = "error"

        # Default to opencode if none found
        if not enabled_adapters:
            enabled_adapters = ["opencode"]

        # 7. Embedding Cache Ready check
        try:
            retrieval_mode = eng.search.get_retrieval_mode()
            semantic_installed = eng.search.semantic_dependencies_available()

            if eng.embedding_cache_ready():
                lines.append("✓ Embedding Cache Ready")
            else:
                if retrieval_mode == "hybrid":
                    lines.append("✗ Embedding Cache not ready")
                    lines.append("  → Run `oem warmup` once per machine to pre-download")
                    lines.append("  → Or switch to `oem config retrieval auto` or `oem config retrieval bm25`")
                    status = "error"
                elif semantic_installed:
                    lines.append("⚠ Embedding Cache cold (semantic retrieval will download on first use)")
                    lines.append("  → Run `oem warmup` to pre-download, or let the first managed run warm it")
                else:
                    lines.append("⚠ Semantic retrieval not installed; BM25-only mode active")
                    lines.append("  → Install `[semantic]` for hybrid search, or continue with BM25/auto mode")
        except Exception as e:
            lines.append(f"✗ Failed to check Embedding Cache: {e}")
            status = "error"

        # 8. Managed Runtime Available check
        try:
            if shutil.which("oem"):
                lines.append("✓ Managed Runtime Available")
            else:
                lines.append("⚠ Managed Runtime not available (executable 'oem' not found in PATH)")
        except Exception as e:
            lines.append(f"✗ Failed to check Managed Runtime: {e}")
            status = "error"

        # 9. Search Pipeline Available check
        try:
            _ = eng.search.stats()
            eng.search.search("test", k=1)
            lines.append("✓ Search Pipeline Available")
        except Exception as e:
            lines.append(f"✗ Search Pipeline not available: {e}")
            status = "error"

        # --- Runtime Health Checks ---
        runtime_lines = []

        # 10. Session Recovery Ready
        from oem_knowledge.runtime import SessionState
        try:
            active_file = resolved_dir / "state" / "active_session.json"
            _ = SessionState.load(active_file)
            runtime_lines.append("✓ Session Recovery Ready")
        except Exception as e:
            runtime_lines.append(f"✗ Session Recovery not ready: {e}")

        # 11. Reflection Pipeline Ready
        try:
            rs = eng.reflection
            res = rs.reflect_session(project, conversation_text="")
            if res.get("status") == "success":
                runtime_lines.append("✓ Reflection Pipeline Ready")
            else:
                runtime_lines.append("✗ Reflection Pipeline not ready")
        except Exception as e:
            runtime_lines.append(f"✗ Reflection Pipeline not ready: {e}")

        # 12. Materialization Pipeline Ready
        try:
            mat_res = eng.materialization.materialize_concepts(project)
            if mat_res.get("status") == "success":
                runtime_lines.append("✓ Materialization Pipeline Ready")
            else:
                runtime_lines.append("✗ Materialization Pipeline not ready")
        except Exception as e:
            runtime_lines.append(f"✗ Materialization Pipeline not ready: {e}")

        # 13. Outcome Tracking Ready
        try:
            outcomes_file = resolved_dir / "state" / "outcomes.jsonl"
            outcomes_file.parent.mkdir(parents=True, exist_ok=True)
            runtime_lines.append("✓ Outcome Tracking Ready")
        except Exception as e:
            runtime_lines.append(f"✗ Outcome Tracking not ready: {e}")

        spinner.__exit__(None, None, None)
        print(render_panel("OEM Environment Check", lines, status=status))

        # --- OpenCode Integration Panel ---
        opencode_lines = []
        opencode_status = "ok"
        opencode_dir = Path.home() / ".config" / "opencode"
        jsonc_file = opencode_dir / "opencode.jsonc"
        opencode_active = ("opencode" in enabled_adapters or jsonc_file.exists())

        if opencode_active:
            plugin_dest = opencode_dir / "plugins" / "openempiric.ts"
            inst_dest = opencode_dir / "instructions" / "memory-start.md"

            # Check plugin
            if not plugin_dest.exists():
                opencode_lines.append("✗ OpenCode Plugin not installed (missing plugins/openempiric.ts) — run 'oem setup opencode'")
                opencode_status = "error"
            else:
                try:
                    p_content = plugin_dest.read_text(encoding="utf-8")
                    if "knowledge_session_start" in p_content or "verify plugin array" in p_content or "session lifecycle is automatic" in p_content:
                        opencode_lines.append("⚠ OpenCode Plugin is legacy/outdated — run 'oem setup opencode --repair'")
                        if opencode_status != "error":
                            opencode_status = "warning"
                    else:
                        opencode_lines.append("✓ OpenCode Plugin installed")
                except Exception as e:
                    opencode_lines.append(f"⚠ Failed to read openempiric.ts plugin: {e}")
                    if opencode_status != "error":
                        opencode_status = "warning"

            # Check instructions
            if not inst_dest.exists():
                opencode_lines.append("✗ OpenCode Instructions not installed (missing instructions/memory-start.md) — run 'oem setup opencode'")
                opencode_status = "error"
            else:
                try:
                    i_content = inst_dest.read_text(encoding="utf-8")
                    if "knowledge_session_start" in i_content or "knowledge_session_commit" in i_content or "verify plugin array" in i_content:
                        opencode_lines.append("⚠ OpenCode Instructions are legacy/outdated — run 'oem setup opencode --repair'")
                        if opencode_status != "error":
                            opencode_status = "warning"
                    else:
                        opencode_lines.append("✓ OpenCode Instructions installed")
                except Exception as e:
                    opencode_lines.append(f"⚠ Failed to read memory-start.md instructions: {e}")
                    if opencode_status != "error":
                        opencode_status = "warning"

            # Check config
            mcp_registered = False
            mcp_cmd = []
            if not jsonc_file.exists():
                opencode_lines.append("✗ OpenCode Config missing (missing opencode.jsonc) — run 'oem setup opencode'")
                opencode_status = "error"
            else:
                try:
                    text = jsonc_file.read_text(encoding="utf-8")
                    cleaned = _strip_jsonc_comments(text)
                    config_data = json.loads(cleaned, strict=False)
                    inst_path_str = str(inst_dest.resolve())
                    inst_list = config_data.get("instructions", [])
                    if inst_path_str not in inst_list:
                        opencode_lines.append("✗ OpenCode Config does not register memory-start.md instruction — run 'oem setup opencode'")
                        opencode_status = "error"
                    else:
                        opencode_lines.append("✓ OpenCode Config verified")

                    mcp_config = config_data.get("mcp", {}).get("openempiric")
                    if mcp_config:
                        mcp_registered = True
                        cmd = mcp_config.get("command")
                        mcp_args = mcp_config.get("args", [])
                        if isinstance(cmd, str):
                            mcp_cmd = [cmd] + mcp_args
                        elif isinstance(cmd, list):
                            mcp_cmd = cmd + mcp_args
                        opencode_lines.append("✓ OEM MCP Server registered in OpenCode config")
                    else:
                        opencode_lines.append("✗ OEM MCP Server not registered in OpenCode config — run 'oem setup opencode'")
                        opencode_status = "error"
                except Exception as e:
                    opencode_lines.append(f"✗ OpenCode Config validation failed: {e} — run 'oem setup opencode'")
                    opencode_status = "error"

            # Check MCP Server Reachability and Functionality
            if mcp_registered and mcp_cmd:
                reachable, functional, num_tools, err = check_mcp_server(mcp_cmd)
                if reachable:
                    opencode_lines.append("✓ OEM MCP Server reachable")
                    if functional:
                        opencode_lines.append("✓ OEM MCP Server functional (stats call succeeded)")
                    else:
                        opencode_lines.append(f"✗ OEM MCP Server functional check failed: {err}")
                        opencode_status = "error"
                    opencode_lines.append(f"✓ {num_tools} tools available")
                else:
                    opencode_lines.append(f"✗ OEM MCP Server unreachable: {err}")
                    opencode_status = "error"

            # Check Context Injection Working
            from oem_knowledge.runtime import _OEM_RUNTIME_CONTEXT_PATH
            try:
                from oem_knowledge.runtime import _compile_oem_context
                _ = _compile_oem_context(eng)
                context_dir = _OEM_RUNTIME_CONTEXT_PATH.parent
                context_dir.mkdir(parents=True, exist_ok=True)
                test_file = context_dir / ".oem_doctor_write_test"
                test_file.write_text("test", encoding="utf-8")
                test_file.unlink()
                opencode_lines.append("✓ Context Injection Working")
            except Exception as e:
                opencode_lines.append(f"✗ Context Injection not working: {e}")
                opencode_status = "error"

            # Check MCP Registered (via adapter)
            try:
                from oem_knowledge.adapters import get_adapter
                adapter = get_adapter("opencode", eng, project)
                if adapter.verify_mcp():
                    opencode_lines.append("✓ MCP Registered")
                else:
                    opencode_lines.append("✗ MCP not registered")
                    opencode_status = "error"
            except Exception as e:
                opencode_lines.append(f"✗ Failed to verify MCP registration: {e}")
                opencode_status = "error"

            print(render_panel("OpenCode Integration", opencode_lines, status=opencode_status))

        # --- Codex App Integration Panel ---
        codex_lines = []
        codex_status = "ok"
        codex_active = False

        try:
            from oem_knowledge.adapters.codex_app.adapter import CodexAppAdapter
            codex_adapter = CodexAppAdapter(eng, project)
            
            try:
                config_path = codex_adapter.get_config_path()
                codex_home_detected = True
            except RuntimeError as re_err:
                codex_home_detected = False
                re_msg = str(re_err)
                lines_err = [line.strip() for line in re_msg.splitlines()]
                if "codex-app" in enabled_adapters or "codex" in enabled_adapters:
                    codex_active = True
                    codex_lines.append("✗ Codex home not detected")
                    for line_err in lines_err:
                        if "Please configure" in line_err or "Example" in line_err:
                            codex_lines.append(f"  → {line_err}")
                    codex_status = "error"

            if codex_active is False and codex_home_detected:
                if config_path.exists() or "codex-app" in enabled_adapters or "codex" in enabled_adapters:
                    codex_active = True
                    
                    # 1. Config found
                    if config_path.exists():
                        codex_lines.append("✓ Config found")
                    else:
                        codex_lines.append(f"✗ Config not found (missing {config_path})")
                        codex_status = "error"
                    
                    # 2. OEM MCP registered
                    if config_path.exists() and codex_adapter.verify_mcp():
                        codex_lines.append("✓ OEM MCP registered")
                        
                        # 3. Tools reachable
                        expected_mcp = codex_adapter.build_mcp_config()
                        mcp_cmd = [expected_mcp["command"]] + expected_mcp["args"]
                        if sys.platform != "win32" and "wsl.exe" in mcp_cmd[0].lower():
                            mcp_cmd[0] = "wsl.exe"
                        reachable, functional, num_tools, err = check_mcp_server(mcp_cmd)
                        if reachable and functional:
                            codex_lines.append("✓ Tools reachable")
                        else:
                            codex_lines.append(f"✗ Tools unreachable: {err}")
                            codex_status = "error"
                    else:
                        codex_lines.append("✗ OEM MCP registered")
                        codex_lines.append("✗ Tools reachable (MCP not registered)")
                        codex_status = "error"

                    # 4. Check MCP Registered hook (via adapter)
                    try:
                        if codex_adapter.verify_mcp():
                            codex_lines.append("✓ MCP Registered")
                        else:
                            codex_lines.append("✗ MCP not registered")
                            codex_status = "error"
                    except Exception as e:
                        codex_lines.append(f"✗ Failed to verify MCP registration: {e}")
                        codex_status = "error"

        except Exception as e:
            if "codex-app" in enabled_adapters or "codex" in enabled_adapters:
                codex_active = True
                codex_lines.append(f"✗ Codex App Integration Check failed: {e}")
                codex_status = "error"

        if codex_active:
            print(render_panel("Codex App Integration", codex_lines, status=codex_status))

        # Runtime Health Check Panel
        if any("✗" in l for l in runtime_lines):
            print(render_panel("Runtime Health", runtime_lines, status="error"))
        else:
            print(render_panel("Runtime Health", runtime_lines, status="ok"))

        if status == "error" or (codex_active and codex_status == "error"):
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

        if status == "error" or (codex_active and codex_status == "error"):
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
