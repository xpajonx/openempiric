from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from ..client import find_opencode, safe_workdir, run_json
from harness_tui.panels import render_panel

_plans: dict[str, dict] = {}


def register(mcp: object) -> None:
    from fastmcp import FastMCP
    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_plan_begin(task: str) -> str:
        """Start a new plan with the given task description. Returns a plan_id that subsequent plan tools use."""
        plan_id = f"plan_{uuid.uuid4().hex[:8]}"
        _plans[plan_id] = {
            "id": plan_id,
            "task": task,
            "steps": [],
            "status": "planning",
            "created_at": time.time(),
        }
        return json.dumps({"plan_id": plan_id, "task": task, "status": "planning"})

    @mcp.tool()
    def harness_plan_step(plan_id: str, intent: str, depends_on: str = "") -> str:
        """Add a step to an existing plan.

        Args:
            plan_id: The plan ID from harness_plan_begin
            intent: What this step should accomplish
            depends_on: Comma-separated step IDs or intent descriptions this step depends on (optional)
        """
        plan = _plans.get(plan_id)
        if not plan:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        if plan["status"] != "planning":
            return json.dumps({"error": f"Plan {plan_id} is already {plan['status']}, cannot add steps"})

        step_id = f"step_{len(plan['steps']) + 1}"
        deps = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else []
        step = {"id": step_id, "intent": intent, "depends_on": deps, "status": "pending"}
        plan["steps"].append(step)
        return json.dumps({"plan_id": plan_id, "step_id": step_id, "intent": intent, "status": "added"})

    @mcp.tool()
    def harness_plan_finalize(plan_id: str) -> str:
        """Lock a plan and compute the execution graph. No more steps can be added after finalization."""
        plan = _plans.get(plan_id)
        if not plan:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        if not plan["steps"]:
            return json.dumps({"error": "Cannot finalize a plan with no steps"})

        plan["status"] = "finalized"
        summary = {
            "plan_id": plan_id,
            "task": plan["task"],
            "steps": len(plan["steps"]),
            "status": "finalized",
            "step_list": [
                {"id": s["id"], "intent": s["intent"], "depends_on": s["depends_on"]}
                for s in plan["steps"]
            ],
        }
        return json.dumps(summary, indent=2)

    @mcp.tool()
    def harness_plan_execute(plan_id: str, workdir: str = "", max_parallel: int = 2, timeout: int = 300) -> str:
        """Execute a finalized plan. Steps run in dependency order with parallelizable steps running concurrently.

        Args:
            plan_id: The finalized plan ID
            workdir: Working directory for subagents
            max_parallel: Maximum parallel subagents
            timeout: Per-step timeout in seconds
        """
        plan = _plans.get(plan_id)
        if not plan:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        if plan["status"] != "finalized":
            return json.dumps({"error": f"Plan {plan_id} must be finalized first (current: {plan['status']})"})

        plan["status"] = "executing"
        start_time = time.time()

        completed: dict[str, dict] = {}
        results = []

        steps = plan["steps"][:]
        while steps:
            batch = []
            remaining = []
            for s in steps:
                deps = set(s["depends_on"])
                if deps and not deps.intersection({sid for sid in completed}):
                    remaining.append(s)
                else:
                    batch.append(s)

            if not batch and remaining:
                return json.dumps({"error": "Circular dependency detected", "remaining": [s["id"] for s in remaining]})

            batch_results = asyncio.run(_run_batch(batch, workdir, max_parallel, timeout))

            for s, r in zip(batch, batch_results):
                completed[s["id"]] = r
                s["status"] = "done"
                results.append({"step_id": s["id"], "intent": s["intent"], **r})

            steps = remaining

        duration = time.time() - start_time
        total_cost = sum(r.get("cost", 0) for r in results)
        total_tokens = {"total": 0, "input": 0, "output": 0}
        for r in results:
            for k in total_tokens:
                total_tokens[k] += r.get("tokens", {}).get(k, 0)

        plan["status"] = "done"
        return json.dumps({
            "plan_id": plan_id,
            "task": plan["task"],
            "steps_executed": len(results),
            "total_duration_s": round(duration, 2),
            "total_cost": round(total_cost, 6),
            "total_tokens": total_tokens,
            "errors": sum(1 for r in results if r.get("error")),
            "step_results": results,
        }, indent=2)

    @mcp.tool()
    def harness_plan_status(plan_id: str) -> str:
        """Inspect a plan's current state — created steps, execution progress, and results."""
        plan = _plans.get(plan_id)
        if not plan:
            return json.dumps({"error": f"Plan {plan_id} not found"})

        step_status = []
        for s in plan["steps"]:
            step_status.append({"id": s["id"], "intent": s["intent"], "status": s.get("status", "pending"), "depends_on": s["depends_on"]})
        return json.dumps({
            "plan_id": plan["id"],
            "task": plan["task"],
            "status": plan["status"],
            "steps": len(plan["steps"]),
            "step_list": step_status,
        }, indent=2)

    @mcp.tool()
    def harness_plan_abort(plan_id: str) -> str:
        """Cancel a plan that is still in planning or executing state."""
        plan = _plans.get(plan_id)
        if not plan:
            return json.dumps({"error": f"Plan {plan_id} not found"})
        if plan["status"] in ("done",):
            return json.dumps({"error": f"Plan {plan_id} is already done"})

        plan["status"] = "aborted"
        return json.dumps({"plan_id": plan_id, "status": "aborted"})


async def _run_batch(batch: list[dict], workdir: str, max_parallel: int, timeout: int) -> list[dict]:
    sem = asyncio.Semaphore(max_parallel)

    async def _exec(s: dict) -> dict:
        async with sem:
            return await _run_step(s["intent"], workdir, timeout)

    tasks = [_exec(s) for s in batch]
    return await asyncio.gather(*tasks, return_exceptions=True)


async def _run_step(intent: str, workdir: str, timeout: int) -> dict:
    cmd = find_opencode()
    cwd = safe_workdir(workdir)
    args = [cmd, "run", "--format", "json", intent, "--dangerously-skip-permissions"]

    env = os.environ.copy()
    env["OPENCODE_DISABLE_AUTOCOMPACT"] = "1"

    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"timed out ({timeout}s)", "duration_s": round(time.time() - start, 2)}
    except Exception as e:
        return {"error": str(e), "duration_s": round(time.time() - start, 2)}

    duration = time.time() - start
    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""

    from ..events import parse_events
    transcript = parse_events(stdout)

    text_parts = []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "text":
            text_parts.append(ev.get("part", {}).get("text", ""))

    return {
        "text": "\n".join(text_parts) if text_parts else "(no text output)",
        "session_id": transcript.session_id,
        "tool_calls": len(transcript.tool_calls),
        "tokens": dict(transcript.total_tokens),
        "cost": transcript.total_cost,
        "duration_s": round(duration, 2),
        "returncode": proc.returncode,
        "error": stderr[:500] if stderr else None,
    }
