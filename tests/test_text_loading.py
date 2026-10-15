"""Large text loads publish only complete documents and reject stale reads."""
import time

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest

from app.text_loading import TextLoader


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
                      (("line\n" * 100_000, "utf-8", "\n"), (0, 0)), None)
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
    loader._read_done(old_generation, first, (("old", "utf-8", "\n"), (0, 0)), None)
    _wait(qapp, lambda: bool(ready))
    assert len(ready) == 1
    assert ready[0][0] == second
    assert ready[0][1].toPlainText() == "new"
    ready[0][1].deleteLater()
    loader.cancel()
    assert QThreadPool.globalInstance().waitForDone(10000)
