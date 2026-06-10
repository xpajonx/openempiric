from __future__ import annotations
import json
from pathlib import Path
from typing import TYPE_CHECKING
from oem_knowledge.models import ConceptFitness

if TYPE_CHECKING:
    from oem_knowledge.engine import KnowledgeEngine


class FitnessService:
    """Calculates concept fitness statistics based on session outcomes.

    These statistics represent correlations between concept injection/usage and session outcomes,
    not direct evidence of causation.
    """

    def __init__(self, engine: KnowledgeEngine):
        self.engine = engine

    def _find_concept_id(self, term: str, registry: dict) -> str:
        term_clean = term.strip().lower()
        if term_clean in registry:
            return term_clean
        for cid, data in registry.items():
            if term_clean == cid.lower():
                return cid
            if term_clean == data.get("canonical_name", "").lower():
                return cid
            if term_clean in [a.lower() for a in data.get("aliases", [])]:
                return cid
        return term  # Keep the raw term if not found in registry

    def calculate_fitness(self, project: str | None = None, lock: bool = True) -> dict[str, ConceptFitness]:
        harness = self.engine._resolve_harness(project)
        outcomes_file = harness / "state" / "outcomes.jsonl"
        registry = self.engine.state._load_registry(project, lock=lock)

        # Initialize statistics for all registered concepts
        stats: dict[str, dict] = {}
        for cid, data in registry.items():
            stats[cid] = {
                "concept_id": cid,
                "canonical_name": data.get("canonical_name", cid),
                "retrieved": 0,
                "referenced": 0,
                "ignored": 0,
                "successful_sessions": 0,
                "failed_sessions": 0,
                "satisfaction_sum": 0.0,
                "evidence_count": data.get("evidence_count", 0),
            }

        if outcomes_file.exists():
            try:
                for line in outcomes_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    outcome = record.get("outcome")
                    retrieved_raw = record.get("retrieved_concepts")
                    # Fallback to referenced_concepts if retrieved_concepts is missing or empty
                    if not retrieved_raw:
                        retrieved_raw = record.get("referenced_concepts", [])
                    referenced_raw = record.get("referenced_concepts", [])

                    # Resolve unique concept IDs for this record to avoid double-counting within a session
                    retrieved_resolved = {self._find_concept_id(c, registry) for c in retrieved_raw}
                    referenced_resolved = {self._find_concept_id(c, registry) for c in referenced_raw}

                    # Parse goal satisfaction
                    satisfaction = record.get("goal_satisfaction")
                    if satisfaction is None:
                        satisfaction = 1.0 if outcome == "success" else 0.0

                    # We also want to record unregistered concepts if they show up in telemetry
                    all_resolved_cids = retrieved_resolved.union(referenced_resolved)
                    for cid in all_resolved_cids:
                        if cid not in stats:
                            stats[cid] = {
                                "concept_id": cid,
                                "canonical_name": cid,
                                "retrieved": 0,
                                "referenced": 0,
                                "ignored": 0,
                                "successful_sessions": 0,
                                "failed_sessions": 0,
                                "satisfaction_sum": 0.0,
                                "evidence_count": 0,
                            }

                    for cid in retrieved_resolved:
                        stats[cid]["retrieved"] += 1
                        if cid not in referenced_resolved:
                            stats[cid]["ignored"] += 1

                    for cid in referenced_resolved:
                        stats[cid]["referenced"] += 1
                        stats[cid]["satisfaction_sum"] += satisfaction
                        if outcome == "success":
                            stats[cid]["successful_sessions"] += 1
                        elif outcome == "failure":
                            stats[cid]["failed_sessions"] += 1
            except Exception:
                pass

        results: dict[str, ConceptFitness] = {}
        for cid, s in stats.items():
            total_resolved = s["successful_sessions"] + s["failed_sessions"]
            score = 0.0
            if total_resolved > 0:
                score = round(s["satisfaction_sum"] / total_resolved, 4)

            results[cid] = ConceptFitness(
                concept_id=s["concept_id"],
                canonical_name=s["canonical_name"],
                retrieved=s["retrieved"],
                referenced=s["referenced"],
                ignored=s["ignored"],
                successful_sessions=s["successful_sessions"],
                failed_sessions=s["failed_sessions"],
                evidence_count=s["evidence_count"],
                fitness_score=score,
            )

        return results
