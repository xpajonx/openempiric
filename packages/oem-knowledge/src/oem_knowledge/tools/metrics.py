from __future__ import annotations
import json
import time
from pathlib import Path


def update_metrics_file(metrics_file: Path, updates: dict):
    data = {
        "retrieval": {
            "search_count": 0,
            "search_latency_total": 0.0,
            "search_latency_min": None,
            "search_latency_max": None,
            "last_search_latency": None,
            "last_search_at": None,
            "cache_hits": 0,
            "cache_misses": 0,
            "concepts_retrieved": 0,
        },
        "context": {
            "context_count": 0,
            "context_latency_total": 0.0,
            "context_latency_min": None,
            "context_latency_max": None,
            "last_context_latency": None,
            "last_context_at": None,
        },
        "knowledge_usage": {
            "concepts_injected": 0,
            "concepts_referenced": 0,
            "concepts_ignored": 0,
            "agent_decisions_aligned": 0,
            "last_report_at": None,
        },
        "reflection": {
            "structured_events": 0,
            "fallback_extractions": 0,
            "empty_reflections": 0,
            "file_observations": 0,
            "noise_events_filtered": 0,
            "telemetry_events_skipped": 0,
        },
        "runtime": {
            "sessions_started": 0,
            "sessions_completed": 0,
            "sessions_failed": 0,
            "sessions_recovered": 0,
            "reflections": 0,
            "materializations": 0,
        },
    }
    if metrics_file.exists():
        try:
            existing = json.loads(metrics_file.read_text(encoding="utf-8"))
            data.update(existing)
            for k in ["retrieval", "context", "knowledge_usage", "reflection", "runtime"]:
                if k in existing:
                    data[k].update(existing[k])
        except Exception:
            pass

    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if "concepts_referenced" in updates:
        data["knowledge_usage"]["concepts_referenced"] += updates["concepts_referenced"]
    if "concepts_ignored" in updates:
        data["knowledge_usage"]["concepts_ignored"] += updates["concepts_ignored"]
    if "agent_decisions_aligned" in updates:
        data["knowledge_usage"]["agent_decisions_aligned"] += updates["agent_decisions_aligned"]
    if "structured_events" in updates:
        data["reflection"]["structured_events"] += updates["structured_events"]
    if "fallback_extractions" in updates:
        data["reflection"]["fallback_extractions"] += updates["fallback_extractions"]
    if "empty_reflections" in updates:
        data["reflection"]["empty_reflections"] += updates["empty_reflections"]
    if "file_observations" in updates:
        data["reflection"]["file_observations"] += updates["file_observations"]
    if "noise_events_filtered" in updates:
        data["reflection"]["noise_events_filtered"] += updates["noise_events_filtered"]
    if "telemetry_events_skipped" in updates:
        data["reflection"]["telemetry_events_skipped"] += updates["telemetry_events_skipped"]
    if "sessions_started" in updates:
        data["runtime"]["sessions_started"] += updates["sessions_started"]
    if "sessions_completed" in updates:
        data["runtime"]["sessions_completed"] += updates["sessions_completed"]
    if "sessions_failed" in updates:
        data["runtime"]["sessions_failed"] += updates["sessions_failed"]
    if "sessions_recovered" in updates:
        data["runtime"]["sessions_recovered"] += updates["sessions_recovered"]
    if "reflections" in updates:
        data["runtime"]["reflections"] += updates["reflections"]
    if "materializations" in updates:
        data["runtime"]["materializations"] += updates["materializations"]
    data["knowledge_usage"]["last_report_at"] = now_str

    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    metrics_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def report_usage(
    concepts_used: list[str],
    concepts_ignored: list[str] | None = None,
    decisions: list[str] | None = None,
    project: str | None = None,
) -> str:
    from oem_knowledge.engine import find_harness_root, OEM_DIR

    p = Path(project or ".").resolve()
    root = find_harness_root(p) or p
    harness = root / OEM_DIR

    # 1. Read last injected concepts from session state
    session_state_path = harness / "state" / "session_state.json"
    injected_concepts = []
    if session_state_path.exists():
        try:
            state_data = json.loads(session_state_path.read_text(encoding="utf-8"))
            injected_concepts = state_data.get("last_injected_concepts", [])
        except Exception:
            pass

    # 2. Auto-derive ignored concepts if not provided
    ignored = concepts_ignored if concepts_ignored is not None else []
    if concepts_ignored is None:
        ignored = [cid for cid in injected_concepts if cid not in concepts_used]

    # 3. Update metrics
    metrics_file = harness / "state" / "metrics.json"
    update_metrics_file(
        metrics_file,
        {
            "concepts_referenced": len(concepts_used),
            "concepts_ignored": len(ignored),
            "agent_decisions_aligned": len(decisions) if decisions else 0,
        },
    )

    # 4. Append to usage_log.jsonl
    log_path = harness / "state" / "usage_log.jsonl"
    log_entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "concepts_used": concepts_used,
        "concepts_ignored": ignored,
        "decisions": decisions or [],
        "session_concepts_injected": injected_concepts,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    # Format output panel
    from oem_knowledge.ui import render_panel

    lines = [
        f"Report Timestamp: {log_entry['timestamp']}",
        f"Concepts Used:    [{', '.join(concepts_used) if concepts_used else 'None'}]",
        f"Concepts Ignored: [{', '.join(ignored) if ignored else 'None'}]",
        f"Decisions Aligned: {len(decisions) if decisions else 0}",
        "",
        "Note: This is experimental telemetry for establishing pipelines.",
        "Roadmap decisions are not made on this reported usage.",
    ]
    return render_panel("Knowledge Usage Report Received", lines, "ok")


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def knowledge_usage_report(
        concepts_used: list[str],
        concepts_ignored: list[str] | None = None,
        decisions: list[str] | None = None,
        project: str = "",
    ) -> str:
        """Report concept usage and decision alignment for the session. (Experimental/Low-Confidence Telemetry)"""
        return report_usage(concepts_used, concepts_ignored, decisions, project or None)
