"""Fuzzy quick-open palette (Ctrl+P) for jumping between documents."""

from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
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
    def __init__(self, candidates: list[tuple[str, str]], theme: Theme, parent=None,
                 *, locations: dict[str, str] | None = None, loading: bool = False):
        super().__init__(parent)
        # candidates: list of (display_name, full_path)
        self._candidates = candidates
        self._selected: str | None = None
        self._locations = locations or {}
        self._loading = loading
        self._open_requested = False

        self.setWindowTitle("快速開啟")
        self.setModal(True)
        self.resize(580, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._input = QLineEdit()
        self._input.setPlaceholderText("輸入檔名片段…（↑↓ 選擇，Enter 開啟，Esc 取消）")
        self._list = QListWidget()
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._open_button = QPushButton("開啟其他文件…")
        layout.addWidget(self._input)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._status)
        layout.addWidget(self._open_button)

        self._input.textChanged.connect(self._refilter)
        self._input.returnPressed.connect(self._accept_current)
        self._list.itemClicked.connect(lambda _it: self._accept_current())
        self._input.installEventFilter(self)
        self._list.installEventFilter(self)
        self._open_button.clicked.connect(self._request_open)

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
        if obj is self._list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._list.count():
                row = self._list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                self._list.setCurrentRow(max(0, min(row, self._list.count() - 1)))
                return True
        return super().eventFilter(obj, event)

    def _refilter(self, text: str):
        previous = self._list.currentItem()
        selected = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        self._list.clear()
        scored: list[tuple[float, str, str]] = []
        for name, path in self._candidates:
            score = fuzzy_score(text, name)
            if score is None:
                score = fuzzy_score(text, self._locations.get(path) or path)
            if score is not None:
                scored.append((score, name, path))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _score, name, path in scored[:200]:
            location = self._locations.get(path) or str(Path(path).parent)
            item = QListWidgetItem(f"{name}\n{location}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)
            if path == selected:
                self._list.setCurrentItem(item)
        if self._list.count() and self._list.currentRow() < 0:
            self._list.setCurrentRow(0)
        if self._loading:
            self._status.setText("正在更新文件庫清單；可先開啟下方已找到的文件。")
        elif not self._candidates:
            self._status.setText("尚無文件；可開啟文件，或在側欄加入文件庫。")
        elif not scored:
            self._status.setText("沒有符合的文件，請換個檔名或路徑片段。")
        else:
            self._status.setText(f"找到 {len(scored)} 份文件" +
                                 ("，顯示前 200 份；輸入更多文字可縮小範圍。" if len(scored) > 200 else ""))

    def set_candidates(self, candidates, *, locations=None, loading=False):
        self._candidates = candidates
        self._locations = locations or {}
        self._loading = loading
        self._refilter(self._input.text())

    def _request_open(self):
        self._open_requested = True
        self.accept()

    def open_requested(self) -> bool:
        return self._open_requested

    def _accept_current(self):
        item = self._list.currentItem()
        if item:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def selected_path(self) -> str | None:
        return self._selected
