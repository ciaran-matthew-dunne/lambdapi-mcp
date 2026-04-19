"""LSP client for ``lambdapi lsp`` over stdio JSON-RPC.

This is the single substrate every MCP tool layers on. Responsibilities:

- spawn the ``lambdapi lsp`` subprocess and manage its lifetime
- read/write framed JSON-RPC messages on its stdio
- route responses back to the caller (by request id) and notifications
  to a queue (diagnostics, window/logMessage, …)
- expose the standard LSP methods the tools need, plus the custom
  ``proof/goals`` request

The client is synchronous: each ``request`` call blocks until the
server replies (or times out). MCP tools are already serialised per
session, so we don't need concurrent request handling.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time


class LSPError(Exception):
    """Raised when the server returns an error, times out, or crashes."""


class _DocSession:
    """State for one ``open_doc`` context: the drained notifications
    from the did_open cycle, plus a convenience view on diagnostics."""

    def __init__(self, client: "LSPClient", uri: str) -> None:
        self._client = client
        self._uri = uri
        self.notifications: list[dict] = []

    @property
    def diagnostics(self) -> list[dict]:
        return self._client.latest_diagnostics(
            self.notifications, uri=self._uri
        )


class LSPClient:
    """A running ``lambdapi lsp`` subprocess."""

    def __init__(
        self,
        lib_root: str,
        map_dirs: list[str] | None = None,
        binary: str | None = None,
        log_file: str | None = None,
        timeout: float = 30.0,
    ):
        self.lib_root = lib_root
        self.map_dirs = list(map_dirs or [])
        self.log_file = log_file
        self.binary = binary or shutil.which("lambdapi")
        if not self.binary:
            raise LSPError("lambdapi binary not found on PATH")
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._stderr: list[str] = []
        self._notifications: queue.Queue = queue.Queue()
        self._pending: dict[int, queue.Queue] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._stop = threading.Event()
        self.restart_count = 0

    # --- Process lifecycle --------------------------------------------

    def _reset_state(self) -> None:
        """Reset in-memory state before a fresh spawn."""
        self._stderr = []
        self._notifications = queue.Queue()
        self._pending = {}
        self._next_id = 1
        self._stop = threading.Event()

    def start(self) -> None:
        self._reset_state()
        cmd = [
            self.binary, "lsp",
            "--standard-lsp",
            f"--lib-root={self.lib_root}",
        ]
        for md in self.map_dirs:
            cmd.append(f"--map-dir={md}")
        if self.log_file:
            cmd.append(f"--log-file={self.log_file}")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.initialize()

    def _is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _restart(self) -> None:
        """Kill the (possibly dead) subprocess and start a new one.

        Opened documents are not restored — every tool does did_open at
        the start of its own request, so restart is safe."""
        with self._restart_lock:
            if self._is_alive():
                return  # another thread already restarted
            try:
                self.stop()
            except Exception:
                pass
            self.start()
            self.restart_count += 1

    def stop(self) -> None:
        self._stop.set()
        if not self._proc:
            return
        if self._proc.poll() is None:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=1)
        for pipe in (self._proc.stdout, self._proc.stderr, self._proc.stdin):
            try:
                pipe and pipe.close()
            except Exception:
                pass

    def __enter__(self) -> "LSPClient":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # --- Framing ------------------------------------------------------

    def _write(self, msg: dict) -> None:
        body = json.dumps(msg).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        buf = b""
        while not self._stop.is_set():
            while b"\r\n\r\n" not in buf:
                chunk = self._proc.stdout.read(1)
                if not chunk:
                    return
                buf += chunk
            header, _, buf = buf.partition(b"\r\n\r\n")
            size = None
            for line in header.decode().splitlines():
                if line.lower().startswith("content-length:"):
                    size = int(line.split(":", 1)[1].strip())
            if size is None:
                return
            while len(buf) < size:
                chunk = self._proc.stdout.read(size - len(buf))
                if not chunk:
                    return
                buf += chunk
            body, buf = buf[:size], buf[size:]
            try:
                msg = json.loads(body.decode())
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                q = self._pending.pop(msg["id"], None)
                if q is not None:
                    q.put(msg)
            else:
                self._notifications.put(msg)

    def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr.append(line.decode(errors="replace").rstrip())

    # --- Requests / notifications -------------------------------------

    def _alloc_request(self, method: str, params: dict | None):
        """Allocate id + reply queue under the lock, return (mid, queue, payload)."""
        with self._lock:
            mid = self._next_id
            self._next_id += 1
            reply: queue.Queue = queue.Queue(maxsize=1)
            self._pending[mid] = reply
        payload = {
            "jsonrpc": "2.0", "id": mid,
            "method": method, "params": params or {},
        }
        return mid, reply, payload

    def request(self, method: str, params: dict | None = None):
        if not self._is_alive():
            self._restart()
        mid, reply, payload = self._alloc_request(method, params)
        try:
            self._write(payload)
        except (BrokenPipeError, OSError):
            # Send failed; the old `mid` and its reply queue were wiped
            # by _restart (via _reset_state), so re-allocate and retry.
            self._restart()
            mid, reply, payload = self._alloc_request(method, params)
            self._write(payload)
        # Poll the reply queue in short slices so we can notice a dead
        # subprocess well before [self.timeout] elapses.
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._pending.pop(mid, None)
                raise LSPError(
                    f"timeout waiting for {method} (id={mid}); "
                    f"stderr tail: {self._stderr[-5:]}"
                )
            try:
                msg = reply.get(timeout=min(0.5, remaining))
                break
            except queue.Empty:
                if not self._is_alive():
                    self._pending.pop(mid, None)
                    raise LSPError(
                        f"{method}: lambdapi lsp died while waiting "
                        f"(stderr tail: {self._stderr[-5:]})"
                    )
        if "error" in msg:
            raise LSPError(f"{method}: {msg['error']}")
        return msg.get("result")

    def notify(self, method: str, params: dict | None = None) -> None:
        if not self._is_alive():
            self._restart()
        payload = {
            "jsonrpc": "2.0",
            "method": method, "params": params or {},
        }
        try:
            self._write(payload)
        except (BrokenPipeError, OSError):
            self._restart()
            self._write(payload)

    def drain_notifications(self, timeout: float = 3.0) -> list[dict]:
        """Collect notifications until [timeout] seconds of silence."""
        out: list[dict] = []
        try:
            while True:
                msg = self._notifications.get(timeout=timeout)
                out.append(msg)
                timeout = 0.2
        except queue.Empty:
            pass
        return out

    # --- High-level helpers ------------------------------------------

    def initialize(self) -> dict | None:
        result = self.request("initialize", {"capabilities": {}})
        self.notify("initialized", {})
        return result

    def did_open(
        self,
        uri: str,
        text: str,
        language_id: str = "lp",
        version: int = 1,
    ) -> None:
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri, "languageId": language_id,
                "version": version, "text": text,
            },
        })

    def did_close(self, uri: str) -> None:
        self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})

    @contextlib.contextmanager
    def open_doc(self, uri: str, text: str, drain_timeout: float = 5.0):
        """Context manager: did_open → drain diagnostics → yield a
        ``DocSession`` (so ``session.diagnostics`` / ``.notifications``
        are available inside or after the block) → did_close on exit.

        Replaces the did_open / try / drain / did_close boilerplate every
        position-taking tool needs."""
        self.did_open(uri, text)
        session = _DocSession(self, uri)
        try:
            session.notifications = self.drain_notifications(
                timeout=drain_timeout
            )
            yield session
        finally:
            self.did_close(uri)

    def hover(self, uri: str, line: int, character: int):
        return self.request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    def definition(self, uri: str, line: int, character: int):
        return self.request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    def document_symbol(self, uri: str):
        return self.request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri},
        })

    def goals(self, uri: str, line: int, character: int):
        return self.request("proof/goals", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
        })

    # --- Diagnostic helpers -------------------------------------------

    @staticmethod
    def latest_diagnostics(
        notifications: list[dict], uri: str
    ) -> list[dict]:
        """Return the final publishDiagnostics for [uri].

        LSP specifies each publishDiagnostics supersedes the previous for
        its URI, so only the last one is authoritative.
        """
        latest: list[dict] = []
        for msg in notifications:
            if msg.get("method") != "textDocument/publishDiagnostics":
                continue
            if msg.get("params", {}).get("uri") != uri:
                continue
            latest = msg["params"].get("diagnostics", [])
        return latest


def default_lib_root() -> str:
    """Locate the opam-installed Lambdapi stdlib root."""
    env = os.environ.get("LAMBDAPI_LIB_ROOT")
    if env and os.path.isdir(env):
        return env
    default = os.path.expanduser("~/.opam/default/lib/lambdapi/lib_root")
    if os.path.isdir(default):
        return default
    return os.getcwd()


def file_uri(path: str) -> str:
    return "file://" + os.path.abspath(path)
