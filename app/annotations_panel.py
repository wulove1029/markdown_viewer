"""Left-panel tab listing and editing annotations for the current document."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import LIGHT, Theme, collection_stylesheet


class _NoteEdit(QPlainTextEdit):
    """QPlainTextEdit that emits editingFinished when it loses focus."""

    editingFinished = pyqtSignal()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.editingFinished.emit()


class AnnotationsPanel(QWidget):
    def __init__(self, callbacks: dict, parent=None):
        super().__init__(parent)
        # callbacks: note_changed(id,text), color_changed(id,hex),
        # tags_changed(id,list), deleted(id), doc_tags_changed(list),
        # activated(id)
        self._cb = callbacks
        self._theme = LIGHT
        self._doc = None
        self._selected_id = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel("文件標籤"))
        self._doc_tags = QLineEdit()
        self._doc_tags.setPlaceholderText("以逗號分隔，如：PD協定, 待讀")
        self._doc_tags.editingFinished.connect(self._emit_doc_tags)
        layout.addWidget(self._doc_tags)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("篩選標籤…")
        self._filter.textChanged.connect(self._refresh_list)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self._list, stretch=1)

        layout.addWidget(QLabel("備註"))
        self._note = _NoteEdit()
        self._note.setFixedHeight(80)
        self._note.editingFinished.connect(self._emit_note)
        layout.addWidget(self._note)

        self._tags = QLineEdit()
        self._tags.setPlaceholderText("此標註的標籤，逗號分隔")
        self._tags.editingFinished.connect(self._emit_tags)
        layout.addWidget(self._tags)

        row = QHBoxLayout()
        self._color_btn = QPushButton("顏色…")
        self._color_btn.clicked.connect(self._pick_color)
        self._delete_btn = QPushButton("刪除")
        self._delete_btn.clicked.connect(self._delete_selected)
        row.addWidget(self._color_btn)
        row.addWidget(self._delete_btn)
        layout.addLayout(row)

        self.apply_theme(LIGHT)
        self._set_editor_enabled(False)

    # ---- external API ----
    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def set_document(self, doc):
        self._doc = doc
        self._doc_tags.setText(", ".join(doc.doc_tags) if doc else "")
        self._selected_id = None
        self._set_editor_enabled(False)
        self._refresh_list()

    def select(self, ann_id: str):
        self._selected_id = ann_id
        self._load_editor(ann_id)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == ann_id:
                self._list.setCurrentItem(item)
                break

    # ---- internal ----
    def _refresh_list(self):
        self._list.clear()
        if not self._doc:
            return
        flt = self._filter.text().strip()
        for a in self._doc.annotations:
            if flt and flt not in a.tags and flt not in (a.note or ""):
                continue
            label = (a.exact[:40] + "…") if len(a.exact) > 40 else a.exact
            if a.tags:
                label += "  #" + " #".join(a.tags)
            item = QListWidgetItem("● " + label)
            item.setForeground(QColor(a.color))
            item.setData(Qt.ItemDataRole.UserRole, a.id)
            self._list.addItem(item)

    def _find(self, ann_id):
        if not self._doc:
            return None
        for a in self._doc.annotations:
            if a.id == ann_id:
                return a
        return None

    def _on_item_clicked(self, item):
        self._selected_id = item.data(Qt.ItemDataRole.UserRole)
        self._load_editor(self._selected_id)

    def _on_item_activated(self, item):
        ann_id = item.data(Qt.ItemDataRole.UserRole)
        self._cb["activated"](ann_id)

    def _load_editor(self, ann_id):
        a = self._find(ann_id)
        if not a:
            self._set_editor_enabled(False)
            return
        self._set_editor_enabled(True)
        self._note.setPlainText(a.note)
        self._tags.setText(", ".join(a.tags))

    def _set_editor_enabled(self, on):
        for w in (self._note, self._tags, self._color_btn, self._delete_btn):
            w.setEnabled(on)

    def _emit_note(self):
        if self._selected_id:
            self._cb["note_changed"](self._selected_id, self._note.toPlainText())

    def _emit_tags(self):
        if self._selected_id:
            tags = [t.strip() for t in self._tags.text().split(",") if t.strip()]
            self._cb["tags_changed"](self._selected_id, tags)

    def _emit_doc_tags(self):
        tags = [t.strip() for t in self._doc_tags.text().split(",") if t.strip()]
        self._cb["doc_tags_changed"](tags)

    def _pick_color(self):
        a = self._find(self._selected_id)
        if not a:
            return
        color = QColorDialog.getColor(QColor(a.color), self, "選擇高亮顏色")
        if color.isValid():
            self._cb["color_changed"](self._selected_id, color.name())

    def _delete_selected(self):
        if self._selected_id:
            self._cb["deleted"](self._selected_id)
