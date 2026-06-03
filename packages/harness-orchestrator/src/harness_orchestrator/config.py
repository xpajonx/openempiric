import yaml
from pathlib import Path

SKILL_PATH = Path.home() / ".config" / "opencode" / "skills" / "harness-orchestrator" / "SKILL.md"


def load_harness_config() -> dict:
    default_config = {
        "subagent_model": "gemini-3.5-flash",
        "enable_telemetry": True,
        "max_parallel_tasks": 4,
        "knowledge_path": ".harness",
        "auto_reflect": True,
        "dangerously_skip_permissions": True,
    }
    if not SKILL_PATH.exists():
        return default_config

    try:
        content = SKILL_PATH.read_text(encoding="utf-8")
        if content.startswith("---"):
            parts = content.split("---")
            if len(parts) >= 3:
                yaml_data = yaml.safe_load(parts[1])
                if yaml_data and isinstance(yaml_data, dict):
                    return {**default_config, **yaml_data.get("config", {})}
    except Exception:
        pass
    return default_config
