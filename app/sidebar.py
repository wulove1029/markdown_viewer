"""Left-side panel: recent files + file browser."""

from pathlib import Path
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QTreeView, QAbstractItemView
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtCore import Qt, QDir, QSortFilterProxyModel, QModelIndex

from .recent_files import RecentFilesView


class MdFilterProxy(QSortFilterProxyModel):
    """Show only directories and .md files; hide hidden (dot) items."""

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model: QFileSystemModel = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        name = model.fileName(index)
        if name.startswith("."):
            return False
        if model.isDir(index):
            return True
        return name.lower().endswith(".md")


class SidebarView(QWidget):
    def __init__(self, on_file_selected, parent=None):
        super().__init__(parent)
        self._on_file_selected = on_file_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 最近開啟
        self._recent = RecentFilesView(on_file_selected=self._open_file)
        layout.addWidget(self._recent)

        # 檔案樹
        self._tree = self._build_tree()
        layout.addWidget(self._tree)

    def _build_tree(self) -> QTreeView:
        fs_model = QFileSystemModel()
        fs_model.setRootPath(QDir.homePath())
        fs_model.setFilter(
            QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot
        )

        proxy = MdFilterProxy()
        proxy.setSourceModel(fs_model)

        tree = QTreeView()
        tree.setModel(proxy)
        tree.setRootIndex(proxy.mapFromSource(fs_model.index(QDir.homePath())))
        for col in range(1, 4):
            tree.hideColumn(col)
        tree.setHeaderHidden(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setAnimated(True)
        tree.setExpandsOnDoubleClick(True)
        tree.clicked.connect(lambda idx: self._on_tree_clicked(idx, fs_model, proxy))

        self._fs_model = fs_model
        self._proxy = proxy
        self._tree_widget = tree
        return tree

    def _on_tree_clicked(self, proxy_index: QModelIndex,
                          fs_model: QFileSystemModel,
                          proxy: MdFilterProxy):
        source_index = proxy.mapToSource(proxy_index)
        path = fs_model.filePath(source_index)
        if path.lower().endswith(".md"):
            self._open_file(path)

    def _open_file(self, filepath: str):
        self._recent.add(filepath)
        self._on_file_selected(filepath)

    def navigate_to(self, folder: str | Path):
        source_index = self._fs_model.index(str(folder))
        proxy_index = self._proxy.mapFromSource(source_index)
        self._tree_widget.setRootIndex(proxy_index)
        self._tree_widget.expand(proxy_index)
