from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opencode_ai import AsyncOpencode

from .events import SessionTranscript, parse_events


@dataclass
class RunResult:
    text: str
    transcript: SessionTranscript = field(default_factory=lambda: SessionTranscript(session_id=""))
    session_id: str = ""
    duration_s: float = 0.0
    returncode: int | None = None
    error: str | None = None


def find_opencode() -> str:
    for candidate in ["opencode", "/usr/local/bin/opencode"]:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return "opencode"


def safe_workdir(workdir: str | None) -> str:
    if workdir and Path(workdir).is_dir():
        return workdir
    return os.getcwd()


def create_async_client(server_url: str | None = None) -> AsyncOpencode:
    url = server_url or os.environ.get("OPENCODE_BASE_URL", "http://localhost:4096")
    return AsyncOpencode(base_url=url)


def run(prompt: str, workdir: str = "", timeout: int = 300, dangerously_skip_permissions: bool = True) -> RunResult:
    cmd = find_opencode()
    cwd = safe_workdir(workdir)
    args = [cmd, "run", prompt]
    if dangerously_skip_permissions:
        args.append("--dangerously-skip-permissions")

    env = os.environ.copy()
    env["OPENCODE_DISABLE_AUTOCOMPACT"] = "1"

    start = time.time()
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return RunResult(text=f"Error: Timed out after {timeout}s", duration_s=time.time() - start, returncode=-1, error="timeout")
    except FileNotFoundError:
        return RunResult(text=f"Error: opencode CLI not found at {cmd}", duration_s=time.time() - start, returncode=-1, error="not_found")
    except Exception as e:
        return RunResult(text=f"Error: {e}", duration_s=time.time() - start, returncode=-1, error=str(e))

    duration = time.time() - start
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")

    return RunResult(
        text="\n".join(parts) if parts else "(no output)",
        duration_s=duration,
        returncode=result.returncode,
    )


def run_json(prompt: str, workdir: str = "", timeout: int = 300, dangerously_skip_permissions: bool = True) -> RunResult:
    cmd = find_opencode()
    cwd = safe_workdir(workdir)
    args = [cmd, "run", "--format", "json", prompt]
    if dangerously_skip_permissions:
        args.append("--dangerously-skip-permissions")

    env = os.environ.copy()
    env["OPENCODE_DISABLE_AUTOCOMPACT"] = "1"

    start = time.time()
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return RunResult(text=f"Error: Timed out after {timeout}s", duration_s=time.time() - start, returncode=-1, error="timeout")
    except FileNotFoundError:
        return RunResult(text=f"Error: opencode CLI not found at {cmd}", duration_s=time.time() - start, returncode=-1, error="not_found")
    except Exception as e:
        return RunResult(text=f"Error: {e}", duration_s=time.time() - start, returncode=-1, error=str(e))

    duration = time.time() - start
    transcript = parse_events(result.stdout)
    stderr_text = result.stderr.strip()

    parts = []
    for line in result.stdout.strip().splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "text":
            parts.append(ev.get("part", {}).get("text", ""))
        elif t == "tool_use":
            state = ev.get("part", {}).get("state", {})
            if state.get("status") == "error":
                parts.append(f"[tool error: {ev['part']['tool']}]")

    if stderr_text:
        parts.append(f"[stderr]\n{stderr_text}")

    return RunResult(
        text="\n".join(parts) if parts else "(no output)",
        transcript=transcript,
        session_id=transcript.session_id,
        duration_s=duration,
        returncode=result.returncode,
    )
