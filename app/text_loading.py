"""Cancelable TXT loads with a separate path for pathological long paragraphs."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont, QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import QPlainTextDocumentLayout
from shiboken6 import delete

from . import background_jobs
from .md_converter import _decode_bytes

ASYNC_TEXT_BYTES = 256 * 1024
_CHUNK_CHARS = 32 * 1024
_BUILD_BUDGET_S = 0.008
_READ_CHUNK_BYTES = 256 * 1024
_LONG_LINE_CHARS = 64 * 1024
_NO_WRAP_PROPERTY = "text_loader_no_wrap"


def text_document_needs_no_wrap(document):
    """Let the host select horizontal scrolling before attaching long-line TXT."""
    return bool(document.property(_NO_WRAP_PROPERTY))


def _has_long_line(text):
    # A chunk containing a newline cannot contain an entire threshold-size
    # paragraph internally. Only its prefix can continue the previous suffix.
    suffix = 0
    for offset in range(0, len(text), _LONG_LINE_CHARS):
        chunk = text[offset:offset + _LONG_LINE_CHARS]
        first = chunk.find("\n")
        if first < 0:
            suffix += len(chunk)
            if suffix >= _LONG_LINE_CHARS:
                return True
        else:
            if suffix + first >= _LONG_LINE_CHARS:
                return True
            suffix = len(chunk) - chunk.rfind("\n") - 1
    return False


@dataclass
class _PreparedText:
    text: str | None
    encoding: str
    newline: str
    signature: tuple[int, int]
    document: QTextDocument | None = None


def _read_cancelable(path, cancel):
    """Keep the established codec/newline contract with cancellation between reads."""
    chunks = []
    with path.open("rb") as source:
        while not cancel.is_set():
            chunk = source.read(_READ_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
    if cancel.is_set():
        return None
    decoded = _decode_bytes(b"".join(chunks))
    if decoded is None or cancel.is_set():
        return None
    text, encoding = decoded
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline


class _ReadSignals(QObject):
    done = Signal(int, object, object, object)
    delivered = Signal(int, object, object, object)

    def __init__(self):
        # Outlive a cancelled/deleted loader until every moved document reaches
        # its GUI owner. Unclaimed documents are destroyed with this broker.
        super().__init__(QCoreApplication.instance())
        self.delivered.connect(self._dispatch, Qt.ConnectionType.QueuedConnection)

    @Slot(int, object, object, object)
    def _dispatch(self, generation, path, payload, error):
        if payload is not None and payload.document is not None:
            payload.document.setParent(self)
        self.done.emit(generation, path, payload, error)
        self.deleteLater()


class _ReadText(QRunnable):
    def __init__(self, generation, path, cancel, font, gui_thread):
        super().__init__()
        self.generation, self.path, self.cancel = generation, path, cancel
        self.signals = _ReadSignals()
        self.font = QFont(font)
        self.gui_thread = gui_thread

    def run(self):
        document = None
        payload = error = None
        try:
            if self.cancel.is_set():
                return
            before = self.path.stat()
            result = _read_cancelable(self.path, self.cancel)
            if self.cancel.is_set():
                return
            after = self.path.stat()
            signature = (after.st_mtime_ns, after.st_size)
            if (before.st_mtime_ns, before.st_size) != signature:
                raise OSError("文件在讀取時已變更，請重新載入。")
            if result is None:
                raise OSError("無法讀取檔案編碼。")
            text, encoding, newline = result
            if _has_long_line(text):
                # Repeatedly appending to the same QTextBlock reshapes its
                # growing contents (quadratic work). Build that document once
                # on its exclusive owner thread, then transfer ownership.
                document = QTextDocument()
                document.setDocumentLayout(QPlainTextDocumentLayout(document))
                document.setDefaultFont(self.font)
                document.setUndoRedoEnabled(False)
                option = document.defaultTextOption()
                option.setWrapMode(QTextOption.WrapMode.NoWrap)
                document.setDefaultTextOption(option)
                document.setPlainText(text)
                if self.cancel.is_set():
                    return
                document.setUndoRedoEnabled(True)
                document.setModified(False)
                document.setProperty(_NO_WRAP_PROPERTY, True)
                payload = _PreparedText(None, encoding, newline, signature, document)
                if not document.moveToThread(self.gui_thread):
                    raise RuntimeError("無法移交純文字文件，請重新載入。")
                document = None  # Never touch the transferred object here.
            else:
                payload = _PreparedText(text, encoding, newline, signature)
        except (OSError, UnicodeError, RuntimeError, MemoryError) as exc:
            payload, error = None, str(exc)
        finally:
            if document is not None:
                delete(document)  # Still owned exclusively by this worker.
            self.signals.delivered.emit(self.generation, self.path, payload, error)


class TextLoader(QObject):
    ready = Signal(object, object, str, str, object)
    failed = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0
        self._cancel = threading.Event()
        self.destroyed.connect(self._cancel.set)
        self._document = None
        self._building = False
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
        self._building = False
        if self._document is not None:
            self._document.deleteLater()
            self._document = None
        self._path = None

    def start(self, path: Path, font):
        self.cancel()
        self.destroyed.disconnect(self._cancel.set)
        self._cancel = threading.Event()
        self.destroyed.connect(self._cancel.set)
        self._path = path
        self._font = font
        worker = _ReadText(self._generation, path, self._cancel, font, self.thread())
        worker.signals.done.connect(self._read_done)
        background_jobs.pool().start(worker, background_jobs.VISIBLE)

    def _read_done(self, generation, path, payload, error):
        if (generation != self._generation or self._cancel.is_set() or path != self._path
                or self._building):
            return
        if error:
            self.failed.emit(path, error)
            return
        if payload is None:
            return
        if payload.document is not None:
            document = payload.document
            document.setParent(None)
            self.ready.emit(path, document, payload.encoding, payload.newline, payload.signature)
            return
        self._text = payload.text
        self._encoding, self._newline, self._signature = (
            payload.encoding, payload.newline, payload.signature)
        self._offset = 0
        self._building = True
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
        deadline = time.perf_counter() + _BUILD_BUDGET_S
        while self._offset < len(self._text):
            end = min(len(self._text), self._offset + _CHUNK_CHARS)
            cursor.insertText(self._text[self._offset:end])
            self._offset = end
            # A Qt insertion itself is not interruptible. Checking time after
            # each bounded insertion avoids doing more work after an overrun.
            if end < len(self._text) and time.perf_counter() >= deadline:
                self._timer.start(0)
                return
            if self._cancel.is_set():
                return
        self._document = None
        self._text = ""
        self._building = False
        document.setParent(None)
        document.setUndoRedoEnabled(True)
        document.setModified(False)
        self.ready.emit(self._path, document, self._encoding, self._newline, self._signature)
