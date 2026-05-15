"""Recent files panel."""

import subprocess
from pathlib import Path

from PyQt6.QtCore import QPoint, QSettings, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QMenu

from .theme import LIGHT, Theme, collection_stylesheet

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
_MAX = 10


class RecentFilesView(QListWidget):
    def __init__(self, on_file_selected, parent=None):
        super().__init__(parent)
        self._on_file_selected = on_file_selected
        self._theme = LIGHT
        self.apply_theme(LIGHT)
        self.itemClicked.connect(self._on_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._refresh()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def add(self, filepath: str):
        paths = self._load()
        fp = str(Path(filepath).resolve())
        if fp in paths:
            paths.remove(fp)
        paths.insert(0, fp)
        self._save(paths[:_MAX])
        self._refresh()

    def clear_all(self):
        self._save([])
        self._refresh()

    def _refresh(self):
        self.clear()
        has_items = False
        for p in self._load():
            path = Path(p)
            if not path.exists():
                continue
            item = QListWidgetItem(path.name)
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.addItem(item)
            has_items = True

        if not has_items:
            item = QListWidgetItem("尚無最近開啟的檔案")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.addItem(item)

    def _show_context_menu(self, pos: QPoint):
        item = self.itemAt(pos)
        if not item or not item.flags() & Qt.ItemFlag.ItemIsEnabled:
            return

        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        open_act = QAction("在檔案總管中顯示", self)
        open_act.triggered.connect(lambda: self._open_location(item))
        menu.addAction(open_act)

        menu.addSeparator()

        remove_act = QAction("從最近清單移除", self)
        remove_act.triggered.connect(lambda: self._remove_item(item))
        menu.addAction(remove_act)

        menu.exec(self.mapToGlobal(pos))

    def _menu_stylesheet(self) -> str:
        theme = self._theme
        return f"""
QMenu {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 4px;
    color: {theme.text};
}}
QMenu::item {{
    padding: 6px 20px;
    color: {theme.text};
}}
QMenu::item:selected {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
"""

    def _open_location(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            subprocess.run(["explorer", "/select,", path])

    def _remove_item(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        paths = self._load()
        if path in paths:
            paths.remove(path)
            self._save(paths)
        self._refresh()

    def _on_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).exists():
            self._on_file_selected(path)

    @staticmethod
    def _load() -> list[str]:
        return QSettings(_ORG, _APP).value("recent_files", []) or []

    @staticmethod
    def _save(paths: list[str]):
        QSettings(_ORG, _APP).setValue("recent_files", paths)
