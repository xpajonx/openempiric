from __future__ import annotations

import math
from pathlib import Path
from oem_knowledge.search import DenseSearch

class SemanticIdentityResolver:
    def __init__(self, engine):
        self.engine = engine
        self.dense_search = DenseSearch()

    def scan_duplicates(self, project: str | None = None, threshold: float = 0.82) -> list[dict]:
        """Scan registry concepts for semantic duplicates."""
        registry = self.engine._load_registry(project)
        if not registry:
            return []

        cids = list(registry.keys())
        texts = []
        for cid in cids:
            data = registry[cid]
            aliases_str = ", ".join(data.get("aliases", []))
            texts.append(f"{data.get('canonical_name', '')}: {aliases_str}")

        if not texts:
            return []

        embeddings = self.dense_search.embed(texts)
        candidates = []

        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                sim = self.dense_search.cosine_similarity(embeddings[i], embeddings[j])
                if sim >= threshold:
                    candidates.append({
                        "concept_a": cids[i],
                        "concept_b": cids[j],
                        "name_a": registry[cids[i]].get("canonical_name", ""),
                        "name_b": registry[cids[j]].get("canonical_name", ""),
                        "similarity": sim
                    })

        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        return candidates
