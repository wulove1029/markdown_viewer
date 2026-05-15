"""File tree browser — shows folders and .md files only."""

from pathlib import Path
from PyQt6.QtWidgets import QTreeView, QAbstractItemView
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtCore import QDir, QSortFilterProxyModel, QModelIndex


class MdFilterProxy(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: QFileSystemModel = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name.startswith("."):
            return False
        if model.isDir(index):
            return True
        return name.lower().endswith(".md")


class FileBrowserView(QTreeView):
    def __init__(self, on_file_selected, parent=None):
        super().__init__(parent)
        self._on_file_selected = on_file_selected

        self._fs = QFileSystemModel()
        self._fs.setRootPath(QDir.homePath())
        self._fs.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )

        self._proxy = MdFilterProxy()
        self._proxy.setSourceModel(self._fs)

        self.setModel(self._proxy)
        self.setRootIndex(self._proxy.mapFromSource(
            self._fs.index(QDir.homePath())
        ))
        for col in range(1, 4):
            self.hideColumn(col)
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAnimated(True)
        self.setExpandsOnDoubleClick(True)
        self.setStyleSheet("""
            QTreeView { background: #f5f5f2; border: none; font-size: 13px; }
            QTreeView::item { padding: 3px 4px; color: #333; }
            QTreeView::item:hover { background: #e8e6fa; color: #5a4faf; }
            QTreeView::item:selected { background: #dddaf7; color: #3d349e; }
        """)
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self, proxy_index: QModelIndex):
        source_index = self._proxy.mapToSource(proxy_index)
        path = self._fs.filePath(source_index)
        if path.lower().endswith(".md"):
            self._on_file_selected(path)

    def navigate_to(self, folder: str | Path):
        source_index = self._fs.index(str(folder))
        proxy_index = self._proxy.mapFromSource(source_index)
        self.setRootIndex(proxy_index)
        self.expand(proxy_index)
