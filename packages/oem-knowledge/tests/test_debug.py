from __future__ import annotations

import sys
from types import SimpleNamespace

from oem_knowledge import debug


def test_oem_debug_waits_and_forwards_cli_args(monkeypatch):
    calls = []

    fake_debugpy = SimpleNamespace(
        listen=lambda address: calls.append(("listen", address)),
        wait_for_client=lambda: calls.append(("wait", None)),
        log_to=lambda path: calls.append(("log_to", path)),
    )
    monkeypatch.setitem(sys.modules, "debugpy", fake_debugpy)

    def target():
        calls.append(("target", tuple(sys.argv)))

    debug._run_debug_target(
        ["--listen", "localhost:4321", "--log-to", "/tmp/oem-debug", "doctor", "--json"],
        program_name="oem-debug",
        target_argv0="oem",
        target_label="oem",
        target=target,
    )

    assert calls == [
        ("log_to", "/tmp/oem-debug"),
        ("listen", ("localhost", 4321)),
        ("wait", None),
        ("target", ("oem", "doctor", "--json")),
    ]


def test_oem_debug_no_wait_skips_wait_for_client(monkeypatch):
    calls = []

    fake_debugpy = SimpleNamespace(
        listen=lambda address: calls.append(("listen", address)),
        wait_for_client=lambda: calls.append(("wait", None)),
    )
    monkeypatch.setitem(sys.modules, "debugpy", fake_debugpy)

    debug._run_debug_target(
        ["--listen", "8765", "--no-wait", "health"],
        program_name="oem-debug",
        target_argv0="oem",
        target_label="oem",
        target=lambda: calls.append(("target", tuple(sys.argv))),
    )

    assert calls == [
        ("listen", ("127.0.0.1", 8765)),
        ("target", ("oem", "health")),
    ]
