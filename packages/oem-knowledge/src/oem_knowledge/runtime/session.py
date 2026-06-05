from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

class SessionState:
    def __init__(
        self,
        session_id: str,
        agent: str,
        status: str,
        started_at: float,
        project: str,
        transcript_path: str,
        context_path: str,
        temp_instructions: str,
    ):
        self.session_id = session_id
        self.agent = agent
        self.status = status
        self.started_at = started_at
        self.project = project
        self.transcript_path = transcript_path
        self.context_path = context_path
        self.temp_instructions = temp_instructions

    @classmethod
    def create(
        cls,
        session_id: str,
        agent: str,
        project: str,
        transcript_path: str,
        context_path: str,
        temp_instructions: str,
    ) -> SessionState:
        return cls(
            session_id=session_id,
            agent=agent,
            status="started",
            started_at=time.time(),
            project=str(Path(project).resolve()),
            transcript_path=str(Path(transcript_path).resolve()),
            context_path=str(Path(context_path).resolve()),
            temp_instructions=str(Path(temp_instructions).resolve()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "status": self.status,
            "started_at": self.started_at,
            "project": self.project,
            "transcript_path": self.transcript_path,
            "context_path": self.context_path,
            "temp_instructions": self.temp_instructions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SessionState:
        return cls(
            session_id=data["session_id"],
            agent=data["agent"],
            status=data["status"],
            started_at=data.get("started_at", 0.0),
            project=data["project"],
            transcript_path=data["transcript_path"],
            context_path=data["context_path"],
            temp_instructions=data["temp_instructions"],
        )

    @classmethod
    def load(cls, file_path: Path) -> Optional[SessionState]:
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception:
            return None

    def save(self, file_path: Path):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
