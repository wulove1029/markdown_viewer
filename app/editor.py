"""Plain-text Markdown editor shown when edit mode is active."""

from pathlib import Path

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QImage, QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit

from .image_paste import (
    import_image_file,
    is_image_file,
    markdown_image_link,
    save_clipboard_image,
)
from .md_highlighter import MarkdownHighlighter
from .theme import LIGHT, Theme
from .wikilink_completion import active_query, filter_completions


# QTextCursor.selectedText() encodes line breaks as U+2029, not a newline.
_PARAGRAPH_SEP = chr(0x2029)


class EditorView(QPlainTextEdit):
    modified_changed = Signal(bool)
    image_status = Signal(str)  # user-facing status bar message
    translate_requested = Signal(str)  # selected text to translate

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFont("Cascadia Code")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        self.setTabStopDistance(QFontMetricsF(font).horizontalAdvance(" ") * 4)
        self.document().modificationChanged.connect(self.modified_changed)
        self._highlighter = MarkdownHighlighter(self.document(), LIGHT)
        self._wikilink_candidates: list[str] = []
        self._document_path: str | None = None
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated[str].connect(self._insert_wikilink_completion)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        # selectedText() joins wrapped lines with U+2029; restore real newlines
        # so the provider sees the paragraph the user actually highlighted.
        selection = (
            self.textCursor().selectedText().replace(_PARAGRAPH_SEP, "\n").strip()
        )
        if selection:
            menu.addSeparator()
            action = menu.addAction("翻譯選取內容")
            action.triggered.connect(
                lambda _checked=False, text=selection: (
                    self.translate_requested.emit(text)
                )
            )
        menu.exec(event.globalPos())

    def set_content(self, text: str):
        self._completer.popup().hide()
        self.setPlainText(text)
        self.document().setModified(False)

    def set_document_path(self, path: str | Path | None) -> None:
        """Tell the editor which file it is editing (for asset placement)."""
        self._document_path = str(path) if path else None

    # ---------------- image paste / drag-and-drop ----------------
    @staticmethod
    def _mime_has_image(mime) -> bool:
        """True when *mime* holds raster image data or all-image file URLs."""
        if mime.hasImage():
            return True
        if mime.hasUrls():
            urls = mime.urls()
            return bool(urls) and all(
                u.isLocalFile() and is_image_file(u.toLocalFile()) for u in urls
            )
        return False

    def _insert_image_mime(self, mime) -> None:
        if not self._document_path:
            self.image_status.emit("請先儲存文件才能貼入圖片")
            return
        links: list[str] = []
        if mime.hasImage():
            qimage = mime.imageData()
            if not isinstance(qimage, QImage):
                qimage = QImage(qimage)
            rel = save_clipboard_image(qimage, self._document_path)
            if rel is None:
                self.image_status.emit("圖片儲存失敗，請重試")
                return
            links.append(markdown_image_link(rel))
        elif mime.hasUrls():
            for url in mime.urls():
                rel = import_image_file(url.toLocalFile(), self._document_path)
                links.append(markdown_image_link(rel))
        if links:
            self.insertPlainText("\n".join(links))

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802 (Qt override)
        if self._mime_has_image(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 (Qt override)
        if self._mime_has_image(source):
            self._insert_image_mime(source)
            return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if self._mime_has_image(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 (Qt override)
        if self._mime_has_image(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if self._mime_has_image(mime):
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            self._insert_image_mime(mime)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def set_wikilink_candidates(self, candidates) -> None:
        self._wikilink_candidates = list(candidates)
        if self.isVisible() and self.hasFocus():
            self._show_wikilink_completions()

    def _query_before_cursor(self) -> str | None:
        cursor = self.textCursor()
        cursor.movePosition(
            QTextCursor.MoveOperation.StartOfLine,
            QTextCursor.MoveMode.KeepAnchor,
        )
        return active_query(cursor.selectedText())

    def _show_wikilink_completions(self) -> None:
        query = self._query_before_cursor()
        matches = (
            filter_completions(self._wikilink_candidates, query)
            if query is not None
            else []
        )
        popup = self._completer.popup()
        if not matches:
            popup.hide()
            return

        self._completion_model.setStringList(matches)
        self._completer.setCompletionPrefix("")
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(max(260, min(520, popup.sizeHintForColumn(0) + 30)))
        self._completer.complete(rect)

    def _insert_wikilink_completion(self, completion: str) -> None:
        query = self._query_before_cursor()
        if query is None:
            return
        cursor = self.textCursor()
        if query:
            cursor.movePosition(
                QTextCursor.MoveOperation.PreviousCharacter,
                QTextCursor.MoveMode.KeepAnchor,
                len(query),
            )
        cursor.insertText(f"{completion}]]")
        self.setTextCursor(cursor)
        self._completer.popup().hide()

    def keyPressEvent(self, event):
        popup = self._completer.popup()
        key = event.key()
        if popup.isVisible() and key == Qt.Key.Key_Escape:
            popup.hide()
            event.accept()
            return
        if popup.isVisible() and key in (
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Backtab,
        ):
            event.ignore()
            return
        super().keyPressEvent(event)
        self._show_wikilink_completions()

    def is_modified(self) -> bool:
        return self.document().isModified()

    def mark_saved(self):
        self.document().setModified(False)

    def apply_theme(self, theme: Theme):
        self._highlighter.set_theme(theme)
        self.setStyleSheet(
            f"""
QPlainTextEdit {{
    background: {theme.window};
    border: none;
    color: {theme.text};
    font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
    font-size: 14px;
    line-height: 1.6;
    padding: 16px 24px;
    selection-background-color: {theme.accent_soft};
    selection-color: {theme.text};
}}
"""
        )
