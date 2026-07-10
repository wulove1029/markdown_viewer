"""Plain-text Markdown editor shown when edit mode is active."""

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit

from .md_highlighter import MarkdownHighlighter
from .theme import LIGHT, Theme
from .wikilink_completion import active_query, filter_completions


class EditorView(QPlainTextEdit):
    modified_changed = Signal(bool)

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
        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.activated[str].connect(self._insert_wikilink_completion)

    def set_content(self, text: str):
        self._completer.popup().hide()
        self.setPlainText(text)
        self.document().setModified(False)

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
