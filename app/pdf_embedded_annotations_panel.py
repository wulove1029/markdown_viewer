"""Sidebar panel listing Adobe-style annotations embedded inside a PDF.

Distinct from ``pdf_highlights_panel.py`` / ``pdf_notes_panel.py``, which list
the *app's own* text highlights and page notes (kept in sidecar JSON files).
This panel is read-only: it shows what Acrobat (or any other PDF editor)
already wrote into the PDF itself, with no add/edit/delete affordances, since
the app never modifies the embedded annotations.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .theme import LIGHT, Theme, collection_stylesheet

# Traditional-Chinese labels for the annotation kinds pymupdf reports.
_KIND_LABELS = {
    "Text": "便利貼",
    "Highlight": "螢光標記",
    "FreeText": "文字方塊",
    "Underline": "底線",
    "StrikeOut": "刪除線",
    "Squiggly": "波浪底線",
}


class PdfEmbeddedAnnotationsPanel(QWidget):
    """Lists embedded PDF annotations; clicking one jumps to its page/rect."""

    def __init__(self, callbacks: dict | None = None, parent=None):
        super().__init__(parent)
        # callbacks: activated(EmbeddedAnnotation)
        self._callbacks = callbacks or {}
        self._theme = LIGHT
        self._annotations: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_clicked)
        layout.addWidget(self._list, stretch=1)

        self.apply_theme(LIGHT)
        self.set_annotations([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_annotations(self, annotations) -> None:
        self._annotations = list(annotations or [])
        self._list.clear()
        if not self._annotations:
            item = QListWidgetItem("此 PDF 沒有內嵌註解")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return
        for entry in self._annotations:
            snippet = (entry.content or "").strip().replace("\n", " ").replace("\r", " ")
            if len(snippet) > 60:
                snippet = snippet[:59] + "…"
            kind_label = _KIND_LABELS.get(entry.kind, entry.kind)
            label = f"p.{entry.page + 1}　[{kind_label}]　{snippet or '（無文字內容）'}"
            if entry.author:
                label += f"　— {entry.author}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            tip_lines = [f"第 {entry.page + 1} 頁　{kind_label}"]
            if entry.author:
                tip_lines.append(f"作者：{entry.author}")
            if entry.modified:
                tip_lines.append(f"修改時間：{entry.modified}")
            if entry.content:
                tip_lines.append(entry.content)
            item.setToolTip("\n".join(tip_lines))
            if entry.color:
                item.setForeground(QColor(entry.color))
            self._list.addItem(item)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self._callbacks.get("activated", lambda _e: None)(entry)
