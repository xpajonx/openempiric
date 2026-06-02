from __future__ import annotations

import json
import time

from ..client import run, run_json


def register(mcp: object) -> None:
    from fastmcp import FastMCP
    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_run_opencode(
        prompt: str,
        workdir: str = "",
        timeout: int = 300,
        dangerously_skip_permissions: bool = True,
    ) -> str:
        """Run opencode with a prompt and return the response. Use this to delegate tasks to child opencode sessions — refactoring, testing, research — and get results back."""
        result = run(prompt=prompt, workdir=workdir, timeout=timeout, dangerously_skip_permissions=dangerously_skip_permissions)
        return result.text

    @mcp.tool()
    def harness_run_tasks(tasks: str, workdir: str = "") -> str:
        """Run multiple independent opencode tasks sequentially and return all results. Useful for parallelizable work like 'fix lint errors AND add tests' where each task is independent.

        Args:
            tasks: JSON array of task objects, each with a 'prompt' field. Optionally 'timeout' (default 120s).
            Example: [{"prompt": "fix lint in src/main.py"}, {"prompt": "write tests for utils.py", "timeout": 60}]
            workdir: Project directory (default: current dir)
        """
        try:
            parsed = json.loads(tasks)
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON: {e}"
        if not isinstance(parsed, list):
            return "Error: tasks must be a JSON array"

        results = []
        for i, task in enumerate(parsed):
            prompt = task.get("prompt", "")
            if not prompt:
                results.append(f"Task {i}: skipped (no prompt)")
                continue
            t = task.get("timeout", 120)
            label = task.get("label", f"Task {i}")
            start = time.time()
            out = run(prompt=prompt, workdir=workdir, timeout=t)
            elapsed = time.time() - start
            results.append(f"[{label}] ({elapsed:.1f}s)\n{out.text}")

        return "\n---\n".join(results)
