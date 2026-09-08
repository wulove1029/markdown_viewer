"""Out-of-process Markdown rendering so a huge parse stays killable.

Why a separate process and not another thread: ``app/md_converter.py`` holds
``_CONVERT_LOCK`` for the whole ``markdown-it`` render, and a running
``md.render()`` call cannot be interrupted from Python.  The measured effect
(``docs/upgrades/2026-09-08-next-baseline.md``) is that opening a 100 KB note
while a 5 MB document is being parsed waits for the big parse to finish
(p50 3.2 s).  Dropping the stale callback would not fix that -- the CPU and
the lock are still held -- so the expensive parse runs in a child process that
can simply be killed when its request goes stale.

Transport: a loopback TCP socket with a random 32-byte token handshake and
length-prefixed pickle frames.  Chosen over ``multiprocessing`` because the
frozen build is a windowed (``console=False``) PyInstaller app: spawn would
re-import ``main`` in the child, and stdio pipes are not reliable there, while
a socket is.  The child is launched as ``python -m app.render_worker`` from
source and as ``MarkdownViewer.exe --render-worker`` when frozen.

Only the parent process caches results; the child is stateless per request.
"""

from __future__ import annotations

import atexit
import hmac
import logging
import os
import pickle
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Set to "1" to force every conversion back in-process (tests, triage).
DISABLE_ENV = "MDV_DISABLE_RENDER_SUBPROCESS"

_HEADER = struct.Struct("!Q")
_TOKEN_BYTES = 32
_MAX_FRAME_BYTES = 256 * 1024 * 1024
_HANDSHAKE_TIMEOUT_S = 30.0
_REQUEST_TIMEOUT_S = 180.0
_POLL_S = 0.02
# Recycle a worker after this many documents so a long session cannot grow the
# child's peak RSS without bound (a fresh child costs ~0.5 s, paid in the
# background right after the previous request finished).
_MAX_REQUESTS_PER_WORKER = 12


class RenderWorkerError(RuntimeError):
    """The child process failed; the caller should fall back in-process."""


class RenderWorkerCancelled(RuntimeError):
    """The request was cancelled and the child process was killed."""


# --------------------------------------------------------------------------
# framing
# --------------------------------------------------------------------------


def send_frame(sock: socket.socket, payload: object) -> None:
    data = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    sock.sendall(_HEADER.pack(len(data)) + data)


def _recv_exactly(sock: socket.socket, size: int, cancel, deadline: float) -> bytes:
    """Read *size* bytes, polling *cancel* and the deadline between reads."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        if cancel is not None and cancel.is_set():
            raise RenderWorkerCancelled()
        if time.monotonic() > deadline:
            raise RenderWorkerError("render worker timed out")
        try:
            chunk = sock.recv(min(remaining, 1 << 20))
        except socket.timeout:
            continue
        except OSError as exc:
            raise RenderWorkerError(f"render worker socket error: {exc}") from exc
        if not chunk:
            raise RenderWorkerError("render worker closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket, cancel=None, deadline: float | None = None):
    if deadline is None:
        deadline = time.monotonic() + _REQUEST_TIMEOUT_S
    (size,) = _HEADER.unpack(_recv_exactly(sock, _HEADER.size, cancel, deadline))
    if size > _MAX_FRAME_BYTES:
        raise RenderWorkerError(f"render worker frame too large: {size}")
    return pickle.loads(_recv_exactly(sock, size, cancel, deadline))


# --------------------------------------------------------------------------
# worker handle
# --------------------------------------------------------------------------


class _Worker:
    def __init__(self, proc: subprocess.Popen, sock: socket.socket):
        self.proc = proc
        self.sock = sock
        self.requests = 0

    def kill(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:  # pragma: no cover - process already reaped
            pass


def _worker_command(port: int, token_hex: str) -> tuple[list[str], dict]:
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env[DISABLE_ENV] = "1"  # a child never spawns grandchildren
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--render-worker", str(port), token_hex], env
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return (
        [sys.executable, "-X", "utf8", "-m", "app.render_worker", str(port), token_hex],
        env,
    )


class RenderService:
    """Pool of at most one idle child process, plus any currently busy ones."""

    def __init__(self):
        self._lock = threading.Lock()
        self._idle: _Worker | None = None
        self._live: set[_Worker] = set()
        self._prewarming = False
        self.spawned = 0
        self.killed = 0
        self.remote_renders = 0
        self.fallbacks = 0

    # -- lifecycle ---------------------------------------------------------

    def enabled(self) -> bool:
        return os.environ.get(DISABLE_ENV, "") not in ("1", "true", "TRUE")

    def prewarm(self) -> None:
        """Start a spare child in the background (never blocks the caller)."""
        if not self.enabled():
            return
        with self._lock:
            if self._idle is not None or self._prewarming:
                return
            self._prewarming = True
        threading.Thread(target=self._prewarm_run, name="render-prewarm",
                         daemon=True).start()

    def _prewarm_run(self) -> None:
        worker = None
        try:
            worker = self._spawn()
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("render worker prewarm failed: %s", exc)
        with self._lock:
            self._prewarming = False
            if worker is None:
                return
            if self._idle is None:
                self._idle = worker
                worker = None
        if worker is not None:
            self._retire(worker)

    def shutdown(self) -> None:
        with self._lock:
            workers = list(self._live)
            self._live.clear()
            self._idle = None
        for worker in workers:
            worker.kill()

    def _spawn(self) -> _Worker:
        token = secrets.token_bytes(_TOKEN_BYTES)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(_HANDSHAKE_TIMEOUT_S)
            port = server.getsockname()[1]
            cmd, env = _worker_command(port, token.hex())
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                creationflags=creationflags,
            )
            try:
                sock, _addr = server.accept()
            except (socket.timeout, OSError) as exc:
                proc.kill()
                raise RenderWorkerError(f"render worker did not connect: {exc}") from exc
        finally:
            server.close()
        sock.settimeout(_POLL_S)
        try:
            deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_S
            greeting = _recv_exactly(sock, _TOKEN_BYTES, None, deadline)
        except Exception as exc:
            sock.close()
            proc.kill()
            raise RenderWorkerError(f"render worker handshake failed: {exc}") from exc
        if not hmac.compare_digest(greeting, token):
            sock.close()
            proc.kill()
            raise RenderWorkerError("render worker handshake token mismatch")
        worker = _Worker(proc, sock)
        with self._lock:
            self._live.add(worker)
            self.spawned += 1
        return worker

    def _take(self) -> _Worker:
        with self._lock:
            worker = self._idle
            self._idle = None
        if worker is not None and worker.proc.poll() is None:
            return worker
        if worker is not None:
            self._retire(worker)
        return self._spawn()

    def _release(self, worker: _Worker) -> None:
        """Put a healthy worker back, or retire it and warm a replacement."""
        if worker.proc.poll() is not None or worker.requests >= _MAX_REQUESTS_PER_WORKER:
            self._retire(worker)
            self.prewarm()
            return
        with self._lock:
            if self._idle is None:
                self._idle = worker
                return
        self._retire(worker)

    def _retire(self, worker: _Worker) -> None:
        with self._lock:
            self._live.discard(worker)
            self.killed += 1
        worker.kill()

    # -- rendering ---------------------------------------------------------

    def render(self, *, path=None, text=None, cancel=None):
        """Render in a child process.

        Returns ``(rendered_body, error_message)`` exactly like
        ``md_converter._cached_body``.  Raises :class:`RenderWorkerCancelled`
        when *cancel* fired (the child is killed first) and
        :class:`RenderWorkerError` for any transport/child failure, which the
        caller turns into an in-process fallback.
        """
        if cancel is not None and cancel.is_set():
            raise RenderWorkerCancelled()
        worker = self._take()
        if cancel is not None and cancel.is_set():
            self._release(worker)
            raise RenderWorkerCancelled()
        request = {"op": "render_file" if path is not None else "render_text",
                   "path": str(path) if path is not None else None,
                   "text": text}
        deadline = time.monotonic() + _REQUEST_TIMEOUT_S
        try:
            send_frame(worker.sock, request)
            reply = recv_frame(worker.sock, cancel, deadline)
        except RenderWorkerCancelled:
            # The whole point of the child: stop the parse now, do not wait.
            self._retire(worker)
            self.prewarm()
            raise
        except Exception as exc:
            self._retire(worker)
            self.prewarm()
            with self._lock:
                self.fallbacks += 1
            raise RenderWorkerError(str(exc)) from exc
        worker.requests += 1
        self._release(worker)
        if not isinstance(reply, dict):
            raise RenderWorkerError("render worker sent a malformed reply")
        if reply.get("ok"):
            with self._lock:
                self.remote_renders += 1
            return reply.get("body"), None
        if reply.get("cancelled"):
            raise RenderWorkerCancelled()
        if reply.get("crash"):
            raise RenderWorkerError(str(reply.get("error")))
        return None, str(reply.get("error") or "")


_SERVICE = RenderService()


def service() -> RenderService:
    return _SERVICE


def prewarm() -> None:
    _SERVICE.prewarm()


def shutdown() -> None:
    _SERVICE.shutdown()


def render_remote(*, path=None, text=None, cancel=None):
    return _SERVICE.render(path=path, text=text, cancel=cancel)


atexit.register(shutdown)
