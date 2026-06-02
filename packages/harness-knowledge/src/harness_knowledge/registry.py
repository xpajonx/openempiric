from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ConceptEntry:
    concept_id: str
    name: str
    aliases: list[str] = field(default_factory=list)
    evidence_count: int = 0
    session_count: int = 0
    confidence: float = 0.0
    lifecycle: str = "candidate"
    materialized: bool = False
    relationships: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "concept_id": self.concept_id,
            "name": self.name,
            "aliases": self.aliases,
            "evidence_count": self.evidence_count,
            "session_count": self.session_count,
            "confidence": self.confidence,
            "lifecycle": self.lifecycle,
            "materialized": self.materialized,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ConceptEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class ConceptRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.concepts: dict[str, ConceptEntry] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for cid, entry in data.items():
                    self.concepts[cid] = ConceptEntry.from_dict(entry)
            except (json.JSONDecodeError, Exception):
                self.concepts = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {cid: entry.to_dict() for cid, entry in self.concepts.items()}
        self.path.write_text(json.dumps(data, indent=2))

    def get_or_create(self, name: str) -> ConceptEntry:
        for cid, entry in self.concepts.items():
            if entry.name.lower() == name.lower():
                return entry
            if name.lower() in [a.lower() for a in entry.aliases]:
                return entry
        cid = f"concept_{len(self.concepts) + 1:03d}"
        entry = ConceptEntry(concept_id=cid, name=name)
        self.concepts[cid] = entry
        self.save()
        return entry

    def add_evidence(self, concept_id: str):
        entry = self.concepts.get(concept_id)
        if entry is None:
            return
        entry.evidence_count += 1
        entry.confidence = min(1.0, entry.confidence + 0.15)
        if entry.evidence_count >= 3 and entry.lifecycle == "candidate":
            entry.lifecycle = "emerging"
        if entry.evidence_count >= 5 and entry.lifecycle == "emerging":
            entry.lifecycle = "validated"
            entry.materialized = True
        if entry.evidence_count >= 8 and entry.lifecycle == "validated":
            entry.lifecycle = "canonical"
        self.save()

    def stale_candidates(self, max_sessions: int = 10) -> list[str]:
        return [
            cid for cid, entry in self.concepts.items()
            if entry.lifecycle == "candidate" and entry.session_count > max_sessions
        ]
