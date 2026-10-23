"""One bounded Qt worker pool with priorities and optional queued coalescing."""

from __future__ import annotations

import threading
import time
from typing import Callable, Hashable

from PySide6.QtCore import QRunnable, QThreadPool

from . import diagnostics

MAX_THREADS = 4
VISIBLE = 1
BACKGROUND = -1


class _PendingWork(QRunnable):
    def __init__(self, queue, key, worker, should_cancel):
        super().__init__()
        self.queue, self.key = queue, key
        self.worker, self.should_cancel = worker, should_cancel

    def run(self):
        # Taking the latest payload and removing the pending slot is atomic.
        # A later submission may queue one successor while this task runs.
        with self.queue._lock:
            self.queue._pending.pop(self.key, None)
            worker, should_cancel = self.worker, self.should_cancel
            self.worker = self.should_cancel = None
        started = time.monotonic()
        cancelled = failed = False
        try:
            cancelled = should_cancel is not None and should_cancel()
            if not cancelled:
                worker.run()
        except Exception:
            failed = True
            raise
        finally:
            diagnostics.record(
                "background_work", duration_ms=(time.monotonic() - started) * 1000,
                cancelled=bool(cancelled), failed=failed,
            )


class BackgroundQueue:
    """Qt owns execution and shutdown; this object only coalesces pending work.

    Use a key only for replaceable requests whose discarded workers require no
    completion callback. Search/TXT ownership callbacks must still run and use
    their existing one-active/one-pending or cancellation protocol instead.
    """

    def __init__(self, thread_pool: QThreadPool):
        self.thread_pool = thread_pool
        self._lock = threading.Lock()
        self._pending: dict[Hashable, _PendingWork] = {}

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def submit(
        self, worker: QRunnable, *, priority: int = BACKGROUND,
        key: Hashable | None = None, should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        if key is None:
            if should_cancel is not None:
                raise ValueError("取消前置檢查必須使用可合併的工作識別鍵。")
            self.thread_pool.start(worker, priority)
            return
        with self._lock:
            pending = self._pending.get(key)
            if pending is not None:
                pending.worker, pending.should_cancel = worker, should_cancel
                return
            pending = _PendingWork(self, key, worker, should_cancel)
            self._pending[key] = pending
        self.thread_pool.start(pending, priority)


_lock = threading.Lock()
_queue: BackgroundQueue | None = None


def pool() -> QThreadPool:
    """Configure Qt's process-wide pool once, retaining existing shutdown rules."""
    global _queue
    with _lock:
        if _queue is None:
            thread_pool = QThreadPool.globalInstance()
            thread_pool.setMaxThreadCount(MAX_THREADS)
            _queue = BackgroundQueue(thread_pool)
        return _queue.thread_pool


def submit(
    worker: QRunnable, *, priority: int = BACKGROUND,
    key: Hashable | None = None, should_cancel: Callable[[], bool] | None = None,
) -> None:
    pool()
    assert _queue is not None
    _queue.submit(worker, priority=priority, key=key, should_cancel=should_cancel)
