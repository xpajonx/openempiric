from pathlib import Path
from typing import Optional
from oem_knowledge.engine import KnowledgeEngine

class BaseAdapter:
    def __init__(self, engine: KnowledgeEngine, project_path: Optional[str] = None):
        self.engine = engine
        self.project_path = project_path

    def install_skill(self) -> bool:
        """Install skill metadata into the project workspace."""
        return False

    def verify_mcp(self) -> bool:
        """Verify if the MCP server is registered for this adapter."""
        return False

