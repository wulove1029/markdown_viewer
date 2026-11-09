"""Cancellable HTML encoding and file preparation outside the GUI thread."""

from __future__ import annotations

import html as html_module
import re
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from . import background_jobs
from .worker_relay import WorkerRelay, call_on_destroyed


class HtmlFileLease:
    """Pure Python ownership lets any cancellation path remove its own file."""

    def __init__(self, path: str):
        self._path = path

    def fileName(self) -> str:
        return self._path

    def close(self) -> None:
        try:
            Path(self._path).unlink(missing_ok=True)
        except OSError:
            # Chromium may briefly retain a file handle on Windows. The lease
            # destructor gets one final opportunity after navigation releases it.
            return

    def __del__(self):
        self.close()


class _Request:
    def __init__(self, serial: int, generation: int):
        self.serial = serial
        self.generation = generation
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._lease = None
        self.error = ""

    def cancel(self) -> None:
        with self._lock:
            self.cancelled.set()
            lease, self._lease = self._lease, None
        if lease is not None:
            lease.close()

    def publish(self, lease, error: str) -> None:
        with self._lock:
            if not self.cancelled.is_set():
                self._lease = lease
                self.error = error
                return
        if lease is not None:
            lease.close()

    def take(self):
        with self._lock:
            lease, self._lease = self._lease, None
            return lease


class _Lifetime:
    """No QObject/view references: safe to retain in a worker or destroyed slot."""

    def __init__(self):
        self.serial = 0
        self.request = None
        self.files = []
        self.closed = False

    def cancel(self) -> None:
        self.serial += 1
        if self.request is not None:
            self.request.cancel()
            self.request = None

    def close(self, *_args) -> None:
        self.closed = True
        self.cancel()
        for lease in self.files:
            lease.close()
        self.files.clear()


def _prepare_html_file(html: str, base_url: str, cancelled: threading.Event):
    if cancelled.is_set():
        return None
    if base_url:
        base = html_module.escape(base_url, quote=True)
        tag = f'<base href="{base}">'
        head = re.search(r"<head(?:\s[^>]*)?>", html, flags=re.IGNORECASE)
        if head is not None:
            html = html[:head.end()] + tag + html[head.end():]
        else:
            html = tag + html
    lease = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="markdown-viewer-", suffix=".html", delete=False,
        ) as stream:
            lease = HtmlFileLease(stream.name)
            for offset in range(0, len(html), 64 * 1024):
                if cancelled.is_set():
                    break
                payload = html[offset:offset + 64 * 1024].encode("utf-8")
                if stream.write(payload) != len(payload):
                    raise OSError("Incomplete HTML temporary-file write")
        if cancelled.is_set():
            lease.close()
            return None
        return lease
    except Exception:
        if lease is not None:
            lease.close()
        raise


class _PrepareWorker(QRunnable):
    """Prepares one file; ``report(request)`` is the controller's relay emit."""

    def __init__(self, request: _Request, html: str, base_url: str, report):
        super().__init__()
        self.request = request
        self.html = html
        self.base_url = base_url
        self.report = report

    def run(self) -> None:
        lease = None
        error = ""
        try:
            lease = _prepare_html_file(self.html, self.base_url, self.request.cancelled)
        except Exception as exc:
            error = str(exc)
        finally:
            self.html = ""
        self.request.publish(lease, error)
        self.report(self.request)


class HtmlPreparation(QObject):
    """Deliver only the latest prepared file and retain at most two navigations."""

    ready = Signal(int, int, str, str)  # serial, generation, path, error

    def __init__(self, parent=None, *, pool=None):
        super().__init__(parent)
        self._pool = pool if pool is not None else background_jobs.pool()
        self._lifetime = _Lifetime()
        # A closure, not the bound close method: PySide would otherwise track
        # the lifetime object weakly. Workers report through a relay this
        # controller owns, so they hold no Qt object.
        call_on_destroyed(self, self._lifetime.close)
        self._relay = WorkerRelay(self, self._finished)

    @property
    def files(self):
        return self._lifetime.files

    @property
    def serial(self) -> int:
        return self._lifetime.serial

    def cancel(self) -> None:
        self._lifetime.cancel()

    def prepare(self, html: str, base_url: str, generation: int) -> None:
        self.cancel()
        if self._lifetime.closed:
            return
        request = _Request(self.serial, generation)
        self._lifetime.request = request
        worker = _PrepareWorker(request, html, base_url, self._relay.emit)
        if self._pool is background_jobs.pool():
            background_jobs.submit(
                worker, priority=background_jobs.VISIBLE, key=(id(self), "html"),
                should_cancel=request.cancelled.is_set,
            )
        else:
            self._pool.start(worker, background_jobs.VISIBLE)

    @Slot(object)
    def _finished(self, request: _Request) -> None:
        if (self._lifetime.closed or request is not self._lifetime.request
                or request.cancelled.is_set()):
            request.cancel()
            return
        self._lifetime.request = None
        lease = request.take()
        if lease is not None:
            self.files.append(lease)
            while len(self.files) > 2:
                self.files.pop(0).close()
        self.ready.emit(request.serial, request.generation,
                        lease.fileName() if lease is not None else "", request.error)
