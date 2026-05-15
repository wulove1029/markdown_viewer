"""Left icon ribbon — Obsidian-style."""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QSpacerItem, QSizePolicy
from PyQt6.QtCore import Qt

_RIBBON_STYLE = """
QWidget#ribbon {
    background: #e8e8e4;
    border-right: 1px solid #d5d5d0;
}
QPushButton {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: #666;
    font-family: "Segoe UI Symbol", "Segoe UI", sans-serif;
    font-size: 16px;
    font-weight: normal;
    padding: 0px;
    min-width: 36px;
    max-width: 36px;
    min-height: 36px;
    max-height: 36px;
}
QPushButton:hover {
    background: #d5d3f0;
    color: #5a4faf;
}
QPushButton[active="true"] {
    background: #c8c3f0;
    color: #3d349e;
}
"""

# 收合時：只剩這一顆浮在渲染區左上角
_FLOAT_BTN_STYLE = """
QPushButton {
    background: #e8e8e4;
    border: 1px solid #d5d5d0;
    border-radius: 4px;
    color: #666;
    font-size: 16px;
    padding: 2px;
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
}
QPushButton:hover { background: #d5d3f0; color: #5a4faf; }
"""


class RibbonButton(QPushButton):
    def __init__(self, icon: str, tooltip: str, parent=None):
        super().__init__(icon, parent)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", False)
        self.setCheckable(True)

    def set_active(self, active: bool):
        self.setChecked(active)
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)


class Ribbon(QWidget):
    def __init__(self, on_tab_changed, on_toggle_sidebar, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbon")
        self.setFixedWidth(44)
        self.setStyleSheet(_RIBBON_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._buttons: list[RibbonButton] = []
        self._on_tab_changed = on_tab_changed

        # ── 收合按鈕（最上方）────────────────────────────────────
        self._toggle_btn = QPushButton("◫")
        self._toggle_btn.setToolTip("收合/展開側邊欄")
        self._toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_btn.setFixedSize(36, 36)
        self._toggle_btn.clicked.connect(on_toggle_sidebar)
        layout.addWidget(self._toggle_btn)

        # ── 間隔線 ───────────────────────────────────────────────
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #d5d5d0;")
        layout.addWidget(sep)

        # ── 功能按鈕 ─────────────────────────────────────────────
        self._add_btn("⊞", "檔案瀏覽", 0)
        self._add_btn("⊙", "最近開啟", 1)
        self._add_btn("☰", "目錄", 2)

        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        )

        self._set_active(0)

    def _add_btn(self, icon: str, tooltip: str, index: int):
        btn = RibbonButton(icon, tooltip)
        btn.clicked.connect(lambda checked, i=index: self._on_clicked(i))
        self.layout().addWidget(btn)
        self._buttons.append(btn)

    def _on_clicked(self, index: int):
        self._set_active(index)
        self._on_tab_changed(index)

    def _set_active(self, index: int):
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == index)
