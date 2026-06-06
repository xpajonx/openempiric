import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from oem_knowledge.engine import KnowledgeEngine

from oem_knowledge.adapters.base import BaseAdapter
from oem_knowledge.adapters.registry import register_adapter

@register_adapter("antigravity")
@register_adapter("agy")
class AntigravityAdapter(BaseAdapter):
    def __init__(self, engine: Optional[KnowledgeEngine] = None, project_path: Optional[str] = None):
        eng = engine or KnowledgeEngine(project_path)
        super().__init__(eng, project_path)

    def get_app_data_dir(self) -> Path:
        """Get the app data directory where Antigravity stores logs/transcripts."""
        return Path(os.environ.get("AGY_APP_DATA_DIR", Path.home() / ".gemini" / "antigravity-cli"))

    def parse_transcript(self, transcript_path: Path) -> str:
        """Parse AGY JSONL transcript format into a raw conversation string."""
        if not transcript_path.exists():
            logging.warning(f"Transcript path does not exist: {transcript_path}")
            return ""
        
        conversation_lines = []
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        step = json.loads(line)
                        source = step.get("source")
                        step_type = step.get("type")
                        
                        if step_type == "USER_INPUT":
                            content = step.get("content", "")
                            conversation_lines.append(f"User: {content}")
                        elif step_type == "PLANNER_RESPONSE":
                            content = step.get("content", "")
                            conversation_lines.append(f"Agent: {content}")
                    except Exception as e:
                        logging.warning(f"Error parsing transcript line: {e}")
        except Exception as e:
            logging.error(f"Failed to read transcript file: {e}")
            
        return "\n\n".join(conversation_lines)

    def discover_latest_transcript(self) -> Optional[Path]:
        """Find the latest transcript.jsonl under the AGY app data dir."""
        app_data = self.get_app_data_dir()
        brain_dir = app_data / "brain"
        if not brain_dir.is_dir():
            return None
        
        latest_transcript = None
        latest_mtime = 0.0
        
        # Traverse brain/<conversation-id>/.system_generated/logs/transcript.jsonl
        for conversation_dir in brain_dir.iterdir():
            if not conversation_dir.is_dir():
                continue
            t_file = conversation_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if t_file.exists():
                mtime = t_file.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_transcript = t_file
                    
        return latest_transcript

    def discover_transcript_by_id(self, conversation_id: str) -> Optional[Path]:
        """Find transcript.jsonl for a specific conversation ID."""
        app_data = self.get_app_data_dir()
        t_file = app_data / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
        return t_file if t_file.exists() else None

    def context_injection(self, project_path: Optional[str] = None) -> str:
        """Capabilities: context_injection."""
        from oem_knowledge.runtime.context import _compile_oem_context
        ctx = _compile_oem_context(self.engine)
        
        lines = [
            "# OEM Runtime Notice",
            "Project memory is already active. Relevant project memory has been restored automatically. "
            "OEM memory serves as a persistent knowledge layer to inform your planning and guide your execution of project workflows (e.g. AGENTS.md) without replacing them. "
            "Cross-reference the active concepts and past failures below to ensure your work aligns with existing decisions and avoids repeat mistakes. "
            "Use OEM search when additional project context is needed (such as reviewing project history, understanding prior decisions, or investigating known failures). "
            "Diagnostics (doctor/health) are not required for normal work and are automatically managed by the runtime supervisor. Do not assume work should proceed unless the user requests it. "
            "Your utilization of memory is measured and logged at the end of the session via the knowledge_usage_report tool.\n",
            "# Previous Session Context\n"
        ]
        
        lines.append("## Last Topic")
        lines.append(ctx.get("last_topic", "General development") + "\n")
        
        lines.append("## Recent Decisions")
        decisions = ctx.get("recent_decisions", [])
        if decisions:
            for d in decisions:
                lines.append(f"- {d}")
        else:
            lines.append("- None")
        lines.append("")
        
        lines.append("## Open Questions")
        questions = ctx.get("open_questions", [])
        if questions:
            for q in questions:
                lines.append(f"- {q}")
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Active Concepts")
        concepts = ctx.get("active_concepts", [])
        if concepts:
            for c in concepts[:5]:
                desc = c.get("description", "")
                desc_str = f": {desc}" if desc else ""
                lines.append(f"- **{c['name']}** ({c['id']}){desc_str}")
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Relevant Failures")
        failures = ctx.get("relevant_failures", [])
        if failures:
            for f in failures:
                lines.append(f"- {f}")
        else:
            lines.append("- None")
        lines.append("")

        lines.append("## Memory Context")
        lines.append(ctx.get("memory_context", "") + "\n")
        
        return "\n".join(lines).strip()

    def session_start(self, project_path: Optional[str] = None) -> Dict[str, Any]:
        """Capabilities: session_start."""
        proj = Path(project_path) if project_path else Path.cwd()
        res = self.engine.restore_session_state(project_path)
        
        import uuid
        session_id = f"session_{uuid.uuid4().hex[:12]}"
        
        return {
            "session_id": session_id,
            "active_goals": res.get("active_goals", []),
            "recent_discoveries": res.get("active_decisions", [])
        }

    def session_commit(self, conversation_text_or_path: str, outcome: str = "success", reason: Optional[str] = None, session_id: Optional[str] = None, project_path: Optional[str] = None) -> Dict[str, Any]:
        """Capabilities: session_commit."""
        # Check if conversation_text_or_path is a path to a transcript file or ID
        conversation_text = ""
        if conversation_text_or_path:
            p = Path(conversation_text_or_path)
            if p.exists() and p.is_file():
                conversation_text = self.parse_transcript(p)
            elif len(conversation_text_or_path) < 100 and "/" not in conversation_text_or_path:
                # Might be conversation ID
                p_id = self.discover_transcript_by_id(conversation_text_or_path)
                if p_id:
                    conversation_text = self.parse_transcript(p_id)
                else:
                    conversation_text = conversation_text_or_path
            else:
                conversation_text = conversation_text_or_path

        res = self.engine.session_commit(project_path, conversation_text=conversation_text, session_id=session_id)
        
        # Record outcome
        try:
            self.engine.record_outcome(outcome, session_id=session_id, project=project_path, reason=reason)
        except Exception as e:
            logging.warning(f"Failed to record outcome: {e}")
            
        return res

    def verify_mcp(self) -> bool:
        """Verify if Antigravity has the app data directory created."""
        try:
            return self.get_app_data_dir().exists()
        except Exception:
            return False

    def get_expected_transcript_path(self, session_id: str) -> Path:
        """Get the expected path where the transcript is stored for recovery."""
        app_data = self.get_app_data_dir()
        return app_data / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"


