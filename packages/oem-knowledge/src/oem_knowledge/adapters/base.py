from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

class BaseAdapter:
    """Canonical SDK contract interface for all agent adapters extending OpenEmpiric.
    
    Implementations should register themselves via register_adapter() decorator.
    """
    
    def __init__(self, engine: KnowledgeEngine, project_path: Optional[str] = None):
        self.engine = engine
        self.project_path = project_path

    def install_skill(self) -> bool:
        """Install skill metadata into the project workspace (e.g. skills/openempiric.yaml)."""
        return False

    def verify_mcp(self) -> bool:
        """Verify if the adapter environment is registered/ready (e.g. plugin linked/installed)."""
        return False

    def get_expected_transcript_path(self, session_id: str) -> Path:
        """Get the expected path where the session transcript is stored for recovery."""
        h = self.engine._resolve_harness(self.project_path)
        return h / "state" / f"chat_{session_id}.md"

    def parse_transcript(self, transcript_path: Path) -> str:
        """Parse the agent's custom log/TUI transcript format into a raw conversation text string."""
        if transcript_path.exists():
            return transcript_path.read_text(encoding="utf-8")
        return ""

    def discover_latest_transcript(self) -> Optional[Path]:
        """Optionally scan system directories to locate the latest transcript file."""
        return None

    # Lifecycle runtime hooks
    def pre_session(self) -> None:
        """Executed before the coding agent session begins."""
        pass

    def context_injection(self) -> str:
        """Compile and format project context details into instructions for the agent."""
        return ""

    def knowledge_search(self, query: str) -> Dict[str, Any]:
        """Perform search operations against the active knowledge graph."""
        return {"results": []}

    def post_session(self, committed: bool) -> None:
        """Executed after the agent session ends and commit steps complete."""
        pass
