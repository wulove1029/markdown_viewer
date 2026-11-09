"""Background metadata reads report to the window on the GUI thread only."""

import threading

from PySide6.QtCore import QObject, QThreadPool
from PySide6.QtTest import QTest
from shiboken6 import delete

from app import tag_flow


class _Window(QObject):
    def __init__(self, path):
        super().__init__()
        self._current_file = path
        self.reports = []

    def _on_document_metadata_ready(self, *args):
        self.reports.append(("ready", threading.get_ident(), args))

    def _on_document_metadata_failed(self, *args):
        self.reports.append(("failed", threading.get_ident(), args))


def _settle(qapp):
    assert QThreadPool.globalInstance().waitForDone(5000)
    QTest.qWait(20)
    qapp.processEvents()


def test_metadata_worker_reports_results_and_failures_on_gui_thread(
    qapp, tmp_path, monkeypatch
):
    path = tmp_path / "note.md"
    path.write_text("---\ntags: [alpha]\n---\nBody #beta", encoding="utf-8")
    monkeypatch.setattr(tag_flow, "cached_metadata", lambda _path: None)
    window = _Window(path)
    tag_flow.request_metadata(window)
    _settle(qapp)
    kind, thread_id, (generation, reported, result) = window.reports[-1]
    assert (kind, thread_id, generation, reported) == (
        "ready", threading.get_ident(), window._metadata_generation, path)
    assert "alpha" in result.front_tags

    def fail(*_args, **_kwargs):
        raise OSError("unreadable")

    monkeypatch.setattr(tag_flow, "read_metadata", fail)
    tag_flow.request_metadata(window)
    _settle(qapp)
    assert window.reports[-1] == (
        "failed", threading.get_ident(), (window._metadata_generation, path, "unreadable"))
    delete(window)


def test_metadata_read_outliving_its_window_reports_nothing(qapp, tmp_path, monkeypatch):
    path = tmp_path / "note.md"
    path.write_text("Body", encoding="utf-8")
    entered, release, workers = threading.Event(), threading.Event(), []
    read = tag_flow.read_metadata

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return read(*args, **kwargs)

    class TrackedWorker(tag_flow._MetadataWorker):
        def __init__(self, *args):
            super().__init__(*args)
            workers.append(vars(self).copy())

    monkeypatch.setattr(tag_flow, "cached_metadata", lambda _path: None)
    monkeypatch.setattr(tag_flow, "read_metadata", blocked)
    monkeypatch.setattr(tag_flow, "_MetadataWorker", TrackedWorker)
    window = _Window(path)
    reports = window.reports
    try:
        tag_flow.request_metadata(window)
        assert entered.wait(5)
        assert not any(isinstance(value, QObject) for value in workers[0].values())
        relays = window._metadata_worker_relays
        delete(window)
        assert all(relay.closed for relay in relays)
    finally:
        release.set()
    _settle(qapp)
    assert reports == []
