"""Left panel — switches between file browser, recent files, and TOC."""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel,
                              QStackedWidget, QPushButton, QHBoxLayout,
                              QFileDialog)
from PyQt6.QtCore import Qt

from .file_browser import FileBrowserView
from .recent_files import RecentFilesView
from .toc import TocView

_PANEL_W = 240

_HEADER_STYLE = """
QWidget#panelHeader {
    background: #eeecea;
    border-bottom: 1px solid #d5d5d0;
}
QLabel {
    font-size: 11px;
    font-weight: 700;
    color: #555;
    letter-spacing: 0.5px;
    padding-left: 8px;
}
QPushButton {
    background: transparent;
    border: none;
    color: #888;
    font-size: 14px;
    padding: 2px 6px;
    border-radius: 4px;
}
QPushButton:hover { background: #d5d3f0; color: #5a4faf; }
"""

_PANEL_STYLE = """
QWidget#leftPanel {
    background: #f5f5f2;
    border-right: 1px solid #d5d5d0;
}
"""


class LeftPanel(QWidget):
    TITLES = ["檔案瀏覽", "最近開啟", "目錄"]

    def __init__(self, on_file_selected, on_anchor_clicked, parent=None):
        super().__init__(parent)
        self.setObjectName("leftPanel")
        self.setMinimumWidth(120)
        self.setStyleSheet(_PANEL_STYLE)
        self._on_file_selected = on_file_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── header bar ────────────────────────────────────────────
        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        self._header.setFixedHeight(32)
        self._header.setStyleSheet(_HEADER_STYLE)
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(0, 0, 4, 0)
        self._title_label = QLabel("檔案瀏覽")

        # 開啟檔案按鈕
        self._open_btn = QPushButton("📂")
        self._open_btn.setFixedSize(22, 22)
        self._open_btn.setToolTip("開啟 Markdown 檔案 (Ctrl+O)")
        self._open_btn.clicked.connect(self.open_file_dialog)

        # 清除最近開啟（僅在最近開啟頁顯示）
        self._clear_btn = QPushButton("🗑")
        self._clear_btn.setFixedSize(22, 22)
        self._clear_btn.setToolTip("清除全部紀錄")
        self._clear_btn.hide()

        self._close_btn = QPushButton("✕")
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.setToolTip("收合面板")
        hl.addWidget(self._title_label)
        hl.addStretch()
        hl.addWidget(self._clear_btn)
        hl.addWidget(self._open_btn)
        hl.addWidget(self._close_btn)
        layout.addWidget(self._header)

        # ── stacked pages ─────────────────────────────────────────
        self._stack = QStackedWidget()

        self._file_browser = FileBrowserView(on_file_selected=on_file_selected)
        self._recent = RecentFilesView(on_file_selected=on_file_selected)
        self._toc = TocView(on_anchor_clicked=on_anchor_clicked)

        self._stack.addWidget(self._file_browser)   # index 0
        self._stack.addWidget(self._recent)          # index 1
        self._stack.addWidget(self._toc)             # index 2

        layout.addWidget(self._stack)

        self._clear_btn.clicked.connect(self._recent.clear_all)

    # ── public API ────────────────────────────────────────────────

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

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "開啟 Markdown 檔案", "",
            "Markdown 檔案 (*.md *.markdown);;所有檔案 (*)"
        )
        if path:
            self._on_file_selected(path)

    def switch_to(self, index: int):
        self._stack.setCurrentIndex(index)
        self._title_label.setText(self.TITLES[index])
        # 清除按鈕只在「最近開啟」頁顯示
        self._clear_btn.setVisible(index == 1)
