from __future__ import annotations

import re
from pathlib import Path
class ConceptEvolutionEngine:
    def __init__(self, engine):
        self.engine = engine

    def evolve_concept(self, concept_id: str, project: str | None = None) -> dict:
        """Consolidate evidence, deduplicate learnings, and rewrite concept summary."""
        concepts_dir = self.engine._concepts_dir(project)
        concept_file = concepts_dir / f"{concept_id}.md"

        if not concept_file.exists():
            return {"status": "error", "message": f"Concept file {concept_id}.md not found."}

        content = concept_file.read_text(encoding="utf-8")

        # Extract frontmatter and body
        fm_match = re.match(r"^(---\s*\n.*?\n---\s*\n)(.*)$", content, re.DOTALL)
        if not fm_match:
            return {"status": "error", "message": "Invalid frontmatter structure."}

        header = fm_match.group(1)
        body = fm_match.group(2)

        # Parse learnings list
        learnings_section = re.search(r"## Learnings.*$", body, re.DOTALL)
        learnings = []
        if learnings_section:
            for line in learnings_section.group(0).splitlines():
                if line.strip().startswith("-"):
                    # Extract the core text
                    item = re.sub(r"^-\s*(\*\*[^*]+\*\*:\s*)?", "", line.strip())
                    if item:
                        learnings.append(item.strip())

        # Deduplicate learnings and extract key sentences
        unique_learnings = []
        seen = set()
        for item in learnings:
            item_lower = item.lower().strip(".")
            if item_lower not in seen:
                seen.add(item_lower)
                unique_learnings.append(item)

        # Rewrite summary based on learnings
        if unique_learnings:
            bullet_points = "\n".join(f"- {item}" for item in unique_learnings)
            new_body = f"# {concept_id.replace('_', ' ').title()}\n\nThis concept has evolved with consolidated evidence.\n\n## Learnings\n{bullet_points}\n"
        else:
            new_body = body

        new_content = header + new_body
        self.engine._safe_write_concept_file(concept_file, new_content, project)

        return {
            "status": "success",
            "message": f"Concept {concept_id} evolved and consolidated.",
            "learnings_count": len(unique_learnings)
        }


class ContradictionDetector:
    def __init__(self, engine):
        self.engine = engine
        self.dense_search = self.engine.search_service

        # Hardcoded architectural contradiction rule pairs (lowercased)
        self.conflict_rules = [
            (r"rest\b", r"grpc\b", "REST vs gRPC protocol conflict"),
            (r"postgresql\b", r"mysql\b", "PostgreSQL vs MySQL database selection conflict"),
            (r"tabs\b", r"spaces\b", "Tabs vs Spaces formatting conflict"),
            (r"monolith\b", r"microservice\b", "Monolithic vs Microservice architecture conflict"),
            (r"sync\b", r"async\b", "Synchronous vs Asynchronous flow conflict"),
        ]

    def detect_contradictions(self, project: str | None = None) -> list[dict]:
        """Scan all concepts and identify architectural or semantic contradictions."""
        registry = self.engine._load_registry(project)
        concepts_dir = self.engine._concepts_dir(project)
        cids = list(registry.keys())

        docs = {}
        for cid in cids:
            wiki_file = concepts_dir / f"{cid}.md"
            if wiki_file.exists():
                docs[cid] = wiki_file.read_text(encoding="utf-8").lower()

        contradictions = []

        # 1. Rule-based static contradiction scanning
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                cid_a = cids[i]
                cid_b = cids[j]
                content_a = docs.get(cid_a, "")
                content_b = docs.get(cid_b, "")

                if not content_a or not content_b:
                    continue

                for pattern_a, pattern_b, desc in self.conflict_rules:
                    if (re.search(pattern_a, content_a) and re.search(pattern_b, content_b)) or \
                       (re.search(pattern_b, content_a) and re.search(pattern_a, content_b)):
                        contradictions.append({
                            "concept_a": cid_a,
                            "concept_b": cid_b,
                            "name_a": registry[cid_a].get("canonical_name", ""),
                            "name_b": registry[cid_b].get("canonical_name", ""),
                            "type": "architectural_conflict",
                            "description": desc
                        })

        return contradictions
