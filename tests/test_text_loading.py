"""Large text loads publish only complete documents and reject stale reads."""
import threading
import time

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QThread, QThreadPool
from PySide6.QtGui import QFont, QTextCursor, QTextDocument
from PySide6.QtTest import QTest

from app.text_loading import TextLoader, _PreparedText


def _wait(qapp, predicate):
    end = time.monotonic() + 10
    while not predicate() and time.monotonic() < end:
        QTest.qWait(5)
    assert predicate()


def test_large_text_roundtrip_preserves_unicode_encoding_and_newlines(qapp, tmp_path):
    path = tmp_path / "large.txt"
    text = ("文字 😀 line\r\n" * 30_000)
    path.write_bytes(text.encode("utf-16"))
    loader = TextLoader()
    results = []
    loader.ready.connect(lambda *args: results.append(args))
    loader.start(path, QFont())
    assert not results
    _wait(qapp, lambda: bool(results))
    received_path, document, encoding, newline, _signature = results[0]
    assert received_path == path
    assert encoding == "utf-16"
    assert newline == "\r\n"
    assert document.toPlainText() == text.replace("\r\n", "\n")
    assert not document.isModified()
    assert document.isUndoRedoEnabled()
    assert not document.isUndoAvailable()
    document.deleteLater()
    loader.cancel()
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_cancel_during_document_build_never_publishes_partial_text(qapp, tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("line\n" * 100_000, encoding="utf-8")
    loader = TextLoader()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(path, QFont())
    # Force a known pending build state before delivering another chunk.
    loader._read_done(loader._generation, path,
                      _PreparedText("line\n" * 100_000, "utf-8", "\n", (0, 0)), None)
    loader._append_chunk()
    assert loader._document is not None
    loader.cancel()
    QTest.qWait(30)
    assert not ready
    assert loader._document is None
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_late_read_after_switch_does_not_replace_new_document(qapp, tmp_path):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("old", encoding="utf-8")
    second.write_text("new", encoding="utf-8")
    loader = TextLoader()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(first, QFont())
    old_generation = loader._generation
    loader.start(second, QFont())
    loader._read_done(old_generation, first, _PreparedText("old", "utf-8", "\n", (0, 0)), None)
    _wait(qapp, lambda: bool(ready))
    assert len(ready) == 1
    assert ready[0][0] == second
    assert ready[0][1].toPlainText() == "new"
    ready[0][1].deleteLater()
    loader.cancel()
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_document_construction_yields_on_time_budget_and_never_publishes_partial(
    qapp, tmp_path, monkeypatch,
):
    from app import text_loading

    loader = TextLoader()
    loader._path = tmp_path / "budget.txt"
    loader._font = QFont()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader._read_done(loader._generation, loader._path,
                      _PreparedText("line\n" * 50_000, "utf-8", "\n", (0, 0)), None)
    times = iter((0.0, 0.004, 0.009))
    monkeypatch.setattr(text_loading.time, "perf_counter", lambda: next(times))
    loader._append_chunk()
    assert loader._offset == text_loading._CHUNK_CHARS * 2
    assert loader._timer.isActive()
    assert ready == []
    loader.cancel()
    assert loader._document is None
    assert not loader._timer.isActive()


def test_background_read_stops_between_chunks_before_decoding(tmp_path, monkeypatch):
    import io
    import threading

    from app import text_loading

    cancelled = threading.Event()
    reads = []

    class CancelAfterRead(io.BytesIO):
        def read(self, size=-1):
            result = super().read(size)
            reads.append(len(result))
            cancelled.set()
            return result

    class Source:
        def open(self, mode):
            assert mode == "rb"
            return CancelAfterRead(b"a" * (text_loading._READ_CHUNK_BYTES * 3))

    monkeypatch.setattr(text_loading, "_decode_bytes", lambda _: (_ for _ in ()).throw(
        AssertionError("A cancelled read must not decode the partial bytes")))
    assert text_loading._read_cancelable(Source(), cancelled) is None
    assert reads == [text_loading._READ_CHUNK_BYTES]


def test_new_load_after_cancelled_build_can_finish(qapp, tmp_path):
    loader = TextLoader()
    loader._path = tmp_path / "old.txt"
    loader._font = QFont()
    loader._read_done(loader._generation, loader._path,
                      _PreparedText("old\n" * 100_000, "utf-8", "\n", (0, 0)), None)
    assert loader._building
    new_path = tmp_path / "new.txt"
    new_path.write_text("new 完整內容", encoding="utf-8")
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(new_path, QFont())
    _wait(qapp, lambda: bool(ready))
    assert ready[0][0] == new_path
    assert ready[0][1].toPlainText() == "new 完整內容"
    ready[0][1].deleteLater()
    loader.cancel()
    assert QThreadPool.globalInstance().waitForDone(10000)


def test_long_line_handoff_preserves_every_character_edit_undo_and_save(qapp, tmp_path):
    from app.atomic_io import atomic_write_bytes
    from app.editor import EditorView
    from app.text_loading import text_document_needs_no_wrap

    path = tmp_path / "long.txt"
    original = "開頭 😀\t" + "文字😀abc " * 15_000 + "結尾\r\nnext"
    path.write_bytes(original.encode("utf-16"))
    loader = TextLoader()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(path, QFont())
    _wait(qapp, lambda: bool(ready))
    _, document, encoding, newline, _ = ready[0]
    assert document.thread() == qapp.thread()
    assert document.documentLayout().thread() == qapp.thread()
    assert document.parent() is None
    assert text_document_needs_no_wrap(document)
    expected = original.replace("\r\n", "\n")
    assert document.toPlainText() == expected
    assert not document.isUndoAvailable()
    editor = EditorView()
    editor.resize(900, 600)
    editor.use_buffer_document(document, plain_text_mode=True, document_path=path)
    editor.show()
    qapp.processEvents()
    assert editor.lineWrapMode() == editor.LineWrapMode.NoWrap
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    editor.insertPlainText(" 編輯😀")
    assert document.isModified()
    editor.undo()
    assert document.toPlainText() == expected
    editor.redo()
    rendered = document.toPlainText().replace("\n", newline).encode(encoding)
    target = tmp_path / "saved.txt"
    atomic_write_bytes(target, rendered)
    assert target.read_bytes().decode("utf-16") == original + " 編輯😀"
    ordinary = editor.create_buffer_document("ordinary\n完整")
    editor.use_buffer_document(ordinary, plain_text_mode=True, document_path=target)
    assert editor.lineWrapMode() == editor.LineWrapMode.WidgetWidth
    assert editor.toPlainText() == "ordinary\n完整"
    assert document.toPlainText() == expected + " 編輯😀"
    editor.release_buffer_document()
    editor.close()
    editor.deleteLater()
    ordinary.deleteLater()
    document.deleteLater()
    loader.cancel()


def test_cancel_while_worker_owns_long_document_destroys_on_worker_and_reopens(
    qapp, tmp_path, monkeypatch,
):
    from app import text_loading

    built, release = threading.Event(), threading.Event()
    owners, destroyed = [], []

    class TrackedDocument(QTextDocument):
        def setPlainText(self, text):
            super().setPlainText(text)
            owners.append(QThread.currentThread())
            self.destroyed.connect(
                lambda: destroyed.append(QThread.currentThread()),
                Qt.ConnectionType.DirectConnection,
            )
            built.set()
            assert release.wait(5)

    monkeypatch.setattr(text_loading, "QTextDocument", TrackedDocument)
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("long😀" * 20_000, encoding="utf8")
    second.write_text("重新開啟完整內容", encoding="utf8")
    loader = TextLoader()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(first, QFont())
    try:
        assert built.wait(5)
        loader.start(second, QFont())
    finally:
        release.set()
    _wait(qapp, lambda: bool(ready) and bool(destroyed))
    assert len(ready) == 1 and ready[0][0] == second
    assert ready[0][1].toPlainText() == "重新開啟完整內容"
    assert destroyed == owners
    assert owners[0] != qapp.thread()
    ready[0][1].deleteLater()
    loader.cancel()


def test_cancel_after_handoff_destroys_unclaimed_document_on_gui(qapp, tmp_path, monkeypatch):
    from app import text_loading

    destroyed = []

    class TrackedDocument(QTextDocument):
        def __init__(self, *args):
            super().__init__(*args)
            self.destroyed.connect(
                lambda: destroyed.append(QThread.currentThread()),
                Qt.ConnectionType.DirectConnection,
            )

    monkeypatch.setattr(text_loading, "QTextDocument", TrackedDocument)
    path = tmp_path / "long.txt"
    path.write_text("long" * 20_000, encoding="utf8")
    loader = TextLoader()
    ready = []
    loader.ready.connect(lambda *args: ready.append(args))
    loader.start(path, QFont())
    # Keep the GUI queue paused until the worker has moved its complete document.
    assert QThreadPool.globalInstance().waitForDone(10000)
    loader.cancel()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not ready
    assert destroyed == [qapp.thread()]


def test_long_line_threshold_handles_chunk_boundaries_without_truncation():
    from app.text_loading import _LONG_LINE_CHARS, _has_long_line

    size = _LONG_LINE_CHARS
    assert not _has_long_line("x" * (size - 1))
    assert _has_long_line("x" * size)
    assert _has_long_line("prefix\n" + "😀" * size + "\nend")
    assert not _has_long_line(("x" * (size - 1) + "\n") * 3)


def test_deleted_loader_still_disposes_transferred_document_on_gui(qapp, tmp_path, monkeypatch):
    from shiboken6 import delete

    from app import text_loading

    destroyed = []

    class TrackedDocument(QTextDocument):
        def __init__(self, *args):
            super().__init__(*args)
            self.destroyed.connect(
                lambda: destroyed.append(QThread.currentThread()),
                Qt.ConnectionType.DirectConnection,
            )

    monkeypatch.setattr(text_loading, "QTextDocument", TrackedDocument)
    path = tmp_path / "long.txt"
    path.write_text("x" * 70_000, encoding="utf8")
    loader = TextLoader()
    loader.start(path, QFont())
    assert QThreadPool.globalInstance().waitForDone(10000)
    delete(loader)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert destroyed == [qapp.thread()]
