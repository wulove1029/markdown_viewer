"""Plain-text Markdown editor shown when edit mode is active."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import QPlainTextEdit

from .md_highlighter import MarkdownHighlighter
from .theme import LIGHT, Theme


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

    def set_content(self, text: str):
        self.setPlainText(text)
        self.document().setModified(False)

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
