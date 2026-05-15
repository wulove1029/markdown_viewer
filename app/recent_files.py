"""Recent files panel."""

import subprocess
from pathlib import Path
from PyQt6.QtWidgets import QListWidget, QListWidgetItem, QMenu
from PyQt6.QtCore import Qt, QSettings, QPoint
from PyQt6.QtGui import QAction

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
_MAX = 10


class RecentFilesView(QListWidget):
    def __init__(self, on_file_selected, parent=None):
        super().__init__(parent)
        self._on_file_selected = on_file_selected
        self.setStyleSheet("""
            QListWidget { background: #f5f5f2; border: none; font-size: 13px; }
            QListWidget::item { padding: 6px 10px; color: #333;
                                border-bottom: 1px solid #ebebea; }
            QListWidget::item:hover { background: #e8e6fa; color: #5a4faf; }
            QListWidget::item:selected { background: #dddaf7; color: #3d349e; }
        """)
        self.itemClicked.connect(self._on_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._refresh()

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
        for p in self._load():
            path = Path(p)
            if not path.exists():
                continue
            item = QListWidgetItem(path.name)
            item.setToolTip(p)
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.addItem(item)

    def _show_context_menu(self, pos: QPoint):
        item = self.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #f5f5f2; border: 1px solid #d5d5d0; border-radius: 4px; }
            QMenu::item { padding: 6px 20px; color: #333; font-size: 13px; }
            QMenu::item:selected { background: #e8e6fa; color: #5a4faf; }
        """)
        open_act = QAction("開啟檔案位置", self)
        open_act.triggered.connect(lambda: self._open_location(item))
        menu.addAction(open_act)
        menu.addSeparator()
        remove_act = QAction("移除此紀錄", self)
        remove_act.triggered.connect(lambda: self._remove_item(item))
        menu.addAction(remove_act)
        menu.exec(self.mapToGlobal(pos))

    def _open_location(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            # 開啟 Windows 檔案總管並選取該檔案
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
