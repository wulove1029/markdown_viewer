"""Cancelable disk reads and bounded GUI document construction for large TXT."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout

from .md_converter import read_text_detailed

ASYNC_TEXT_BYTES = 256 * 1024
_CHUNK_CHARS = 32 * 1024


class _ReadSignals(QObject):
    done = Signal(int, object, object, object)


class _ReadText(QRunnable):
    def __init__(self, generation, path, cancel):
        super().__init__()
        self.generation, self.path, self.cancel = generation, path, cancel
        self.signals = _ReadSignals()

    def run(self):
        try:
            before = self.path.stat()
            result = read_text_detailed(self.path) if not self.cancel.is_set() else None
            after = self.path.stat()
            signature = (after.st_mtime_ns, after.st_size)
            if (before.st_mtime_ns, before.st_size) != signature:
                raise OSError("文件在讀取時已變更，請重新載入。")
            if result is None:
                raise OSError("無法讀取檔案編碼。")
            payload = (result, signature)
            error = None
        except (OSError, UnicodeError) as exc:
            payload, error = None, str(exc)
        if not self.cancel.is_set():
            self.signals.done.emit(self.generation, self.path, payload, error)


class TextLoader(QObject):
    ready = Signal(object, object, str, str, object)
    failed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._cancel = threading.Event()
        self._document = None
        self._text = ""
        self._path = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._append_chunk)

    def cancel(self):
        self._generation += 1
        self._cancel.set()
        self._timer.stop()
        self._text = ""
        if self._document is not None:
            self._document.deleteLater()
            self._document = None
        self._path = None

    def start(self, path: Path, font):
        self.cancel()
        self._cancel = threading.Event()
        self._path = path
        self._font = font
        worker = _ReadText(self._generation, path, self._cancel)
        worker.signals.done.connect(self._read_done)
        QThreadPool.globalInstance().start(worker)

    def _read_done(self, generation, path, payload, error):
        if generation != self._generation or self._cancel.is_set() or path != self._path:
            return
        if error:
            self.failed.emit(path, error)
            return
        (self._text, self._encoding, self._newline), self._signature = payload
        self._offset = 0
        document = QTextDocument(self)
        document.setDocumentLayout(QPlainTextDocumentLayout(document))
        document.setDefaultFont(self._font)
        document.setUndoRedoEnabled(False)
        self._document = document
        self._timer.start(0)

    def _append_chunk(self):
        document = self._document
        if document is None or self._cancel.is_set():
            return
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        end = min(len(self._text), self._offset + _CHUNK_CHARS)
        cursor.insertText(self._text[self._offset:end])
        self._offset = end
        if end < len(self._text):
            self._timer.start(0)
            return
        self._document = None
        self._text = ""
        document.setParent(None)
        document.setUndoRedoEnabled(True)
        document.setModified(False)
        self.ready.emit(self._path, document, self._encoding, self._newline, self._signature)
