"""Tabbed left workspace panel."""

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .file_browser import FileBrowserView
from .recent_files import RecentFilesView
from .theme import LIGHT, Theme, panel_stylesheet, svg_icon
from .toc import TocView


class LeftPanel(QWidget):
    def __init__(self, on_file_selected, on_anchor_clicked, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self.setObjectName("leftPanel")
        self.setMinimumWidth(180)
        self.setMaximumWidth(420)
        self._on_file_selected = on_file_selected
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(4)

        self._title_label = QLabel("工作面板")
        self._title_label.setObjectName("panelTitle")

        self._open_btn = QPushButton()
        self._open_btn.setToolTip("開啟 Markdown 檔案 (Ctrl+O)")
        self._open_btn.setAccessibleName("開啟 Markdown 檔案")
        self._open_btn.setIconSize(QSize(20, 20))
        self._open_btn.clicked.connect(self.open_file_dialog)

        self._close_btn = QPushButton()
        self._close_btn.setToolTip("收合側邊欄")
        self._close_btn.setAccessibleName("收合側邊欄")
        self._close_btn.setIconSize(QSize(20, 20))

        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._open_btn)
        header_layout.addWidget(self._close_btn)
        layout.addWidget(self._header)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._file_browser = FileBrowserView(on_file_selected=on_file_selected)
        self._recent = RecentFilesView(on_file_selected=on_file_selected)
        self._toc = TocView(on_anchor_clicked=on_anchor_clicked)

        self._tabs.addTab(self._file_browser, "檔案")
        self._tabs.addTab(self._recent, "最近")
        self._tabs.addTab(self._toc, "目錄")

        layout.addWidget(self._tabs)
        self.apply_theme(theme)

    @property
    def toc(self) -> TocView:
        return self._toc

    @property
    def file_browser(self) -> FileBrowserView:
        return self._file_browser

    @property
    def recent(self) -> RecentFilesView:
        return self._recent

    @property
    def close_btn(self) -> QPushButton:
        return self._close_btn

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(panel_stylesheet(theme))
        self._open_btn.setIcon(svg_icon("folder-open", theme.text_muted, 20))
        self._close_btn.setIcon(svg_icon("chevron-left", theme.text_muted, 20))
        self._file_browser.apply_theme(theme)
        self._recent.apply_theme(theme)
        self._toc.apply_theme(theme)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "開啟 Markdown 檔案",
            "",
            "Markdown 檔案 (*.md *.markdown);;所有檔案 (*)",
        )
        if path:
            self._on_file_selected(path)

    def switch_to(self, index: int):
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)
