from __future__ import annotations

import json
import os
import subprocess
import time

from ..client import find_opencode, safe_workdir, run_json


def register(mcp: object) -> None:
    from fastmcp import FastMCP

    if not isinstance(mcp, FastMCP):
        return

    @mcp.tool()
    def harness_session_run_json(
        prompt: str,
        workdir: str = "",
        timeout: int = 300,
        dangerously_skip_permissions: bool = True,
    ) -> str:
        """Run a prompt and return structured JSON with session_id, tokens, tool calls, costs, and text output.

        Use this when you need visibility into the agent's tool usage, token consumption, or per-step costs.
        Returns a JSON object with:
          - session_id: the session identifier
          - text: human-readable response
          - tool_calls: list of tool invocations (tool name, status, input, output, duration_ms)
          - tokens: {total, input, output, reasoning, cache_write, cache_read}
          - cost: total cost
          - steps: number of completion steps
          - duration_s: wall-clock seconds
        """
        result = run_json(
            prompt=prompt,
            workdir=workdir,
            timeout=timeout,
            dangerously_skip_permissions=dangerously_skip_permissions,
        )
        return json.dumps(
            {
                "session_id": result.session_id,
                "text": result.text,
                "tool_calls": [
                    {
                        "tool": t.tool,
                        "status": t.status,
                        "title": t.title,
                        "duration_ms": t.duration_ms,
                        "truncated": t.truncated,
                        "exit_code": t.exit_code,
                    }
                    for t in result.transcript.tool_calls
                ],
                "tokens": result.transcript.total_tokens,
                "cost": result.transcript.total_cost,
                "steps": len(result.transcript.steps),
                "duration_s": round(result.duration_s, 2),
            },
            indent=2,
        )

    @mcp.tool()
    def harness_session_create(workdir: str = "") -> str:
        """Create a new opencode session and return its session_id. Starts with a simple initialization prompt to establish the session."""
        result = run_json(prompt="initialize session", workdir=workdir, timeout=30)
        if not result.session_id:
            return json.dumps(
                {"error": "Failed to create session", "text": result.text}
            )
        return json.dumps({"session_id": result.session_id})

    @mcp.tool()
    def harness_session_prompt(
        session_id: str, prompt: str, workdir: str = "", timeout: int = 300
    ) -> str:
        """Continue an existing session with a new prompt. Returns structured JSON with tokens, tool calls, text."""
        cmd = find_opencode()
        cwd = safe_workdir(workdir)
        args = [
            cmd,
            "run",
            "--format",
            "json",
            "--continue",
            "--session",
            session_id,
            prompt,
        ]

        env = os.environ.copy()
        env["OPENCODE_DISABLE_AUTOCOMPACT"] = "1"

        start = time.time()
        try:
            result = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
            )
        except subprocess.TimeoutExpired:
            return json.dumps(
                {"error": f"Timed out after {timeout}s", "session_id": session_id}
            )
        except FileNotFoundError:
            return json.dumps(
                {"error": "opencode CLI not found", "session_id": session_id}
            )
        except Exception as e:
            return json.dumps({"error": str(e), "session_id": session_id})

        time.time() - start

        from ..events import parse_events

        transcript = parse_events(result.stdout)
        parts = []
        for line in result.stdout.strip().splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "text":
                parts.append(ev.get("part", {}).get("text", ""))

        return json.dumps(
            {
                "session_id": transcript.session_id or session_id,
                "text": "\n".join(parts) if parts else "(no output)",
                "tool_calls": [
                    {
                        "tool": t.tool,
                        "status": t.status,
                        "title": t.title,
                        "duration_ms": t.duration_ms,
                    }
                    for t in transcript.tool_calls
                ],
                "tokens": transcript.total_tokens,
                "cost": transcript.total_cost,
                "steps": len(transcript.steps),
                "duration_s": round(time.time() - start, 2),
            },
            indent=2,
        )

    @mcp.tool()
    def harness_session_list() -> str:
        """List all opencode sessions with their IDs, titles, and timestamps."""
        cmd = find_opencode()
        try:
            result = subprocess.run(
                [cmd, "session", "list"], capture_output=True, text=True, timeout=15
            )
        except Exception as e:
            return f"Error: {e}"
        return result.stdout.strip() or "(no sessions)"

    @mcp.tool()
    def harness_session_export(session_id: str, sanitize: bool = False) -> str:
        """Export a session's data as JSON. Use sanitize=True to redact sensitive data."""
        cmd = find_opencode()
        args = [cmd, "export", session_id]
        if sanitize:
            args.append("--sanitize")
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        except Exception as e:
            return f"Error: {e}"
        return result.stdout.strip() or result.stderr.strip() or "(empty)"

    @mcp.tool()
    def harness_session_fork(session_id: str, workdir: str = "") -> str:
        """Fork an existing session into a new independent session."""
        cmd = find_opencode()
        cwd = safe_workdir(workdir)
        args = [
            cmd,
            "run",
            "--format",
            "json",
            "--fork",
            "--session",
            session_id,
            "fork session",
        ]

        env = os.environ.copy()
        env["OPENCODE_DISABLE_AUTOCOMPACT"] = "1"

        try:
            result = subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=30, env=env
            )
        except Exception as e:
            return json.dumps({"error": str(e)})

        from ..events import parse_events

        transcript = parse_events(result.stdout)
        return json.dumps({"session_id": transcript.session_id})
