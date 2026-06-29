"""Fuzzy quick-open palette (Ctrl+P) for jumping between documents."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from .theme import Theme, collection_stylesheet


def fuzzy_score(query: str, text: str) -> float | None:
    """Subsequence fuzzy match. Returns a score (higher = better) or None.

    All characters of *query* must appear in *text* in order. Consecutive and
    start-of-word matches score higher; shorter targets are mildly preferred.
    """
    if not query:
        return 0.0
    text_low = text.lower()
    pos = 0
    score = 0.0
    prev = -2
    for ch in query.lower():
        idx = text_low.find(ch, pos)
        if idx == -1:
            return None
        if idx == prev + 1:
            score += 5.0
        if idx == 0 or text_low[idx - 1] in " /\\_-.":
            score += 3.0
        score += 1.0
        prev = idx
        pos = idx + 1
    return score - len(text) * 0.01


class QuickOpenDialog(QDialog):
    def __init__(self, candidates: list[tuple[str, str]], theme: Theme, parent=None):
        super().__init__(parent)
        # candidates: list of (display_name, full_path)
        self._candidates = candidates
        self._selected: str | None = None

        self.setWindowTitle("快速開啟")
        self.setModal(True)
        self.resize(580, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("輸入檔名片段…（↑↓ 選擇，Enter 開啟，Esc 取消）")
        self._list = QListWidget()
        layout.addWidget(self._input)
        layout.addWidget(self._list)

        self._input.textChanged.connect(self._refilter)
        self._input.returnPressed.connect(self._accept_current)
        self._list.itemClicked.connect(lambda _it: self._accept_current())
        self._input.installEventFilter(self)

        self._apply_theme(theme)
        self._refilter("")
        self._input.setFocus()

    def _apply_theme(self, theme: Theme):
        self.setStyleSheet(
            f"QDialog {{ background: {theme.window}; }}"
            f"QLineEdit {{ background: {theme.surface}; border: 1px solid {theme.border};"
            f" border-radius: 6px; color: {theme.text}; padding: 6px 10px; font-size: 14px; }}"
            f"QLineEdit:focus {{ border-color: {theme.accent}; }}"
        )
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def eventFilter(self, obj, event):
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._list.count():
                row = self._list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                self._list.setCurrentRow(max(0, min(row, self._list.count() - 1)))
                return True
        return super().eventFilter(obj, event)

    def _refilter(self, text: str):
        self._list.clear()
        scored: list[tuple[float, str, str]] = []
        for name, path in self._candidates:
            score = fuzzy_score(text, name)
            if score is None:
                score = fuzzy_score(text, path)
            if score is not None:
                scored.append((score, name, path))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _score, name, path in scored[:200]:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _accept_current(self):
        item = self._list.currentItem()
        if item:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def selected_path(self) -> str | None:
        return self._selected
