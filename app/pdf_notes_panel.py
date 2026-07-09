"""Sidebar panel listing page-anchored PDF notes."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import LIGHT, Theme, collection_stylesheet


class PdfNotesPanel(QWidget):
    def __init__(self, callbacks: dict, parent=None):
        super().__init__(parent)
        # callbacks: add(), activated(id), edit(id), deleted(id)
        self._callbacks = callbacks
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._add_btn = QPushButton("＋ 在此頁新增註記")
        self._add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_btn.clicked.connect(lambda: self._callbacks.get("add", lambda: None)())

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_menu)

        layout.addWidget(self._add_btn)
        layout.addWidget(self._list)
        self.apply_theme(LIGHT)
        self.set_notes([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))
        self._add_btn.setStyleSheet(
            f"QPushButton {{ background: {theme.surface}; border: 1px solid {theme.border};"
            f" border-radius: 6px; color: {theme.text}; padding: 6px 10px; }}"
            f"QPushButton:hover {{ background: {theme.surface_hover};"
            f" border-color: {theme.accent}; }}"
        )

    def set_current_page(self, page0: int):
        self._add_btn.setText(f"＋ 在第 {page0 + 1} 頁新增註記")

    def set_notes(self, notes):
        self._list.clear()
        if not notes:
            item = QListWidgetItem("此 PDF 尚無頁面註記")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return
        for note in notes:
            snippet = (note.note or "").strip().replace("\n", " ")
            if len(snippet) > 60:
                snippet = snippet[:59] + "…"
            label = f"p.{note.page + 1}　{snippet or '（空白註記）'}"
            if note.tags:
                label += "　" + " ".join("#" + t for t in note.tags)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, note.id)
            item.setToolTip(note.note or "")
            if note.color:
                item.setForeground(QColor(self._theme.text))
            self._list.addItem(item)

    def _on_clicked(self, item: QListWidgetItem):
        note_id = item.data(Qt.ItemDataRole.UserRole)
        if note_id:
            self._callbacks.get("activated", lambda _i: None)(note_id)

    def _on_menu(self, pos: QPoint):
        item = self._list.itemAt(pos)
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        note_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {self._theme.surface}; border: 1px solid {self._theme.border};"
            f" color: {self._theme.text}; }}"
            f"QMenu::item:selected {{ background: {self._theme.surface_hover}; }}"
        )
        jump = QAction("跳到此頁", self)
        jump.triggered.connect(lambda: self._callbacks.get("activated", lambda _i: None)(note_id))
        edit = QAction("編輯註記", self)
        edit.triggered.connect(lambda: self._callbacks.get("edit", lambda _i: None)(note_id))
        delete = QAction("刪除註記", self)
        delete.triggered.connect(lambda: self._callbacks.get("deleted", lambda _i: None)(note_id))
        menu.addAction(jump)
        menu.addAction(edit)
        menu.addSeparator()
        menu.addAction(delete)
        menu.exec(self._list.mapToGlobal(pos))
