from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25:
    def __init__(
        self,
        doc_freqs: dict[str, int] | None = None,
        avg_dl: float = 0,
        total_docs: int = 0,
    ):
        self.doc_freqs = doc_freqs or {}
        self.avg_dl = avg_dl
        self.total_docs = total_docs
        self.k1 = 1.5
        self.b = 0.75

    def score(self, query: str, doc_text: str) -> float:
        if self.total_docs == 0:
            return 0.0
        query_tokens = tokenize(query)
        doc_tokens = tokenize(doc_text)
        doc_len = len(doc_tokens)
        tf = Counter(doc_tokens)

        score = 0.0
        idf_cache = {}
        for t in query_tokens:
            if t not in idf_cache:
                df = self.doc_freqs.get(t, 1)
                idf_cache[t] = math.log((self.total_docs - df + 0.5) / (df + 0.5) + 1.0)
            idf = idf_cache[t]
            freq = tf.get(t, 0)
            score += (
                idf
                * (freq * (self.k1 + 1))
                / (freq + self.k1 * (1 - self.b + self.b * doc_len / self.avg_dl))
            )

        return score


class DenseSearch:
    def __init__(self):
        self._model = None

    def _lazy_model(self):
        if self._model is None:
            import sys
            from fastembed import TextEmbedding
            try:
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=True)
            except Exception:
                print("\n[OEM] Embedding model 'BAAI/bge-small-en-v1.5' not found in cache.", file=sys.stderr)
                print("[OEM] Downloading model (~67 MB)...", file=sys.stderr)
                self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", local_files_only=False)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(emb) for emb in self._lazy_model().embed(texts)]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
