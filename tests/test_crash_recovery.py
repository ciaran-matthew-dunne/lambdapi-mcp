"""LSP subprocess crash recovery: if `lambdapi lsp` dies, the next tool
call should transparently respawn it rather than error forever."""

import os
import signal
import time

import pytest

from lambdapi_mcp import tools


def _sigkill_lsp(lsp):
    """Kill the underlying lambdapi lsp subprocess with SIGKILL."""
    proc = lsp._proc
    assert proc is not None
    os.kill(proc.pid, signal.SIGKILL)
    # Give the OS + our reader threads a moment to notice.
    for _ in range(50):
        if proc.poll() is not None:
            break
        time.sleep(0.02)


def test_recovers_after_lsp_crash(lsp, fixture_path):
    # Sanity call
    r = tools.tool_check(lsp, fixture_path("simple.lp"))
    assert r["ok"] is True
    baseline = lsp.restart_count

    _sigkill_lsp(lsp)

    # Next call must succeed — the client should notice the dead proc,
    # spawn a fresh `lambdapi lsp`, and re-issue the request.
    r = tools.tool_check(lsp, fixture_path("simple.lp"))
    assert r["ok"] is True, f"expected recovery, got {r}"
    assert lsp.restart_count == baseline + 1, (
        "expected the client to record one restart"
    )

    # And a second call still works (state is clean).
    r = tools.tool_symbols(lsp, fixture_path("simple.lp"))
    names = {s["name"] for s in r["symbols"]}
    assert "double" in names
