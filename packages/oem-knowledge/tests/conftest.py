import pytest
import sys
import re
import math

class MockTextEmbedding:
    def __init__(self, *args, **kwargs):
        self.model_name = kwargs.get("model_name", "mock")

    def embed(self, texts):
        synonyms = {
            "guidelines": "rules",
            "upgrader": "migration",
            "upgrade": "migration",
            "db": "database",
        }
        embeddings = []
        for text in texts:
            words = re.findall(r"\w+", text.lower())
            vec = [0.0] * 384
            if words:
                for w in words:
                    w = synonyms.get(w, w)
                    idx = (hash(w) & 0xffffffff) % 384
                    vec[idx] += 1.0
                norm = math.sqrt(sum(x * x for x in vec))
                if norm > 0:
                    vec = [x / norm for x in vec]
            embeddings.append(vec)
        return embeddings

@pytest.fixture(autouse=True, scope="session")
def mock_fastembed_session():
    class DummyFastembed:
        TextEmbedding = MockTextEmbedding
    sys.modules["fastembed"] = DummyFastembed
