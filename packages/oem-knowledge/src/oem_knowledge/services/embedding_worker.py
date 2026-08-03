"""Spawn-isolated semantic indexing worker.

Runs KnowledgeEngine indexing in a subprocess so the parent can enforce a
hard wall-clock bound (terminate) — session finalization must never hang.
"""
import logging
import os
from multiprocessing.queues import Queue

logger = logging.getLogger(__name__)


def run_isolated_index(project_dir: str, budget_s: float = 10.0) -> dict:
    """Run offline, local-only indexing. Returns a picklable result dict."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        from oem_knowledge.engine import KnowledgeEngine
        with KnowledgeEngine(project_dir) as eng:
            result = eng.search.index_all(progress_callback=None, budget_seconds=budget_s)
        if not isinstance(result, dict):
            result = {"status": "error", "error": f"unexpected result type: {type(result).__name__}"}
        return result
    except Exception as exc:  # pragma: no cover - defensive subprocess boundary
        logger.warning("[OEM] isolated index worker failed: %s", exc)
        return {"status": "error", "error": str(exc)}


def _worker_main(project_dir: str, budget_s: float, result_queue: Queue) -> None:
    result_queue.put(run_isolated_index(project_dir, budget_s))
