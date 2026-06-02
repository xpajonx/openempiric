from __future__ import annotations

import json
import time

from ..client import run, run_json
from ..config import load_harness_config


def register(mcp: object) -> None:
    from fastmcp import FastMCP
    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_run_opencode(
        prompt: str,
        workdir: str = "",
        timeout: int = 300,
    ) -> str:
        """Run opencode with a prompt and return the response. Use this to delegate tasks to child opencode sessions — refactoring, testing, research — and get results back."""
        cfg = load_harness_config()
        dangerously_skip_permissions = cfg.get("dangerously_skip_permissions", True)
        result = run_json(prompt=prompt, workdir=workdir, timeout=timeout, dangerously_skip_permissions=dangerously_skip_permissions)
        
        telemetry_info = ""
        try:
            from harness_knowledge.engine import KnowledgeEngine
            eng = KnowledgeEngine(workdir or None)
            total_tool_calls = sum(1 for e in result.transcript.events if e.type == "tool_use")
            telemetry = {
                "duration_sec": int(result.duration_s),
                "total_tool_calls": total_tool_calls
            }
            eng.session_commit(
                project=workdir or None,
                conversation_text=result.text,
                session_id=result.session_id,
                telemetry=telemetry
            )
            telemetry_info = f"\n\n[Telemetry: session {result.session_id} ingested ({int(result.duration_s)}s, {total_tool_calls} tools)]"
        except ImportError:
            pass
        except Exception as e:
            telemetry_info = f"\n\n[Telemetry Error: {e}]"
            
        return result.text + telemetry_info

    @mcp.tool()
    def harness_run_tasks(tasks: str, workdir: str = "", parallel: bool = False) -> str:
        """Run multiple independent opencode tasks (sequentially or in parallel) and return all results. Useful for parallelizable work like 'fix lint errors AND add tests' where each task is independent.

        Args:
            tasks: JSON array of task objects, each with a 'prompt' field. Optionally 'timeout' (default 120s).
            Example: [{"prompt": "fix lint in src/main.py"}, {"prompt": "write tests for utils.py", "timeout": 60}]
            workdir: Project directory (default: current dir)
            parallel: Run tasks concurrently in parallel
        """
        try:
            parsed = json.loads(tasks)
        except json.JSONDecodeError as e:
            return f"Error: invalid JSON: {e}"
        if not isinstance(parsed, list):
            return "Error: tasks must be a JSON array"

        cfg = load_harness_config()
        if parallel:
            import asyncio
            sem = asyncio.Semaphore(cfg.get("max_parallel_tasks", 4))

            async def _run_task_async(task: dict, idx: int) -> str:
                async with sem:
                    prompt = task.get("prompt", "")
                if not prompt:
                    return f"Task {idx}: skipped (no prompt)"
                t = task.get("timeout", 120)
                label = task.get("label", f"Task {idx}")
                dangerously_skip = cfg.get("dangerously_skip_permissions", True)
                start = time.time()
                out = await asyncio.to_thread(run_json, prompt=prompt, workdir=workdir, timeout=t, dangerously_skip_permissions=dangerously_skip)
                elapsed = time.time() - start
                
                try:
                    from harness_knowledge.engine import KnowledgeEngine
                    eng = KnowledgeEngine(workdir or None)
                    eng.session_commit(
                        project=workdir or None,
                        conversation_text=out.text,
                        session_id=out.session_id,
                        telemetry={"duration_sec": int(out.duration_s), "total_tool_calls": sum(1 for e in out.transcript.events if e.type == "tool_use")}
                    )
                except Exception:
                    pass
                
                return f"[{label}] ({elapsed:.1f}s)\n{out.text}"

            async def _run_all() -> list[str]:
                coros = [_run_task_async(task, i) for i, task in enumerate(parsed)]
                return await asyncio.gather(*coros)

            results = asyncio.run(_run_all())
        else:
            results = []
            for i, task in enumerate(parsed):
                prompt = task.get("prompt", "")
                if not prompt:
                    results.append(f"Task {i}: skipped (no prompt)")
                    continue
                t = task.get("timeout", 120)
                label = task.get("label", f"Task {i}")
                dangerously_skip = cfg.get("dangerously_skip_permissions", True)
                start = time.time()
                out = run_json(prompt=prompt, workdir=workdir, timeout=t, dangerously_skip_permissions=dangerously_skip)
                elapsed = time.time() - start
                
                try:
                    from harness_knowledge.engine import KnowledgeEngine
                    eng = KnowledgeEngine(workdir or None)
                    eng.session_commit(
                        project=workdir or None,
                        conversation_text=out.text,
                        session_id=out.session_id,
                        telemetry={"duration_sec": int(out.duration_s), "total_tool_calls": sum(1 for e in out.transcript.events if e.type == "tool_use")}
                    )
                except Exception:
                    pass
                
                results.append(f"[{label}] ({elapsed:.1f}s)\n{out.text}")

        return "\n---\n".join(results)
