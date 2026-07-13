"""Sidebar panels for PDF markup: text highlights and page notes.

``PdfHighlightsPanel`` lists the text highlights created in the PDF canvas (each
created by selecting text, not from the panel), with a color swatch, jump,
recolor, note, and delete actions. ``PdfMarkupPanel`` wraps it together with the
existing page-notes panel under one "標註" tab so PDFs keep both affordances.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import doc_tags as doc_tags_facade
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
        self._selected_id: str | None = None

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
        layout.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        self._delete_btn = QPushButton("刪除")
        self._delete_btn.clicked.connect(self._delete_selected)
        row.addStretch(1)
        row.addWidget(self._delete_btn)
        layout.addLayout(row)

        self.apply_theme(LIGHT)
        self.set_highlights([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._hint.setStyleSheet(f"color: {theme.text_muted}; background: transparent;")
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_highlights(self, highlights):
        previous_id = self._selected_id
        self._list.clear()
        if not highlights:
            self._selected_id = None
            item = QListWidgetItem("此 PDF 尚無螢光標記")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            self._set_delete_enabled(False)
            return
        self._selected_id = None
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
            if hl.id == previous_id:
                self._selected_id = hl.id
                self._list.setCurrentItem(item)
        self._set_delete_enabled(self._selected_id is not None)

    def _on_clicked(self, item: QListWidgetItem):
        hid = item.data(Qt.ItemDataRole.UserRole)
        if hid:
            self._selected_id = hid
            self._set_delete_enabled(True)
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

    def _set_delete_enabled(self, on: bool):
        self._delete_btn.setEnabled(bool(on))

    def _delete_selected(self):
        if self._selected_id:
            self._callbacks.get("deleted", lambda _i: None)(self._selected_id)


class PdfMarkupPanel(QWidget):
    """Hosts the PDF highlight list and page-note list under one tab.

    Also exposes a "文件標籤" (document-level tags) field at the top, mirroring
    the Markdown ``AnnotationsPanel``. Edits are persisted through
    ``app.doc_tags`` (type-neutral facade) for the currently-open PDF and then
    reported via the ``on_doc_tags_changed`` callback so the tag index / side
    panels can refresh.
    """

    def __init__(
        self,
        note_callbacks: dict,
        highlight_callbacks: dict,
        on_doc_tags_changed=None,
        parent=None,
    ):
        super().__init__(parent)
        self._theme = LIGHT
        # Callback invoked with [current_pdf_path] after doc tags are written.
        self._on_doc_tags_changed = on_doc_tags_changed
        # Absolute path of the PDF whose document tags are currently shown.
        self._current_pdf_path: Path | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Document-level tags (mirrors the Markdown AnnotationsPanel) ---
        self._doc_tags_box = QWidget()
        box_layout = QVBoxLayout(self._doc_tags_box)
        box_layout.setContentsMargins(8, 8, 8, 6)
        box_layout.setSpacing(4)
        self._doc_tags_label = QLabel("文件標籤")
        self._doc_tags = QLineEdit()
        self._doc_tags.setPlaceholderText("以逗號分隔，如：PD協定, 待讀")
        self._doc_tags.editingFinished.connect(self._emit_doc_tags)
        box_layout.addWidget(self._doc_tags_label)
        box_layout.addWidget(self._doc_tags)
        layout.addWidget(self._doc_tags_box)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._highlights = PdfHighlightsPanel(highlight_callbacks or {})
        self._notes = PdfNotesPanel(note_callbacks or {})
        self._tabs.addTab(self._highlights, "螢光")
        self._tabs.addTab(self._notes, "頁註")
        layout.addWidget(self._tabs)
        self._set_doc_tags_enabled(False)
        self.apply_theme(LIGHT)

    def set_pdf_document(self, path: Path | str | None) -> None:
        """Point the document-tag field at ``path`` and load its current tags.

        Pass ``None`` (e.g. when a non-PDF is opened) to clear and disable the
        field. Reading uses the type-neutral ``app.doc_tags`` facade, so this is
        a no-op-safe call for any path.
        """
        if path is None:
            self._current_pdf_path = None
            self._doc_tags.blockSignals(True)
            self._doc_tags.clear()
            self._doc_tags.blockSignals(False)
            self._set_doc_tags_enabled(False)
            return
        self._current_pdf_path = Path(path)
        try:
            tags = doc_tags_facade.read_doc_tags(self._current_pdf_path)
        except Exception:
            tags = []
        self._doc_tags.blockSignals(True)
        self._doc_tags.setText(", ".join(tags))
        self._doc_tags.blockSignals(False)
        self._set_doc_tags_enabled(True)

    @property
    def current_pdf_path(self) -> Path | None:
        return self._current_pdf_path

    def _set_doc_tags_enabled(self, on: bool) -> None:
        self._doc_tags.setEnabled(bool(on))

    def _emit_doc_tags(self) -> None:
        path = self._current_pdf_path
        if path is None:
            return
        tags = [t.strip() for t in self._doc_tags.text().split(",") if t.strip()]
        try:
            doc_tags_facade.write_doc_tags(path, tags)
        except Exception:
            return
        # Reflect the normalized (deduped/stripped) result back into the field.
        try:
            normalized = doc_tags_facade.read_doc_tags(path)
        except Exception:
            normalized = tags
        self._doc_tags.blockSignals(True)
        self._doc_tags.setText(", ".join(normalized))
        self._doc_tags.blockSignals(False)
        if self._on_doc_tags_changed is not None:
            self._on_doc_tags_changed([path])

    @property
    def highlights(self) -> PdfHighlightsPanel:
        return self._highlights

    @property
    def notes(self) -> PdfNotesPanel:
        return self._notes

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._doc_tags_box.setStyleSheet(f"background: {theme.surface};")
        self._doc_tags_label.setStyleSheet(
            f"color: {theme.text_muted}; background: transparent;"
        )
        self._doc_tags.setStyleSheet(
            f"QLineEdit {{ background: {theme.surface_hover}; color: {theme.text};"
            f" border: 1px solid {theme.border}; border-radius: 4px; padding: 4px 6px; }}"
        )
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
