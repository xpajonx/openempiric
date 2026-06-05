from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine

class BaseAdapter:
    """Canonical SDK contract interface for all agent adapters extending OpenEmpiric.
    
    Implementations should register themselves via register_adapter() decorator.
    
    CRITICAL: Sane defaults are provided for all methods and hooks. 
    Many adapters only need to override:
      - verify_mcp() (check if the agent is ready/configured)
      - parse_transcript() (extract conversation dialogue from proprietary logs)
    
    Other lifecycle hooks are completely OPTIONAL and are executed as no-ops by default.
    """
    
    def __init__(self, engine: KnowledgeEngine, project_path: Optional[str] = None):
        self.engine = engine
        self.project_path = project_path

    # =========================================================================
    # CORE INTERFACE METHODS (Typically overridden by adapter authors)
    # =========================================================================

    def install_skill(self) -> bool:
        """[OPTIONAL] Install skill metadata into the project workspace (e.g. skills/openempiric.yaml)."""
        return False

    def verify_mcp(self) -> bool:
        """[RECOMMENDED] Verify if the adapter environment is registered/ready (e.g. plugin linked/installed)."""
        return False

    def get_expected_transcript_path(self, session_id: str) -> Path:
        """[RECOMMENDED] Get the expected path where the session transcript is stored for recovery."""
        h = self.engine._resolve_harness(self.project_path)
        return h / "state" / f"chat_{session_id}.md"

    def parse_transcript(self, transcript_path: Path) -> str:
        """[RECOMMENDED] Parse the agent's custom log/TUI transcript format into raw conversation text.
        
        Default implementation reads the file directly as plain text.
        """
        if transcript_path.exists():
            return transcript_path.read_text(encoding="utf-8")
        return ""

    def discover_latest_transcript(self) -> Optional[Path]:
        """[OPTIONAL] Scan system directories to locate the latest transcript file if path is unknown."""
        return None

    # =========================================================================
    # OPTIONAL LIFECYCLE RUNTIME HOOKS (Only override if custom interception is needed)
    # =========================================================================

    def pre_session(self) -> None:
        """[OPTIONAL] Executed immediately before the coding agent session begins."""
        pass

    def context_injection(self) -> str:
        """[OPTIONAL] Compile and format project context details into instructions for the agent."""
        return ""

    def knowledge_search(self, query: str) -> Dict[str, Any]:
        """[OPTIONAL] Perform search operations against the active knowledge graph."""
        return {"results": []}

    def post_session(self, committed: bool) -> None:
        """[OPTIONAL] Executed after the agent session ends and commit steps complete."""
        pass
