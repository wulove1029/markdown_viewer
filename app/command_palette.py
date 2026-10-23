"""Keyboard-first discovery of the application's existing actions."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
)


def action_entries(menus):
    """Flatten menus without cloning actions or changing their enabled state."""
    entries = []
    seen = set()

    def visit(actions, prefix):
        for action in actions:
            if action.isSeparator() or not action.isVisible():
                continue
            label = action.text().split("\t")[0].replace("&", "").strip()
            if action.menu() is not None:
                visit(action.menu().actions(), [*prefix, label])
            elif id(action) not in seen and label:
                seen.add(id(action))
                entries.append((" › ".join([*prefix, label]), action))

    for menu in menus:
        if isinstance(menu, QMenu):
            visit(menu.actions(), [menu.title().replace("&", "")])
    return entries


class CommandPalette(QDialog):
    """Execute a live QAction after the modal palette gives focus back."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("命令搜尋")
        self.resize(620, 430)
        self.entries = list(entries)
        self._composing = False
        self.selected_action: QAction | None = None
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("輸入操作名稱，例如：儲存、表格、專注…")
        self.query.setAccessibleName("搜尋命令")
        self.query.setClearButtonEnabled(True)
        self.results = QListWidget()
        self.results.setAccessibleName("符合的命令")
        self.results.setUniformItemSizes(True)
        self.hint = QLabel("↑↓ 選擇 · Enter 執行 · Esc 返回")
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        layout.addWidget(self.hint)
        self.query.textChanged.connect(self._filter)
        self.query.installEventFilter(self)
        self.results.itemActivated.connect(self._accept_item)
        self._filter("")
        self.query.setFocus()

    def _filter(self, text):
        terms = text.casefold().split()
        self.results.clear()
        for label, action in self.entries:
            searchable = (label + " " + str(action.data() or "")).casefold()
            if not all(term in searchable for term in terms):
                continue
            hint = action.text().partition("\t")[2]
            item = QListWidgetItem(label + (f"    {hint}" if hint else ""))
            item.setData(Qt.ItemDataRole.UserRole, action)
            if not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(action.toolTip() or "目前文件狀態無法使用這個命令")
            self.results.addItem(item)
        for row in range(self.results.count()):
            if self.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.results.setCurrentRow(row)
                break
        self.hint.setText(
            "↑↓ 選擇 · Enter 執行 · Esc 返回" if self.results.count()
            else "沒有符合的命令，請換個關鍵字"
        )

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.query and event.type() == QEvent.Type.InputMethod:
            self._composing = bool(event.preeditString())
        if watched is self.query and event.type() == QEvent.Type.KeyPress:
            if self._composing:
                return False
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                row = self.results.currentRow() + step
                while 0 <= row < self.results.count():
                    if self.results.item(row).flags() & Qt.ItemFlag.ItemIsEnabled:
                        self.results.setCurrentRow(row)
                        break
                    row += step
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_item(self.results.currentItem())
                return True
        return super().eventFilter(watched, event)

    def _accept_item(self, item):
        if item is None:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        if action is not None and action.isEnabled():
            self.selected_action = action
            self.accept()
