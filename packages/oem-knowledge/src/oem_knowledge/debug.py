from __future__ import annotations

import argparse
import importlib
import os
import sys
from collections.abc import Callable, Sequence


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5678


def _parse_listen(value: str) -> tuple[str, int]:
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        return host or DEFAULT_HOST, int(port_text)
    return DEFAULT_HOST, int(value)


def _run_debug_target(
    argv: Sequence[str] | None,
    *,
    program_name: str,
    target_argv0: str,
    target_label: str,
    target: Callable[[], object],
) -> object:
    parser = argparse.ArgumentParser(
        prog=program_name,
        description=f"Run {target_label} under debugpy with no launch config.",
    )
    parser.add_argument(
        "--listen",
        default=os.environ.get("OEM_DEBUG_LISTEN", f"{DEFAULT_HOST}:{DEFAULT_PORT}"),
        metavar="[HOST:]PORT",
        help="debugpy listen address. Defaults to 127.0.0.1:5678.",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Start immediately instead of waiting for a debugger to attach.",
    )
    parser.add_argument(
        "--log-to",
        metavar="PATH",
        help="Enable debugpy logging to PATH.",
    )
    args, target_args = parser.parse_known_args(argv)

    try:
        debugpy = importlib.import_module("debugpy")
    except ImportError as exc:  # pragma: no cover - exercised through CLI behavior
        raise SystemExit(
            "debugpy is not installed. Reinstall oem-knowledge with dependencies "
            "or run in an environment that provides debugpy."
        ) from exc

    host, port = _parse_listen(args.listen)
    if args.log_to:
        debugpy.log_to(args.log_to)
    debugpy.listen((host, port))
    print(f"Waiting for debugger on {host}:{port}", file=sys.stderr)
    if not args.no_wait:
        debugpy.wait_for_client()

    sys.argv = [target_argv0, *target_args]
    return target()


def main(argv: Sequence[str] | None = None) -> object:
    from oem_knowledge.cli import main as cli_main

    return _run_debug_target(
        argv,
        program_name="oem-debug",
        target_argv0="oem",
        target_label="oem",
        target=cli_main,
    )


def server(argv: Sequence[str] | None = None) -> object:
    from oem_knowledge.server import main as server_main

    return _run_debug_target(
        argv,
        program_name="oem-debug-server",
        target_argv0="oem-server",
        target_label="the OpenEmpiric MCP server",
        target=server_main,
    )
