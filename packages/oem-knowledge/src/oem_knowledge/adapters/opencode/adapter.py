from pathlib import Path
from typing import Optional
from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.engine import KnowledgeEngine

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
  - search_existing_knowledge_before_work
  - record_outcomes_after_work
  - knowledge_search_before_work
  - knowledge_capture_after_work
tools:
  - oem
  - knowledge_search
  - knowledge_session_start
"""
            skills_file.write_text(SKILLS_YAML_CONTENT, encoding="utf-8")
            return True
        except Exception:
            return False
