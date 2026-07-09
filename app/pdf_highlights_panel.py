"""Sidebar panels for PDF markup: text highlights and page notes.

``PdfHighlightsPanel`` lists the text highlights created in the PDF canvas (each
created by selecting text, not from the panel), with a color swatch, jump,
recolor, note, and delete actions. ``PdfMarkupPanel`` wraps it together with the
existing page-notes panel under one "標註" tab so PDFs keep both affordances.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .pdf_notes_panel import PdfNotesPanel
from .theme import LIGHT, Theme, collection_stylesheet

# Shared highlighter palette (kept in sync with pdf_view.PALETTE).
PALETTE: list[tuple[str, str]] = [
    ("#ffd54f", "黃"),
    ("#a5d6a7", "綠"),
    ("#90caf9", "藍"),
    ("#f48fb1", "粉"),
    ("#ce93d8", "紫"),
]


class PdfHighlightsPanel(QWidget):
    def __init__(self, callbacks: dict, parent=None):
        super().__init__(parent)
        # callbacks: activated(id), recolor(id, hex), note(id), deleted(id)
        self._callbacks = callbacks
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._hint = QLabel("在 PDF 中用滑鼠選取文字，右鍵即可加上螢光標記。")
        self._hint.setWordWrap(True)
        self._hint.setProperty("muted", True)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_clicked)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_menu)

        layout.addWidget(self._hint)
        layout.addWidget(self._list)
        self.apply_theme(LIGHT)
        self.set_highlights([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._hint.setStyleSheet(f"color: {theme.text_muted}; background: transparent;")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_highlights(self, highlights):
        self._list.clear()
        if not highlights:
            item = QListWidgetItem("此 PDF 尚無螢光標記")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return
        for hl in highlights:
            snippet = (hl.text or "").strip().replace("\n", " ").replace("\r", " ")
            if len(snippet) > 50:
                snippet = snippet[:49] + "…"
            label = f"p.{hl.page + 1}　{snippet or '（無文字）'}"
            if hl.note:
                label += "　📝"
            if hl.tags:
                label += "　" + " ".join("#" + t for t in hl.tags)
            item = QListWidgetItem("● " + label)
            item.setData(Qt.ItemDataRole.UserRole, hl.id)
            item.setForeground(QColor(hl.color))
            tip = hl.text or ""
            if hl.note:
                tip = f"{tip}\n📝 {hl.note}" if tip else hl.note
            item.setToolTip(tip)
            self._list.addItem(item)

    def _on_clicked(self, item: QListWidgetItem):
        hid = item.data(Qt.ItemDataRole.UserRole)
        if hid:
            self._callbacks.get("activated", lambda _i: None)(hid)

    def _on_menu(self, pos: QPoint):
        item = self._list.itemAt(pos)
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        hid = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {self._theme.surface};"
            f" border: 1px solid {self._theme.border}; color: {self._theme.text}; }}"
            f"QMenu::item:selected {{ background: {self._theme.surface_hover}; }}"
        )
        jump = QAction("跳到此標記", self)
        jump.triggered.connect(lambda: self._callbacks.get("activated", lambda _i: None)(hid))
        menu.addAction(jump)

        color_menu = menu.addMenu("變更顏色")
        for hex_color, lbl in PALETTE:
            act = QAction(lbl, self)
            act.triggered.connect(
                lambda _checked=False, c=hex_color: self._callbacks.get(
                    "recolor", lambda _i, _c: None
                )(hid, c)
            )
            color_menu.addAction(act)
        custom = QAction("自訂…", self)
        custom.triggered.connect(lambda: self._pick_custom(hid))
        color_menu.addAction(custom)

        note = QAction("編輯備註", self)
        note.triggered.connect(lambda: self._callbacks.get("note", lambda _i: None)(hid))
        menu.addAction(note)
        menu.addSeparator()
        delete = QAction("刪除標記", self)
        delete.triggered.connect(lambda: self._callbacks.get("deleted", lambda _i: None)(hid))
        menu.addAction(delete)
        menu.exec(self._list.mapToGlobal(pos))

    def _pick_custom(self, hid):
        color = QColorDialog.getColor(QColor("#ffd54f"), self, "選擇螢光顏色")
        if color.isValid():
            self._callbacks.get("recolor", lambda _i, _c: None)(hid, color.name())


class PdfMarkupPanel(QWidget):
    """Hosts the PDF highlight list and page-note list under one tab."""

    def __init__(self, note_callbacks: dict, highlight_callbacks: dict, parent=None):
        super().__init__(parent)
        self._theme = LIGHT
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._highlights = PdfHighlightsPanel(highlight_callbacks or {})
        self._notes = PdfNotesPanel(note_callbacks or {})
        self._tabs.addTab(self._highlights, "螢光")
        self._tabs.addTab(self._notes, "頁註")
        layout.addWidget(self._tabs)
        self.apply_theme(LIGHT)

    @property
    def highlights(self) -> PdfHighlightsPanel:
        return self._highlights

    @property
    def notes(self) -> PdfNotesPanel:
        return self._notes

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: none; background: {theme.surface}; }}"
            f"QTabBar {{ background: {theme.surface}; border: none; }}"
            f"QTabBar::tab {{ background: transparent; border: none;"
            f" border-bottom: 2px solid transparent; color: {theme.text_muted};"
            f" min-height: 30px; padding: 0 14px; }}"
            f"QTabBar::tab:selected {{ background: {theme.accent_soft};"
            f" border-bottom: 2px solid {theme.accent}; color: {theme.text}; }}"
            f"QTabBar::tab:hover {{ background: {theme.surface_hover}; color: {theme.text}; }}"
        )
        self._highlights.apply_theme(theme)
        self._notes.apply_theme(theme)
