"""Right panel: TOC (top) + File browser (bottom), each collapsible."""

from pathlib import Path
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QPushButton,
                              QSplitter, QSizePolicy)
from PyQt6.QtCore import Qt

from .toc import TocView
from .sidebar import SidebarView

_BTN_STYLE = """
QPushButton {
    background: #e0defa;
    color: #5a4faf;
    border: none;
    border-radius: 0px;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 0;
    text-align: center;
}
QPushButton:hover  { background: #c8c3f0; }
QPushButton:pressed{ background: #b0a8e8; }
"""


class SectionToggle(QWidget):
    """A collapsible section: [▲ Label] / [▼ Label] button + content widget."""

    def __init__(self, label: str, content: QWidget,
                 start_open: bool = True, parent=None):
        super().__init__(parent)
        self._label = label
        self._open = start_open
        self._content = content

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._btn = QPushButton(self._header_text())
        self._btn.setFixedHeight(28)
        self._btn.setStyleSheet(_BTN_STYLE)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.clicked.connect(self._toggle)
        layout.addWidget(self._btn)
        layout.addWidget(content)

        if not start_open:
            content.hide()

    def _header_text(self) -> str:
        arrow = "▲" if self._open else "▼"
        return f"{arrow}  {self._label}"

    def _toggle(self):
        self._open = not self._open
        if self._open:
            self._content.show()
        else:
            self._content.hide()
        self._btn.setText(self._header_text())


class RightPanel(QWidget):
    def __init__(self, on_file_selected, on_anchor_clicked, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setMinimumWidth(0)

        # ── TOC ───────────────────────────────────────────────────
        self._toc = TocView(on_anchor_clicked=on_anchor_clicked)
        toc_section = SectionToggle("目錄", self._toc, start_open=True)

        # ── Sidebar (recent + file tree) ──────────────────────────
        self._sidebar = SidebarView(on_file_selected=on_file_selected)
        file_section = SectionToggle("檔案瀏覽", self._sidebar, start_open=True)

        # ── Splitter to let user resize between sections ──────────
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(toc_section)
        splitter.addWidget(file_section)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 300])
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #ddddd8; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter)

        self.setStyleSheet("background: #f5f5f2; border-right: 1px solid #ddddd8;")

    # ── public API (called by MainWindow) ─────────────────────────

    @property
    def toc(self) -> TocView:
        return self._toc

    @property
    def sidebar(self) -> SidebarView:
        return self._sidebar
