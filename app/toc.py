"""Right-side Table of Contents panel."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .theme import LIGHT, Theme, collection_stylesheet


class TocView(QWidget):
    def __init__(self, on_anchor_clicked, parent=None):
        super().__init__(parent)
        self._on_anchor_clicked = on_anchor_clicked
        self._anchors: list[str] = []
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self.apply_theme(LIGHT)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def update_headings(self, headings: list[tuple[int, str, str]]):
        """headings = list of (level, text, anchor_id)"""
        self._list.clear()
        self._anchors = []

        if not headings:
            item = QListWidgetItem("目前文件沒有標題")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return

        for level, text, anchor in headings:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, anchor)

            font = QFont()
            if level == 1:
                font.setBold(True)
                font.setPointSize(12)
            elif level == 2:
                font.setPointSize(11)
            else:
                font.setPointSize(10)
                item.setForeground(QColor(self._theme.text_subtle))

            item.setFont(font)
            item.setText("  " * (level - 1) + text)
            self._list.addItem(item)
            self._anchors.append(anchor)

    def set_active_anchor(self, anchor: str):
        if not anchor:
            self._list.clearSelection()
            return
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == anchor:
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                return

    def _on_item_clicked(self, item: QListWidgetItem):
        anchor = item.data(Qt.ItemDataRole.UserRole)
        if anchor:
            self._on_anchor_clicked(anchor)
