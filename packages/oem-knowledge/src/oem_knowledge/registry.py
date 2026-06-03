from __future__ import annotations

import json
from pathlib import Path
from oem_knowledge.models import ConceptData
from oem_knowledge.engine import SecureFileSystem


class ConceptRegistry:
    def __init__(self, path: Path):
        self.path = path.resolve()
        # Find project path as the parent directory that contains `.harness` or just the registry's parent parent
        harness_root = path.parent.parent
        self.sfs = SecureFileSystem(harness_root)
        self.concepts: dict[str, ConceptData] = {}
        self._load()

    def _load(self):
        if self.sfs.exists(self.path):
            try:
                data = json.loads(self.sfs.read_text(self.path))
                for cid, entry in data.items():
                    self.concepts[cid] = ConceptData(**entry)
            except Exception:
                self.concepts = {}

    def save(self):
        data = {cid: entry.model_dump() for cid, entry in self.concepts.items()}
        self.sfs.write_text(
            self.path, json.dumps(data, indent=2), force_allow_truncation=True
        )

    def get_or_create(self, name: str) -> ConceptData:
        for cid, entry in self.concepts.items():
            if entry.canonical_name.lower() == name.lower().replace(" ", "-"):
                return entry
            if name.lower() in [a.lower() for a in entry.aliases]:
                return entry
        cid = f"concept_{len(self.concepts) + 1:03d}"
        entry = ConceptData(
            concept_id=cid,
            canonical_name=name.lower().replace(" ", "-"),
            aliases=[name],
        )
        self.concepts[cid] = entry
        self.save()
        return entry

    def add_evidence(self, concept_id: str):
        entry = self.concepts.get(concept_id)
        if entry is None:
            return
        entry.evidence_count += 1
        # confidence mapped from 1 to 5
        entry.confidence = min(5, entry.confidence + 1)
        if entry.evidence_count >= 3 and entry.status == "candidate":
            entry.status = "emerging"
        if entry.evidence_count >= 5 and entry.status == "emerging":
            entry.status = "validated"
        if entry.evidence_count >= 8 and entry.status == "validated":
            entry.status = "canonical"
        self.save()

    def stale_candidates(self, max_sessions: int = 10) -> list[str]:
        return [
            cid
            for cid, entry in self.concepts.items()
            if entry.status == "candidate" and len(entry.sessions) > max_sessions
        ]
