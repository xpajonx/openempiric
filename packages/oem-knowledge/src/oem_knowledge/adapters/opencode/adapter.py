from pathlib import Path
from typing import Optional
from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter

@register_adapter("opencode")
class OpenCodeAdapter(BaseAdapter):
    def install_skill(self) -> bool:
        try:
            harness = self.engine._resolve_harness(self.project_path)
            skills_dir = harness / "skills"
            skills_dir.mkdir(parents=True, exist_ok=True)
            skills_file = skills_dir / "openempiric.yaml"
            
            SKILLS_YAML_CONTENT = """name: openempiric
version: 0.9.5
schema_version: 1
adapter: opencode
description: Agent knowledge runtime
required:
  - knowledge_search
  - knowledge_session_start
  - knowledge_capture_after_work
tools:
  - oem
  - knowledge_search
  - knowledge_session_start
best_practices:
  - Treat OEM as your long-term memory for the project.
  - Context is pre-loaded for you — you already know the active concepts.
  - Use knowledge_search when you need details, not before every response.
  - Explicitly state decisions and rationale.
  - Record failures and root causes.
  - Summarize experiments and results.
  - Capture tradeoffs.
  - Describe outcomes and impact.
"""
            skills_file.write_text(SKILLS_YAML_CONTENT, encoding="utf-8")
            return True
        except Exception:
            return False

    def verify_mcp(self) -> bool:
        """Verify if OpenCode has the plugin linked/installed."""
        import os
        plugins_dir = Path(os.environ.get("OPENCODE_PLUGINS_DIR", Path.home() / ".config" / "opencode" / "plugins"))
        plugin_dest = plugins_dir / "openempiric.ts"
        return plugin_dest.exists() or plugin_dest.is_symlink()

    def verify_health(self) -> tuple[bool, str]:
        import os
        from oem_knowledge.runtime.config import _REPO_ROOT
        plugins_dir = Path(os.environ.get("OPENCODE_PLUGINS_DIR", Path.home() / ".config" / "opencode" / "plugins"))
        plugin_dest = plugins_dir / "openempiric.ts"

        if not (plugin_dest.exists() or plugin_dest.is_symlink()):
            return False, "Plugin openempiric.ts not found in plugins dir"

        plugin_src = _REPO_ROOT / "plugins" / "openempiric.ts"
        if plugin_src.exists() and plugin_dest.is_symlink():
            try:
                if plugin_dest.readlink().resolve() != plugin_src.resolve():
                    return False, "Plugin symlink points to wrong location"
            except (OSError, ValueError):
                return False, "Plugin symlink broken"

        return True, "Plugin healthy"

