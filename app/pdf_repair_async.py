"""Run the PDF ToUnicode repair on a worker thread.

Repairing a long PDF takes seconds, so GUI exports hand it to a private
single-thread pool and keep their progress dialog up until it finishes.  The
completion callback always runs on the main thread.  The headless CLI does not
use this module: it calls ``repair_radical_tounicode`` synchronously so the
process cannot exit while a repair is still running.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QRunnable, QThreadPool, Signal

from . import pdf_tounicode

log = logging.getLogger(__name__)

_pool: QThreadPool | None = None
_active: set[_Job] = set()


def _get_pool() -> QThreadPool:
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(1)  # repairs are memory heavy; run one at a time
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: _pool.waitForDone(10000) if _pool else None)
    return _pool


class _Job(QObject):
    """Lives on the main thread; the worker's signal is delivered here queued."""

    finished = Signal(bool)

    def __init__(self, path: str, on_done: Callable[[bool], None]) -> None:
        super().__init__()
        self.path = path
        self._on_done = on_done
        self.finished.connect(self._deliver)

    def _deliver(self, ok: bool) -> None:
        _active.discard(self)
        try:
            self._on_done(ok)
        finally:
            self.deleteLater()


class _Task(QRunnable):
    def __init__(self, job: _Job) -> None:
        super().__init__()
        self._job = job

    def run(self) -> None:
        ok = False
        try:
            pdf_tounicode.repair_radical_tounicode_strict(Path(self._job.path))
            ok = True
        except Exception:
            log.warning("Could not repair PDF text mapping of %s", self._job.path,
                        exc_info=True)
        self._job.finished.emit(ok)


def repair_async(path: str, on_done: Callable[[bool], None]) -> None:
    """Repair *path* off the main thread, then call ``on_done(succeeded)``.

    Must be called from the main thread.  ``on_done`` always runs on the main
    thread, exactly once; a failure leaves the original PDF untouched.
    """
    job = _Job(str(path), on_done)
    _active.add(job)
    _get_pool().start(_Task(job))
