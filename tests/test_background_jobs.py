"""Shared Qt worker bounds, priority, cancellation, and bounded pending payloads."""

import threading

from PySide6.QtCore import QRunnable, QThreadPool

from app import background_jobs, pdf_view


class Work(QRunnable):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def run(self):
        self.callback()


def test_global_pool_is_shared_and_bounded(qapp):
    thread_pool = background_jobs.pool()
    assert thread_pool is QThreadPool.globalInstance()
    assert thread_pool.maxThreadCount() == background_jobs.MAX_THREADS == 4
    assert background_jobs.pool() is thread_pool


def test_worker_cap_and_priority_order(qapp):
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)
    queue = background_jobs.BackgroundQueue(thread_pool)
    started, release = threading.Event(), threading.Event()
    finished = []

    def block():
        started.set()
        release.wait(5)

    queue.submit(Work(block))
    assert started.wait(2)
    try:
        queue.submit(Work(lambda: finished.append("metadata")), priority=-1)
        queue.submit(Work(lambda: finished.append("normal")), priority=0)
        queue.submit(Work(lambda: finished.append("visible")), priority=1)
        assert thread_pool.activeThreadCount() == 1
        assert finished == []
    finally:
        release.set()
        assert thread_pool.waitForDone(5000)
    assert finished == ["visible", "normal", "metadata"]


def test_coalesced_queue_executes_only_latest_payload_and_skips_cancelled_work(qapp):
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)
    queue = background_jobs.BackgroundQueue(thread_pool)
    started, release = threading.Event(), threading.Event()
    completed = []

    def block():
        started.set()
        release.wait(5)

    queue.submit(Work(block))
    assert started.wait(2)
    try:
        for number in range(100):
            queue.submit(Work(lambda n=number: completed.append(n)), key="same document")
        cancelled = threading.Event()
        cancelled.set()
        queue.submit(Work(lambda: completed.append("obsolete")), key="cancelled",
                     should_cancel=cancelled.is_set)
        assert queue.pending_count == 2
    finally:
        release.set()
        assert thread_pool.waitForDone(5000)
    assert completed == [99]
    assert queue.pending_count == 0


def test_running_task_retains_ownership_and_coalesces_only_successor(qapp):
    thread_pool = QThreadPool()
    thread_pool.setMaxThreadCount(1)
    queue = background_jobs.BackgroundQueue(thread_pool)
    started, release = threading.Event(), threading.Event()
    completed = []

    def running():
        started.set()
        release.wait(5)
        completed.append("running")

    queue.submit(Work(running), key="preview")
    assert started.wait(2)
    try:
        queue.submit(Work(lambda: completed.append("old pending")), key="preview")
        queue.submit(Work(lambda: completed.append("latest pending")), key="preview")
        assert queue.pending_count == 1
    finally:
        release.set()
        assert thread_pool.waitForDone(5000)
    assert completed == ["running", "latest pending"]


def test_cancelled_pdf_metadata_does_not_open_document(qapp, tmp_path, monkeypatch):
    def unexpected(*_args):
        raise AssertionError("Cancelled queued work opened a PDF")

    monkeypatch.setattr(pdf_view, "extract_outline", unexpected)
    monkeypatch.setattr(pdf_view, "extract_embedded_annotations", unexpected)
    for task_class in (pdf_view._PdfOutlineTask, pdf_view._PdfEmbeddedAnnotationsTask):
        results = []
        task = task_class(1, tmp_path / "closed.pdf", "", lambda *args: results.append(args))
        task.cancel.set()
        task.run()
        assert results == [(1, tmp_path / "closed.pdf", [])]
