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

From source the child imports markdown-it and Pygments only (no Qt).  In a
frozen build the child *is* the app executable, so ``main.py``'s module-level
imports pull in Qt before the worker entry point runs -- the frozen child is
therefore heavier and slower to start than the source-mode one.
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
# Generous on purpose: a 10 MB document (MAX_PREVIEW_BYTES) can produce a
# body fragment several times its own size. It is a sanity bound against a
# corrupt length prefix, not a memory budget.
_MAX_FRAME_BYTES = 256 * 1024 * 1024
_HANDSHAKE_TIMEOUT_S = 30.0
_REQUEST_TIMEOUT_S = 180.0
_POLL_S = 0.02
# Recycle a worker after this many documents so a long session cannot grow the
# child's peak RSS without bound (a fresh child costs ~0.5 s, paid in the
# background right after the previous request finished).
_MAX_REQUESTS_PER_WORKER = 12
_MAX_LIVE_WORKERS = 2


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
            log.warning("kill: optional operation failed", exc_info=True)


def _worker_command(port: int) -> tuple[list[str], dict]:
    """Argv for the child. The token is NEVER an argument: any local process
    can read another process's command line, so it goes over the child's stdin
    (see _spawn) and only the port is public."""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env[DISABLE_ENV] = "1"  # a child never spawns grandchildren
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--render-worker", str(port)], env
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return (
        [sys.executable, "-X", "utf8", "-m", "app.render_worker", str(port)],
        env,
    )


class RenderService:
    """Bounded child pool with cancellable waits and visible-work priority."""

    def __init__(self, max_workers: int = _MAX_LIVE_WORKERS):
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)
        self._max_workers = max(1, int(max_workers))
        self._spawning = 0
        self._epoch = 0
        self._waiting_foreground = 0
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
        with self._available:
            if (self._idle is not None or self._prewarming
                    or len(self._live) + self._spawning >= self._max_workers):
                return
            self._prewarming = True
            self._spawning += 1
            epoch = self._epoch
        threading.Thread(target=self._prewarm_run, args=(epoch,), name="render-prewarm",
                         daemon=True).start()

    def _prewarm_run(self, epoch: int) -> None:
        worker = None
        try:
            worker = self._spawn(epoch=epoch)
        except RenderWorkerCancelled:
            pass
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("render worker prewarm failed: %s", exc)
        with self._available:
            self._prewarming = False
            self._spawning -= 1
            self._available.notify_all()
            if worker is None:
                return
            if epoch == self._epoch and self._idle is None:
                self._idle = worker
                worker = None
        if worker is not None:
            self._retire(worker)

    def shutdown(self) -> None:
        with self._available:
            self._epoch += 1
            workers = list(self._live)
            self._live.clear()
            self._idle = None
            self._available.notify_all()
        for worker in workers:
            worker.kill()

    def _spawn(self, cancel=None, epoch=None) -> _Worker:
        if epoch is None:
            with self._lock:
                epoch = self._epoch

        def cancelled():
            return self._epoch != epoch or (cancel is not None and cancel.is_set())

        if cancelled():
            raise RenderWorkerCancelled()
        token = secrets.token_bytes(_TOKEN_BYTES)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            server.settimeout(_POLL_S)
            port = server.getsockname()[1]
            cmd, env = _worker_command(port)
            creationflags = 0
            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                creationflags=creationflags,
            )
            try:
                # Hand the shared secret over a private pipe, then close it.
                proc.stdin.write(token)
                proc.stdin.flush()
                proc.stdin.close()
            except OSError as exc:
                proc.kill()
                raise RenderWorkerError(f"render worker stdin failed: {exc}") from exc
            deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_S
            while True:
                if cancelled():
                    proc.kill()
                    proc.wait(timeout=5)
                    raise RenderWorkerCancelled()
                try:
                    sock, _addr = server.accept()
                    break
                except socket.timeout:
                    if time.monotonic() < deadline:
                        continue
                    proc.kill()
                    proc.wait(timeout=5)
                    raise RenderWorkerError(
                        "render worker did not connect before timeout"
                    ) from None
                except OSError as exc:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise RenderWorkerError(f"render worker did not connect: {exc}") from exc
        finally:
            server.close()
        sock.settimeout(_POLL_S)
        try:
            deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_S
            class SpawnCancellation:
                @staticmethod
                def is_set():
                    return cancelled()

            greeting = _recv_exactly(sock, _TOKEN_BYTES, SpawnCancellation(), deadline)
        except RenderWorkerCancelled:
            sock.close()
            proc.kill()
            proc.wait(timeout=5)
            raise
        except Exception as exc:
            sock.close()
            proc.kill()
            proc.wait(timeout=5)
            raise RenderWorkerError(f"render worker handshake failed: {exc}") from exc
        if not hmac.compare_digest(greeting, token):
            # Reject before a single byte is unpickled.
            sock.close()
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:  # pragma: no cover - already reaped
                log.warning("_spawn: optional operation failed", exc_info=True)
            raise RenderWorkerError("render worker handshake token mismatch")
        worker = _Worker(proc, sock)
        with self._lock:
            stale = cancelled()
            if not stale:
                self._live.add(worker)
                self.spawned += 1
        if stale:
            worker.kill()
            raise RenderWorkerCancelled()
        return worker

    def _take(self, cancel=None, *, metadata_only=False) -> _Worker:
        with self._available:
            epoch = self._epoch
            if not metadata_only:
                self._waiting_foreground += 1
        try:
            while True:
                with self._available:
                    if self._epoch != epoch or (cancel is not None and cancel.is_set()):
                        raise RenderWorkerCancelled()
                    if metadata_only and self._waiting_foreground:
                        self._available.wait(_POLL_S)
                        continue
                    worker = self._idle
                    self._idle = None
                    if worker is None:
                        if len(self._live) + self._spawning >= self._max_workers:
                            self._available.wait(_POLL_S)
                            continue
                        self._spawning += 1
                if worker is not None:
                    if worker.proc.poll() is None:
                        return worker
                    self._retire(worker)
                    continue
                try:
                    return self._spawn(cancel=cancel, epoch=epoch)
                finally:
                    with self._available:
                        self._spawning -= 1
                        self._available.notify_all()
        finally:
            if not metadata_only:
                with self._available:
                    self._waiting_foreground -= 1
                    self._available.notify_all()

    def _release(self, worker: _Worker) -> None:
        """Put a healthy worker back, or retire it and warm a replacement."""
        if worker.proc.poll() is not None or worker.requests >= _MAX_REQUESTS_PER_WORKER:
            self._retire(worker)
            self.prewarm()
            return
        with self._available:
            if worker not in self._live:
                return
            if self._idle is None:
                self._idle = worker
                self._available.notify_all()
                return
        self._retire(worker)

    def _retire(self, worker: _Worker) -> None:
        worker.kill()
        with self._available:
            self._live.discard(worker)
            self.killed += 1
            self._available.notify_all()

    # -- rendering ---------------------------------------------------------

    def render(self, *, path=None, text=None, cancel=None, metadata_only=False):
        from . import diagnostics

        started = time.monotonic()
        cancelled = failed = fallback_requested = False
        try:
            result = self._render(path=path, text=text, cancel=cancel, metadata_only=metadata_only)
            failed = result[1] is not None
            return result
        except RenderWorkerCancelled:
            cancelled = True
            raise
        except RenderWorkerError:
            failed = fallback_requested = True
            raise
        except Exception:
            failed = True
            raise
        finally:
            diagnostics.record(
                "markdown_metadata" if metadata_only else "markdown_render",
                duration_ms=(time.monotonic() - started) * 1000, cancelled=cancelled,
                failed=failed, fallback_requested=fallback_requested,
            )

    def _render(self, *, path=None, text=None, cancel=None, metadata_only=False):
        """Render in a child process.

        Returns ``(rendered_body, error_message)`` exactly like
        ``md_converter._cached_body``.  Raises :class:`RenderWorkerCancelled`
        when *cancel* fired (the child is killed first) and
        :class:`RenderWorkerError` for any transport/child failure, which the
        caller turns into an in-process fallback.
        """
        if cancel is not None and cancel.is_set():
            raise RenderWorkerCancelled()
        worker = self._take(cancel, metadata_only=metadata_only)
        if cancel is not None and cancel.is_set():
            self._release(worker)
            raise RenderWorkerCancelled()
        operation = "metadata" if metadata_only else "render"
        request = {"op": operation + ("_file" if path is not None else "_text"),
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
            log.warning("Render worker transport failed", exc_info=True)
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


def metadata_remote(*, path=None, text=None, cancel=None):
    return _SERVICE.render(path=path, text=text, cancel=cancel, metadata_only=True)


atexit.register(shutdown)
