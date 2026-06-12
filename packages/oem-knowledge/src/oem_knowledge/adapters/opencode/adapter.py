import logging
from pathlib import Path
from typing import Optional
import yaml
from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter

logger = logging.getLogger(__name__)

@register_adapter("opencode")
class OpenCodeAdapter(BaseAdapter):
    def install_skill(self) -> bool:
        try:
            # Purge stale skill cache
            import shutil
            cache_dir = Path.home() / ".config" / "opencode" / "skills" / "openempiric"
            if cache_dir.exists():
                if cache_dir.is_dir():
                    shutil.rmtree(cache_dir)
                else:
                    cache_dir.unlink()

            harness = self.engine._resolve_harness(self.project_path)
            skills_dir = harness / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            skills_file = skills_dir / "openempiric.yaml"

            existing_data = {}
            if skills_file.exists():
                try:
                    with open(skills_file, "r", encoding="utf-8") as f:
                        existing_data = yaml.safe_load(f) or {}
                except Exception as e:
                    logger.warning(f"Failed to parse existing OpenCode skills file {skills_file}: {e}")

            adapters = existing_data.get("adapters", [])
            if not isinstance(adapters, list):
                adapters = [adapters] if adapters else []
            
            if "adapter" in existing_data:
                legacy = existing_data["adapter"]
                if legacy and legacy not in adapters:
                    adapters.append(legacy)

            if "opencode" not in adapters:
                adapters.append("opencode")

            updated_data = dict(existing_data)
            updated_data["name"] = existing_data.get("name", "openempiric")
            updated_data["version"] = existing_data.get("version", "1.0.0")
            updated_data["schema_version"] = existing_data.get("schema_version", 1)
            updated_data["adapters"] = adapters
            
            if "adapter" in updated_data:
                del updated_data["adapter"]

            updated_data["description"] = existing_data.get("description", "Agent knowledge runtime")
            updated_data["required"] = existing_data.get("required", ["knowledge_search", "knowledge_capture_after_work"])
            updated_data["tools"] = existing_data.get("tools", ["oem", "knowledge_search"])
            updated_data["best_practices"] = existing_data.get("best_practices", [
                "OpenEmpiric is already active for this session; do not initialize it manually.",
                "Relevant project memory has been restored automatically into your context.",
                "When OEM knowledge is relevant, prefer calling OEM tools directly instead of executing shell commands.",
                "Do not use shell execution when a corresponding OEM tool is available.",
                "Refer to active concepts and past failures during planning to align with existing decisions.",
                "Report referenced memory concepts at session end using the knowledge_usage_report tool.",
                "Use knowledge_search when additional project context is needed.",
                "Fallback Strategy: If the MCP server is unreachable or a tool call fails, fall back to the OEM CLI (oem search)."
            ])

            with open(skills_file, "w", encoding="utf-8") as f:
                yaml.safe_dump(updated_data, f, default_flow_style=False, sort_keys=False)
            return True
        except Exception as e:
            logger.warning(f"Failed to install OpenCode skill: {e}", exc_info=True)
            return False

    def verify_mcp(self) -> bool:
        """Verify if OpenCode has the plugin linked/installed."""
        import os
        plugins_dir = Path(os.environ.get("OPENCODE_PLUGINS_DIR", Path.home() / ".config" / "opencode" / "plugins"))
        plugin_dest = plugins_dir / "openempiric.ts"
        return plugin_dest.exists() or plugin_dest.is_symlink()

    def verify_health(self) -> tuple[bool, str]:
        import os
        plugins_dir = Path(os.environ.get("OPENCODE_PLUGINS_DIR", Path.home() / ".config" / "opencode" / "plugins"))
        plugin_dest = plugins_dir / "openempiric.ts"

        if not (plugin_dest.exists() or plugin_dest.is_symlink()):
            return False, "Plugin openempiric.ts not found in plugins dir"

        from oem_knowledge.runtime.config import _REPO_ROOT
        plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
        if not plugin_src.exists():
            plugin_src = Path(__file__).resolve().parent.parent.parent / "plugins" / "openempiric.ts"
        if not plugin_src.exists():
            plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"

        if plugin_dest.is_symlink():
            try:
                target = plugin_dest.readlink()
                resolved_target = (plugins_dir / target).resolve() if not target.is_absolute() else target.resolve()
                import tempfile
                sys_temp = Path(tempfile.gettempdir()).resolve()
                try:
                    is_in_temp = resolved_target.resolve().is_relative_to(sys_temp)
                except ValueError:
                    is_in_temp = False
                if is_in_temp:
                    try:
                        is_in_project = resolved_target.resolve().is_relative_to(Path(self.project_path).resolve())
                    except ValueError:
                        is_in_project = False
                    if not is_in_project:
                        return False, f"Plugin symlink target is in volatile temp directory: {resolved_target}"
                if not resolved_target.exists():
                    return False, f"Plugin symlink broken: target not found ({resolved_target})"
                if resolved_target.name != "openempiric.ts":
                    return False, f"Plugin symlink points to wrong location: wrong file name ({resolved_target.name})"
            except (OSError, ValueError) as e:
                return False, f"Plugin symlink broken: {e}"

        return True, "Plugin healthy"
