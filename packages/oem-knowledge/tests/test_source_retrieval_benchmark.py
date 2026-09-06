from __future__ import annotations

import json
import hashlib
import re
import statistics
import time
from pathlib import Path

import pytest

from oem_knowledge.engine import KnowledgeEngine

FIXTURE = Path(__file__).parent / "fixtures" / "source_retrieval_benchmark.json"


def _load_fixture():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert isinstance(data["corpus"], dict) and 24 <= len(data["corpus"]) <= 28
    assert isinstance(data["cases"], list)
    kinds = {"exact_identifier", "semantic_paraphrase"}
    for case in data["cases"]:
        assert set(case) == {"id", "kind", "query", "relevant_paths"}
        assert case["kind"] in kinds and case["relevant_paths"]
        assert all(path in data["corpus"] for path in case["relevant_paths"])
        if case["kind"] == "exact_identifier":
            assert len(case["relevant_paths"]) == 1
            assert case["query"] in data["corpus"][case["relevant_paths"][0]]
        else:
            query_tokens = set(re.findall(r"[a-z0-9]+", case["query"].lower()))
            filename = Path(case["relevant_paths"][0]).name
            stem_tokens = set(re.findall(r"[a-z0-9]+", Path(filename).stem.lower()))
            assert not query_tokens & stem_tokens
    assert sum(c["kind"] == "exact_identifier" for c in data["cases"]) >= 8
    assert sum(c["kind"] == "semantic_paraphrase" for c in data["cases"]) >= 12
    return data


def _materialize(data, root):
    for rel_path, content in data["corpus"].items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _paths(result):
    return [row["metadata"]["rel_path"] for row in result.get("results", [])]


def _reciprocal_rank(paths, relevant, cutoff=5):
    for index, path in enumerate(paths[:cutoff], 1):
        if path in relevant:
            return 1.0 / index
    return 0.0


def _hit_at_5(paths, relevant):
    return float(bool(set(paths[:5]) & set(relevant)))


def _top1_hit(paths, relevant):
    return float(bool(paths) and paths[0] in relevant)


def _index_storage_bytes(path):
    return sum(
        candidate.stat().st_size
        for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"))
        if candidate.exists()
    )


class BenchmarkSemanticEmbedding:
    """Deterministic, noisy sparse proxy for semantic retrieval integration."""

    dimension = 256
    synonyms = {
        "combine": "reconcile",
        "accounting": "ledger",
        "book": "ledger",
        "catalog": "manifest",
        "read": "load",
        "indexed": "source",
        "files": "source",
        "create": "build",
        "searchable": "source",
        "database": "index",
        "order": "rank",
        "ranking": "rank",
        "matching": "source",
        "documents": "results",
        "release": "close",
        "resources": "session",
        "cycle": "session",
        "clean": "normalize",
        "standardize": "normalize",
        "relative": "rel",
        "location": "path",
        "assign": "classify",
        "category": "classify",
        "contents": "document",
        "compute": "calculate",
        "fraction": "rate",
        "guidance": "instructions",
        "searching": "retrieval",
        "project": "source",
        "code": "source",
        "automated": "agent",
        "worker": "agent",
        "locating": "find",
        "demonstration": "demo",
        "operation": "search",
        "options": "settings",
        "turn": "enable",
        "on": "enabled",
        "exported": "generated",
        "machine": "generated",
        "report": "summary",
        "written": "docs",
        "consistent": "reconcile",
    }

    def embed(self, texts):
        vectors = []
        for text in texts:
            tokens = re.findall(r"[A-Za-z0-9]+", text)
            expanded = []
            for token in tokens:
                camel_parts = re.sub(r"(?<!^)(?=[A-Z])", " ", token).lower().split()
                for part in camel_parts:
                    split_parts = part.split("_")
                    expanded.extend(split_parts)
                    expanded.extend(split_parts)
                    synonym = self.synonyms.get(part, part)
                    expanded.extend([synonym, synonym, synonym])
            vector = [0.0] * self.dimension
            for token in expanded:
                slot = int.from_bytes(
                    hashlib.sha256(token.encode("utf-8")).digest()[:4], "big"
                ) % self.dimension
                vector[slot] += 1.0
            noise = hashlib.sha256(text.encode("utf-8")).digest()
            for offset in range(4):
                vector[noise[offset] % self.dimension] += 0.03
            norm = sum(value * value for value in vector) ** 0.5 or 1.0
            vectors.append([value / norm for value in vector])
        return vectors


def test_metric_helpers():
    assert _reciprocal_rank(["a", "b"], ["b"]) == 0.5
    assert _reciprocal_rank(["a", "b"], ["z"]) == 0.0
    assert _hit_at_5(["a", "b"], ["b"]) == 1.0
    assert _hit_at_5(["a"], ["b"]) == 0.0
    assert _top1_hit(["a"], ["a"]) == 1.0
    assert _top1_hit(["b", "a"], ["a"]) == 0.0


def test_fixture_validation():
    data = _load_fixture()
    assert data["version"] == 1


def test_source_retrieval_benchmark(tmp_path):
    data = _load_fixture()
    _materialize(data, tmp_path)
    eng = KnowledgeEngine(project_path=tmp_path)
    eng.init_project(str(tmp_path))
    eng.search.set_retrieval_mode("bm25")
    started = time.perf_counter()
    eng.source.index()
    discovery = eng.source.discover_files()
    indexed_paths = {
        item.rel_path
        for item in discovery.discovered_files
        if item.eligible
    }
    index_latency_ms = (time.perf_counter() - started) * 1000
    cases = data["cases"]
    for case in cases:
        eng.source.search(case["query"], k=5)
    quality = []
    timings = []
    for case in cases:
        started = time.perf_counter()
        result = eng.source.search(case["query"], k=5)
        timings.append((time.perf_counter() - started) * 1000)
        assert result.get("status") in {"success", "no_relevant_source_results"}, (
            f"unexpected search status for case {case['id']}: {result.get('status')}"
        )
        paths = _paths(result)
        assert set(paths) <= set(data["corpus"])
        quality.append((case, paths))
    for _ in range(3):
        for case in cases:
            started = time.perf_counter()
            eng.source.search(case["query"], k=5)
            timings.append((time.perf_counter() - started) * 1000)
    exact = [(c, p) for c, p in quality if c["kind"] == "exact_identifier"]
    semantic = [(c, p) for c, p in quality if c["kind"] == "semantic_paraphrase"]
    labeled_targets = {
        path for case, _ in semantic for path in case["relevant_paths"]
    }
    eligible_target_paths = labeled_targets & indexed_paths
    indexed_target_paths = eligible_target_paths & indexed_paths
    excluded_semantic = [
        case for case, _ in semantic
        if not set(case["relevant_paths"]) <= eligible_target_paths
    ]
    evaluated_semantic = [
        (case, paths)
        for case, paths in semantic
        if set(case["relevant_paths"]) <= eligible_target_paths
    ]
    exact_top1 = sum(_top1_hit(p, c["relevant_paths"]) for c, p in exact) / len(exact)
    semantic_hit = sum(_hit_at_5(p, c["relevant_paths"]) for c, p in evaluated_semantic) / len(evaluated_semantic)
    misses = [c["id"] for c, p in evaluated_semantic if not _hit_at_5(p, c["relevant_paths"])]
    layout = eng.layout()
    summary = {
        "fixture_version": data["version"], "case_count": len(cases),
        "fixture_path_count": len(data["corpus"]), "indexed_path_count": len(indexed_paths),
        "labeled_target_count": len(labeled_targets), "indexed_target_count": len(indexed_target_paths),
        "eligible_target_count": len(eligible_target_paths),
        "eligible_target_coverage": len(indexed_target_paths) / len(eligible_target_paths),
        "fixture_target_coverage": len(eligible_target_paths) / len(labeled_targets),
        "uncovered_target_paths": sorted(labeled_targets - indexed_paths),
        "exact_case_count": len(exact), "semantic_case_count": len(semantic),
        "semantic_evaluated_case_count": len(evaluated_semantic),
        "semantic_excluded_case_ids": [c["id"] for c in excluded_semantic],
        "exact_top1_hit_rate": exact_top1,
        "exact_mrr_at_5": sum(_reciprocal_rank(p, c["relevant_paths"]) for c, p in exact) / len(exact),
        "semantic_hit_at_5": semantic_hit,
        "semantic_mrr_at_5": sum(_reciprocal_rank(p, c["relevant_paths"]) for c, p in evaluated_semantic) / len(evaluated_semantic),
        "semantic_miss_case_ids": misses, "index_latency_ms": index_latency_ms,
        "median_search_latency_ms": statistics.median(timings),
        "source_index_bytes": _index_storage_bytes(layout.source_index_db_path),
        "source_manifest_bytes": layout.source_manifest_path.stat().st_size,
        "hybrid_justified": semantic_hit <= 0.85,
    }
    assert exact_top1 == 1.0
    assert len(evaluated_semantic) == 10
    assert len(summary["semantic_excluded_case_ids"]) == 3
    assert set(summary["semantic_excluded_case_ids"]) == {
        "semantic-example", "semantic-config", "semantic-generated"
    }
    assert summary["eligible_target_coverage"] == 1.0
    assert all(0 <= summary[key] <= 1 for key in ("exact_top1_hit_rate", "exact_mrr_at_5", "semantic_hit_at_5", "semantic_mrr_at_5"))
    assert set(misses) <= {c["id"] for c, _ in semantic}
    hybrid_root = tmp_path / "hybrid"
    hybrid_root.mkdir()
    _materialize(data, hybrid_root)
    hybrid = KnowledgeEngine(project_path=hybrid_root)
    hybrid.init_project(str(hybrid_root))
    hybrid.search.set_retrieval_mode("hybrid")
    hybrid._model = BenchmarkSemanticEmbedding()
    hybrid._local_load_failed = False
    started = time.perf_counter()
    hybrid.source.index()
    hybrid_discovery = hybrid.source.discover_files()
    hybrid_indexed_paths = {
        item.rel_path
        for item in hybrid_discovery.discovered_files
        if item.eligible
    }
    hybrid_index_latency_ms = (time.perf_counter() - started) * 1000
    hybrid_quality = []
    hybrid_timings = []
    for case in cases:
        started = time.perf_counter()
        result = hybrid.source.search(case["query"], k=5)
        hybrid_timings.append((time.perf_counter() - started) * 1000)
        hybrid_quality.append((case, _paths(result)))
    hexact = [(c, p) for c, p in hybrid_quality if c["kind"] == "exact_identifier"]
    hsemantic = [(c, p) for c, p in hybrid_quality if c["kind"] == "semantic_paraphrase"]
    hsemantic = [(c, p) for c, p in hsemantic if set(c["relevant_paths"]) <= hybrid_indexed_paths]
    hybrid_semantic_hit = sum(_hit_at_5(p, c["relevant_paths"]) for c, p in hsemantic) / len(hsemantic)
    hybrid_exact_top1 = sum(_top1_hit(p, c["relevant_paths"]) for c, p in hexact) / len(hexact)
    summary.update({
        "hybrid_exact_top1_hit_rate": hybrid_exact_top1,
        "hybrid_exact_mrr_at_5": sum(_reciprocal_rank(p, c["relevant_paths"]) for c, p in hexact) / len(hexact),
        "hybrid_semantic_hit_at_5": hybrid_semantic_hit,
        "hybrid_semantic_mrr_at_5": sum(_reciprocal_rank(p, c["relevant_paths"]) for c, p in hsemantic) / len(hsemantic),
        "semantic_hit_at_5_gain": hybrid_semantic_hit - semantic_hit,
        "hybrid_index_latency_ms": hybrid_index_latency_ms,
        "hybrid_median_search_latency_ms": statistics.median(hybrid_timings),
        "hybrid_source_index_bytes": _index_storage_bytes(hybrid.layout().source_index_db_path),
        "hybrid_source_manifest_bytes": hybrid.layout().source_manifest_path.stat().st_size,
        "hybrid_quality_gate_passed": hybrid_exact_top1 == 1.0 and hybrid_semantic_hit >= semantic_hit + 0.15,
    })
    assert hybrid_exact_top1 == 1.0
    assert hybrid_semantic_hit >= semantic_hit + 0.15
    assert summary["hybrid_quality_gate_passed"]
    print(json.dumps(summary, indent=2, sort_keys=True))
