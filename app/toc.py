"""Right-side Table of Contents panel."""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QListWidgetItem
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor


class TocView(QWidget):
    def __init__(self, on_anchor_clicked, parent=None):
        super().__init__(parent)
        self._on_anchor_clicked = on_anchor_clicked
        self._anchors: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 目錄清單
        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: #f5f5f2;
                border: none;
                font-size: 13px;
            }
            QListWidget::item {
                padding: 4px 8px;
                color: #333;
                border-bottom: 1px solid #ebebea;
            }
            QListWidget::item:hover {
                background: #e8e6fa;
                color: #5a4faf;
            }
            QListWidget::item:selected {
                background: #dddaf7;
                color: #3d349e;
            }
        """)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self.setStyleSheet("background: #f5f5f2;")

    def update_headings(self, headings: list[tuple[int, str, str]]):
        """headings = list of (level, text, anchor_id)"""
        self._list.clear()
        self._anchors = []

        for level, text, anchor in headings:
            item = QListWidgetItem()
            # 縮排：h1=0, h2=12px, h3=24px ...
            indent = (level - 1) * 12
            item.setText(text)
            item.setData(Qt.ItemDataRole.UserRole, anchor)

            font = QFont()
            if level == 1:
                font.setBold(True)
                font.setPointSize(12)
            elif level == 2:
                font.setPointSize(11)
            else:
                font.setPointSize(10)
                item.setForeground(QColor("#666"))

            item.setFont(font)
            # 用空白模擬縮排
            item.setText("  " * (level - 1) + text)
            self._list.addItem(item)
            self._anchors.append(anchor)

    def set_active_anchor(self, anchor: str):
        """由 scroll spy 呼叫，更新選取但不觸發跳轉。"""
        if not anchor:
            self._list.clearSelection()
            return
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == anchor:
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                return

    def _on_item_clicked(self, item: QListWidgetItem):
        anchor = item.data(Qt.ItemDataRole.UserRole)
        if anchor:
            self._on_anchor_clicked(anchor)
