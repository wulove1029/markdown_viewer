"""Document library browser for Markdown files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QUrl
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .document_libraries import (
    DocumentLibrary,
    DocumentLibraryStore,
    LibraryDocument,
    discover_cloud_library_paths,
    scan_library_documents,
)
from .theme import LIGHT, Theme, collection_stylesheet, svg_icon

_PATH_ROLE = Qt.ItemDataRole.UserRole
_LIBRARY_ROLE = Qt.ItemDataRole.UserRole.value + 1
_HEADER_ROLE = Qt.ItemDataRole.UserRole.value + 2


class FileBrowserView(QWidget):
    def __init__(self, on_file_selected, parent=None):
        super().__init__(parent)
        self.setObjectName("fileBrowser")
        self._on_file_selected = on_file_selected
        self._theme = LIGHT
        self._store = DocumentLibraryStore()
        self._libraries: list[DocumentLibrary] = []
        self._documents: list[LibraryDocument] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(4)

        self._add_btn = QPushButton("加入文件庫")
        self._add_btn.setObjectName("addLibraryButton")
        self._add_btn.setToolTip("新增文件庫資料夾")
        self._add_btn.setAccessibleName("新增文件庫資料夾")
        self._add_btn.setIconSize(QSize(18, 18))
        self._add_btn.clicked.connect(self._add_library)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setToolTip("重新掃描文件庫")
        self._refresh_btn.setAccessibleName("重新掃描文件庫")
        self._refresh_btn.setIconSize(QSize(18, 18))
        self._refresh_btn.clicked.connect(self.refresh_libraries)

        self._manage_btn = QPushButton("管理")
        self._manage_btn.setToolTip("管理文件庫來源")
        self._manage_btn.clicked.connect(self._manage_libraries)

        action_row.addWidget(self._add_btn)
        action_row.addWidget(self._refresh_btn)
        action_row.addStretch()
        action_row.addWidget(self._manage_btn)
        layout.addLayout(action_row)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("搜尋文件庫中的文件")
        self._filter.textChanged.connect(self._refresh_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._list, stretch=1)

        self._status = QLabel()
        self._status.setProperty("muted", True)
        layout.addWidget(self._status)

        self.apply_theme(LIGHT)
        self.refresh_libraries()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._add_btn.setIcon(svg_icon("folder-plus", theme.accent, 18))
        self._refresh_btn.setIcon(svg_icon("refresh", theme.text_muted, 18))
        self.setStyleSheet(self._stylesheet(theme))
        self._list.setStyleSheet(
            collection_stylesheet(theme, "QListWidget")
            + f"""
QListWidget::item {{
    min-height: 34px;
}}
"""
        )

    def navigate_to(self, folder: str | Path):
        self._select_path(Path(folder))

    def select_path(self, filepath: str | Path):
        self._select_path(Path(filepath))

    def refresh_libraries(self):
        self._libraries = self._store.load()
        self._documents = scan_library_documents(self._libraries)
        self._refresh_list()

    def _refresh_list(self):
        self._list.clear()
        query = self._filter.text().strip().casefold()

        if not self._libraries:
            self._filter.setEnabled(False)
            self._refresh_btn.setEnabled(False)
            self._add_empty_item("尚未加入文件庫\n按「加入文件庫」選擇資料夾")
            self._status.setText("尚未設定文件庫")
            return
        self._filter.setEnabled(True)
        self._refresh_btn.setEnabled(True)

        total_shown = 0
        missing_count = 0
        docs_by_library: dict[str, list[LibraryDocument]] = {}
        for doc in self._documents:
            if query and not self._matches_query(doc, query):
                continue
            docs_by_library.setdefault(doc.library_id, []).append(doc)

        for lib in self._libraries:
            root = Path(lib.path)
            if not root.exists():
                missing_count += 1
                if not query:
                    self._add_header_item(f"{lib.name}（找不到資料夾）")
                    self._add_empty_item(lib.path)
                continue

            docs = docs_by_library.get(lib.id, [])
            if query and not docs:
                continue

            self._add_header_item(f"{lib.name}（{len(docs)}）")
            if docs:
                for doc in docs:
                    self._add_file_item(doc)
                    total_shown += 1
            elif not query:
                self._add_empty_item("這個文件庫目前沒有支援的文件")

        if query and total_shown == 0:
            self._add_empty_item("沒有符合搜尋的文件")

        missing_text = f"，{missing_count} 個來源找不到" if missing_count else ""
        self._status.setText(
            f"{len(self._libraries)} 個文件庫，{len(self._documents)} 份文件{missing_text}"
        )

    def _add_header_item(self, text: str):
        item = QListWidgetItem(text)
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(self._theme.text_muted))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        item.setData(_HEADER_ROLE, True)
        self._list.addItem(item)

    def _add_file_item(self, doc: LibraryDocument):
        name = Path(doc.path).name
        relative = doc.relative_path.replace("\\", " / ")
        label = "PDF" if doc.kind == "pdf" else "MD"
        text = f"[{label}] {name}" if relative == name else f"[{label}] {name}\n{relative}"
        item = QListWidgetItem(text)
        item.setSizeHint(QSize(0, 48 if "\n" in text else 36))
        item.setToolTip(doc.path)
        item.setData(_PATH_ROLE, doc.path)
        item.setData(_LIBRARY_ROLE, doc.library_id)
        self._list.addItem(item)

    def _add_empty_item(self, text: str):
        item = QListWidgetItem(text)
        if "\n" in text:
            item.setSizeHint(QSize(0, 56))
        item.setForeground(QColor(self._theme.text_subtle))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._list.addItem(item)

    def _matches_query(self, doc: LibraryDocument, query: str) -> bool:
        haystack = " ".join(
            [
                Path(doc.path).name,
                doc.relative_path,
                doc.library_name,
                doc.path,
            ]
        ).casefold()
        return query in haystack

    def _on_clicked(self, item: QListWidgetItem):
        path = item.data(_PATH_ROLE)
        if path and Path(path).exists():
            self._on_file_selected(path)

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        path = item.data(_PATH_ROLE) if item else None
        if path:
            open_act = QAction("開啟文件", self)
            open_act.triggered.connect(lambda: self._on_file_selected(path))
            menu.addAction(open_act)

            reveal_act = QAction("在檔案總管中顯示", self)
            reveal_act.triggered.connect(lambda: self._open_location(path))
            menu.addAction(reveal_act)

            menu.addSeparator()

        add_act = QAction("新增文件庫資料夾", self)
        add_act.triggered.connect(self._add_library)
        menu.addAction(add_act)

        refresh_act = QAction("重新掃描文件庫", self)
        refresh_act.triggered.connect(self.refresh_libraries)
        menu.addAction(refresh_act)

        manage_act = QAction("管理文件庫", self)
        manage_act.triggered.connect(self._manage_libraries)
        menu.addAction(manage_act)

        menu.exec(self._list.mapToGlobal(pos))

    def _add_library(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "新增文件庫資料夾",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        _lib, added = self._store.add(folder)
        if not added:
            QMessageBox.information(self, "文件庫已存在", "這個資料夾已在文件庫中。")
        self.refresh_libraries()

    def _manage_libraries(self):
        dialog = LibraryManagerDialog(self._store, self._theme, self)
        dialog.exec()
        if dialog.changed:
            self.refresh_libraries()

    def _select_path(self, path: Path):
        target = str(path.resolve()) if path.exists() else str(path)
        for row in range(self._list.count()):
            item = self._list.item(row)
            item_path = item.data(_PATH_ROLE)
            if item_path and str(Path(item_path).resolve()) == target:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                break

    def _open_location(self, path: str):
        subprocess.run(["explorer", "/select,", path])

    def _stylesheet(self, theme: Theme) -> str:
        return f"""
QWidget#fileBrowser {{
    background: {theme.surface};
}}
QWidget#fileBrowser QLabel {{
    background: transparent;
    color: {theme.text};
}}
QWidget#fileBrowser QLabel[muted="true"] {{
    color: {theme.text_muted};
    font-size: 12px;
}}
QWidget#fileBrowser QLineEdit {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 32px;
    padding: 4px 10px;
}}
QWidget#fileBrowser QLineEdit:focus {{
    border-color: {theme.accent};
}}
QWidget#fileBrowser QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text_muted};
    min-height: 34px;
    min-width: 34px;
    padding: 0 8px;
}}
QWidget#fileBrowser QPushButton#addLibraryButton {{
    color: {theme.accent};
    border-color: {theme.border};
}}
QWidget#fileBrowser QPushButton#addLibraryButton:hover {{
    background: {theme.accent_soft};
    border-color: {theme.accent};
    color: {theme.text};
}}
QWidget#fileBrowser QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
    color: {theme.text};
}}
QWidget#fileBrowser QPushButton:pressed {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
"""

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


class LibraryManagerDialog(QDialog):
    def __init__(
        self,
        store: DocumentLibraryStore,
        theme: Theme = LIGHT,
        parent=None,
    ):
        super().__init__(parent)
        self._store = store
        self._theme = theme
        self.changed = False

        self.setWindowTitle("管理文件庫")
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        hint = QLabel(
            "可加入本機資料夾，也可直接加入 Google Drive for desktop 的同步資料夾。"
        )
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._update_button_state)
        layout.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        self._add_btn = QPushButton("新增資料夾")
        self._add_btn.clicked.connect(self._add_folder)
        self._detect_btn = QPushButton("偵測雲端資料夾")
        self._detect_btn.clicked.connect(self._detect_cloud_folders)
        self._open_btn = QPushButton("開啟資料夾")
        self._open_btn.clicked.connect(self._open_selected)
        self._remove_btn = QPushButton("移除")
        self._remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(self._add_btn)
        row.addWidget(self._detect_btn)
        row.addStretch()
        row.addWidget(self._open_btn)
        row.addWidget(self._remove_btn)
        layout.addLayout(row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self.apply_theme(theme)
        self._refresh()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(self._stylesheet(theme))
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def _refresh(self):
        self._list.clear()
        for lib in self._store.load():
            status = "可用" if Path(lib.path).exists() else "找不到資料夾"
            item = QListWidgetItem(f"{lib.name}\n{lib.path}\n{status}")
            item.setSizeHint(QSize(0, 66))
            item.setData(_LIBRARY_ROLE, lib.id)
            item.setToolTip(lib.path)
            if status != "可用":
                item.setForeground(QColor(self._theme.warning))
            self._list.addItem(item)

        if self._list.count() == 0:
            item = QListWidgetItem("尚未加入文件庫")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
        self._update_button_state()

    def _selected_library(self) -> DocumentLibrary | None:
        item = self._list.currentItem()
        if not item:
            return None
        lib_id = item.data(_LIBRARY_ROLE)
        if not lib_id:
            return None
        for lib in self._store.load():
            if lib.id == lib_id:
                return lib
        return None

    def _update_button_state(self):
        has_selection = self._selected_library() is not None
        self._open_btn.setEnabled(has_selection)
        self._remove_btn.setEnabled(has_selection)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "新增文件庫資料夾",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        _lib, added = self._store.add(folder)
        if not added:
            QMessageBox.information(self, "文件庫已存在", "這個資料夾已在文件庫中。")
        self.changed = True
        self._refresh()

    def _detect_cloud_folders(self):
        paths = discover_cloud_library_paths()
        added_count = 0
        for path in paths:
            _lib, added = self._store.add(path)
            if added:
                added_count += 1

        if added_count:
            QMessageBox.information(
                self, "已加入雲端資料夾", f"已加入 {added_count} 個資料夾。"
            )
            self.changed = True
            self._refresh()
        else:
            QMessageBox.information(
                self,
                "未偵測到新來源",
                "沒有找到新的 Google Drive、OneDrive 或 Dropbox 同步資料夾。",
            )

    def _open_selected(self):
        lib = self._selected_library()
        if lib:
            QDesktopServices.openUrl(QUrl.fromLocalFile(lib.path))

    def _remove_selected(self):
        lib = self._selected_library()
        if not lib:
            return
        answer = QMessageBox.question(
            self,
            "移除文件庫",
            f"要從清單移除「{lib.name}」嗎？\n\n檔案本身不會被刪除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(lib.id)
        self.changed = True
        self._refresh()

    def _stylesheet(self, theme: Theme) -> str:
        return f"""
QDialog {{
    background: {theme.surface};
    color: {theme.text};
}}
QLabel {{
    background: transparent;
    color: {theme.text};
}}
QLabel[muted="true"] {{
    color: {theme.text_muted};
}}
QPushButton {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 34px;
    padding: 0 12px;
}}
QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.accent};
}}
QPushButton:pressed {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
QPushButton:disabled {{
    background: {theme.surface_alt};
    border-color: {theme.border};
    color: {theme.text_subtle};
}}
"""
