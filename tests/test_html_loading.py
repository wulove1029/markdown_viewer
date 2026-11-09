"""Background HTML ownership, cancellation, ordering, and browser acceptance."""

import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread, QThreadPool

from app import html_loading


class _ManualPool:
    def __init__(self):
        self.workers = []

    def start(self, worker, priority=0):
        self.workers.append(worker)


def _dispose(controller):
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _wait_until(qapp, predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_chunks_keep_utf8_and_escaped_base_and_cancel_removes_partial_file(tmp_path, monkeypatch):
    original = html_loading.tempfile.NamedTemporaryFile
    cancelled = threading.Event()
    writes = []

    class CancelOnFirstWrite:
        def __init__(self, **kwargs):
            self.stream = original(dir=tmp_path, **kwargs)
            self.name = self.stream.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, payload):
            writes.append(len(payload))
            result = self.stream.write(payload)
            cancelled.set()
            return result

    monkeypatch.setattr(html_loading.tempfile, "NamedTemporaryFile", CancelOnFirstWrite)
    assert html_loading._prepare_html_file("中文😀" * 100000, "", cancelled) is None
    assert len(writes) == 1 and not list(tmp_path.iterdir())
    monkeypatch.setattr(html_loading.tempfile, "NamedTemporaryFile",
                        lambda **kwargs: original(dir=tmp_path, **kwargs))
    text = "<html><head></head><body>" + "中文😀" * 100000 + "</body></html>"
    lease = html_loading._prepare_html_file(text, 'file:///source/?a=1&b="2"', threading.Event())
    value = Path(lease.fileName()).read_text("utf-8")
    assert 'href="file:///source/?a=1&amp;b=&quot;2&quot;"' in value
    assert value.endswith("中文😀</body></html>")
    lease.close()
    assert not list(tmp_path.iterdir())


def test_newest_submission_wins_even_with_same_generation_and_out_of_order_completion(qapp):
    pool = _ManualPool()
    controller = html_loading.HtmlPreparation(pool=pool)
    ready = []
    controller.ready.connect(lambda *args: ready.append(args))
    try:
        controller.prepare("<h1>Old partial</h1>", "", 3)
        older = pool.workers[-1]
        controller.prepare("<h1>Full document</h1>", "", 3)
        newer = pool.workers[-1]
        newer.run()
        older.run()
        assert len(ready) == 1
        assert ready[0][1] == 3
        assert Path(ready[0][2]).read_text("utf-8") == "<h1>Full document</h1>"
        assert older.request.cancelled.is_set()
    finally:
        paths = [Path(item.fileName()) for item in controller.files]
        _dispose(controller)
    assert all(not path.exists() for path in paths)


def test_completed_files_are_bounded_and_parent_destruction_cancels_queued_work(qapp):
    pool = _ManualPool()
    parent = QObject()
    controller = html_loading.HtmlPreparation(parent, pool=pool)
    paths = []
    for index in range(3):
        controller.prepare(f"document {index}", "", index)
        pool.workers[-1].run()
        paths.append(Path(controller.files[-1].fileName()))
    assert len(controller.files) == 2
    assert not paths[0].exists() and all(path.exists() for path in paths[1:])
    controller.prepare("Never shown", "", 4)
    pending = pool.workers[-1]
    _dispose(parent)
    assert pending.request.cancelled.is_set()
    pending.run()
    assert pending.request.take() is None
    assert all(not path.exists() for path in paths)


def test_destroyed_controller_removes_result_that_has_not_reached_queued_gui_callback(qapp):
    pool = _ManualPool()
    controller = html_loading.HtmlPreparation(pool=pool)
    controller.prepare("Completed off-thread", "", 1)
    worker = pool.workers[-1]
    thread = threading.Thread(target=worker.run)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    # No GUI event processing has occurred, so the result is still request-owned.
    lease = worker.request._lease
    assert lease is not None
    path = Path(lease.fileName())
    assert path.exists()
    _dispose(controller)
    qapp.processEvents()
    assert not path.exists()



def test_worker_finishing_after_controller_is_destroyed_reports_nothing(qapp):
    # The worker holds no Qt object (it is released on a pool thread), and the
    # lifetime object is not tracked weakly through a bound-method connection.
    import weakref

    pool = _ManualPool()
    controller = html_loading.HtmlPreparation(pool=pool)
    ready = []
    controller.ready.connect(lambda *args: ready.append(args))
    assert weakref.getweakrefcount(controller._lifetime) == 0
    controller.prepare("Late", "", 2)
    worker = pool.workers[-1]
    assert not any(isinstance(value, QObject) for value in vars(worker).values())
    _dispose(controller)
    thread = threading.Thread(target=worker.run)
    thread.start()
    thread.join(5)
    qapp.processEvents()
    assert ready == []
    assert worker.request.take() is None

def test_prepare_failure_reports_once_and_does_not_retain_a_file(qapp, monkeypatch):
    pool = _ManualPool()
    controller = html_loading.HtmlPreparation(pool=pool)
    ready = []
    controller.ready.connect(lambda *args: ready.append(args))

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(html_loading, "_prepare_html_file", fail)
    try:
        controller.prepare("body", "", 8)
        pool.workers[-1].run()
        assert ready == [(controller.serial, 8, "", "disk full")]
        assert controller.files == []
    finally:
        _dispose(controller)


def test_encoding_and_io_run_off_gui_thread_and_completion_runs_on_gui(qapp, monkeypatch):
    original = html_loading._prepare_html_file
    worker_threads = []
    callback_threads = []

    def capture(*args):
        worker_threads.append(QThread.currentThread())
        return original(*args)

    monkeypatch.setattr(html_loading, "_prepare_html_file", capture)
    controller = html_loading.HtmlPreparation()
    controller.ready.connect(lambda *args: callback_threads.append(QThread.currentThread()))
    try:
        controller.prepare("中文😀" * 300000, "", 1)
        _wait_until(qapp, lambda: bool(callback_threads))
        assert len(worker_threads) == 1 and worker_threads[0] != qapp.thread()
        assert callback_threads == [qapp.thread()]
    finally:
        assert QThreadPool.globalInstance().waitForDone(5000)
        _dispose(controller)


real_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1", reason="set RUN_WEBENGINE_TESTS=1",
)


@real_webengine
def test_renderer_small_replacement_and_deactivate_reject_pending_large_pages(qapp):
    from app.renderer import RendererView
    from tests.test_renderer_switching import _evaluate

    view = RendererView()
    pool = _ManualPool()
    view._html_preparation._pool = pool
    large = "<html><head></head><body>OLD<!--" + "x" * 800000 + "--></body></html>"
    try:
        view.render_html(large)
        pending = pool.workers[-1]
        view.render_html("<html><head></head><body>NEW</body></html>")
        pending.run()
        _wait_until(qapp, lambda: _evaluate(view, "document.body.innerText") == "NEW")
        assert view._html_files == []
        view.render_html(large)
        cancelled = pool.workers[-1]
        view.deactivate()
        cancelled.run()
        assert view._html_files == []
        assert _evaluate(view, "document.body.innerText") == "NEW"
    finally:
        view.close()


@real_webengine
def test_large_html_prepare_failure_surfaces_error_and_clears_loading(qapp, tmp_path, monkeypatch):
    from app.renderer import RendererView
    from tests.test_renderer_switching import _evaluate

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(html_loading, "_prepare_html_file", fail)
    view = RendererView()
    monkeypatch.setattr(view, "_finish_load_file", lambda *args: None)
    source = tmp_path / "note.md"
    source.write_text("# Note", encoding="utf-8")
    failures = []
    view.render_failed.connect(lambda *args: failures.append(args))
    view.load_file(source)
    generation = view._render_generation
    html = "<html><head></head><body>body<!--" + "x" * 800000 + "--></body></html>"
    view._on_file_render_ready(generation, source, html, [])
    _wait_until(qapp, lambda: bool(failures))
    assert failures == [(generation, str(source))]
    assert not view._loading_shield.isVisible()
    assert "無法建立大型文件預覽暫存檔" in _evaluate(view, "document.body.innerText")
    assert view._html_files == []
    view.close()


@real_webengine
@pytest.mark.parametrize("kind", ["pdf", "html"])
def test_large_unsaved_source_snapshot_exports_and_releases_prepared_file(
    qapp, tmp_path, monkeypatch, kind,
):
    from types import SimpleNamespace

    import fitz
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QPushButton, QWidget

    from app import edit_backend, export_actions

    original = html_loading._prepare_html_file
    prepared = []

    def capture(*args):
        lease = original(*args)
        if lease is not None:
            prepared.append((Path(lease.fileName()), QThread.currentThread()))
        return lease

    monkeypatch.setattr(html_loading, "_prepare_html_file", capture)
    source = tmp_path / "unsaved.md"
    source.write_text("# DISK ONLY", encoding="utf-8")
    image = QImage(40, 40, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(tmp_path / "image.png"))
    owner = QWidget()
    owner.current_file = source
    owner.edit_mode = True
    owner.exporting = False
    owner.pdf_progress = None
    owner._active_edit_backend = edit_backend.SOURCE_BACKEND
    owner.wysiwyg_view = None
    owner._export_btn = QPushButton(owner)
    owner.renderer = SimpleNamespace(width=lambda: 850)
    owner._editor = SimpleNamespace(toPlainText=lambda:
        "# UNSAVED BEGIN\n\n![Relative image](image.png)\n\n<!--"
        + "x" * 900000 + "-->\n\nUNSAVED END")
    owner.statusBar = lambda: SimpleNamespace(showMessage=lambda *args: None)
    owner._refresh_icons = lambda: None
    completed, failures = [], []
    output = tmp_path / f"snapshot.{kind}"
    monkeypatch.setattr(export_actions, "show_pdf_progress", lambda *args: None)
    monkeypatch.setattr(export_actions, "on_pdf_exported",
                        lambda *args: completed.append(args))
    monkeypatch.setattr(export_actions, "show_export_complete",
                        lambda window, path: completed.append((window, path, True)))
    monkeypatch.setattr(export_actions.QMessageBox, "warning",
                        lambda *args: failures.append(args))
    monkeypatch.setattr(export_actions, "ask_page_setup",
                        lambda *args: {"size": "A4", "orientation": "portrait"})
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(output), ""))
    getattr(export_actions, f"export_{kind}")(owner)
    owner._editor.toPlainText = lambda: "# LATER EDIT"
    _wait_until(qapp, lambda: bool(completed or failures), timeout=20)
    assert failures == [] and completed == [(owner, str(output), True)]
    assert prepared and all(thread != qapp.thread() for _, thread in prepared)
    if kind == "pdf":
        with fitz.open(output) as pdf:
            text = "\n".join(page.get_text() for page in pdf)
            assert any(page.get_images() for page in pdf)
    else:
        text = output.read_text("utf-8")
        assert '<base href="file:' in text and 'src="image.png"' in text
    assert "UNSAVED BEGIN" in text and "UNSAVED END" in text
    assert "DISK ONLY" not in text and "LATER EDIT" not in text
    assert source.read_text("utf-8") == "# DISK ONLY"
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not path.exists() for path, _ in prepared)
    owner.close()
