"""Sidebar panel listing all tags across the library, with file counts."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .theme import LIGHT, Theme, collection_stylesheet


class TagsPanel(QWidget):
    def __init__(self, on_tag_selected, parent=None):
        super().__init__(parent)
        # on_tag_selected(tag): "" clears the filter.
        self._on_tag_selected = on_tag_selected
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self._list)

        self.apply_theme(LIGHT)
        self.set_tags([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_tags(self, tag_counts):
        self._list.clear()
        clear_item = QListWidgetItem("全部（清除篩選）")
        clear_item.setData(Qt.ItemDataRole.UserRole, "")
        self._list.addItem(clear_item)
        if not tag_counts:
            empty = QListWidgetItem("尚無標籤")
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(empty)
            return
        for tag, count in tag_counts:
            item = QListWidgetItem(f"#{tag}　·　{count}")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self._list.addItem(item)

    def set_active(self, tag: str):
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == tag:
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                return

    def _on_clicked(self, item: QListWidgetItem):
        tag = item.data(Qt.ItemDataRole.UserRole)
        if tag is not None:
            self._on_tag_selected(tag)
