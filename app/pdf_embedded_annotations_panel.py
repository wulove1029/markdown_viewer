"""Sidebar panel listing Adobe-style annotations embedded inside a PDF.

Distinct from ``pdf_highlights_panel.py`` / ``pdf_notes_panel.py``, which list
the *app's own* text highlights and page notes (kept in sidecar JSON files).
This panel is read-only: it shows what Acrobat (or any other PDF editor)
already wrote into the PDF itself, with no add/edit/delete affordances, since
the app never modifies the embedded annotations.

Entries are shown as Acrobat shows them — as discussion threads. A reply
(``/IRT``) is indented under the annotation it answers instead of sitting in
the list as a peer, and a text-markup annotation with no note of its own is
labelled with its type and the passage it marks.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from .pdf_embedded_annotations import (
    KIND_LABELS,
    build_annotation_threads,
    kind_label,
    summary_text,
    tooltip_text,
)
from .theme import LIGHT, Theme, collection_stylesheet

# Kept as a module-level name because tests and other panels refer to it.
_KIND_LABELS = KIND_LABELS

_REPLY_INDENT = "　　↳ "
_MAX_SNIPPET = 60


def _swatch_icon(color: str, border: str, size: int = 12) -> QIcon:
    """A small filled square (with a theme-coloured border) for a list item."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(border))
    painter.drawRoundedRect(0, 0, size - 1, size - 1, 2, 2)
    painter.end()
    return QIcon(pixmap)


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > _MAX_SNIPPET:
        return text[: _MAX_SNIPPET - 1] + "…"
    return text


def parent_label(entry) -> str:
    """List text for a top-level annotation.

    Acrobat's own comment list shows the marked passage for a highlight and the
    typed note underneath it, so both are surfaced here: the marked text
    (quoted) first, then the note text when the annotation has one.
    """
    parts = [f"p.{entry.page + 1}", f"[{kind_label(entry)}]"]
    marked = _clip(entry.marked_text)
    note = _clip(entry.note_text)
    if marked:
        parts.append(f"「{marked}」")
    if note:
        parts.append(note)
    if not marked and not note:
        parts.append(_clip(entry.content) or "（無文字內容）")
    label = "　".join(parts)
    if entry.author:
        label += f"　— {entry.author}"
    return label


def reply_label(entry) -> str:
    """Indented list text for a reply, so a thread reads as a thread."""
    label = _REPLY_INDENT + (_clip(summary_text(entry)) or "（無文字內容）")
    if entry.author:
        label += f"　— {entry.author}"
    return label


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
        if self._annotations:
            self.set_annotations(self._annotations)  # re-tint swatch borders

    def set_annotations(self, annotations) -> None:
        self._annotations = list(annotations or [])
        self._list.clear()
        if not self._annotations:
            item = QListWidgetItem("此 PDF 沒有內嵌註解")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return
        for parent, replies in build_annotation_threads(self._annotations):
            self._add_item(parent, parent_label(parent), replies)
            for reply in replies:
                self._add_item(reply, reply_label(reply), ())

    def _add_item(self, entry, label: str, replies) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        item.setToolTip(tooltip_text(entry, replies))
        # The annotation colour is shown as a small swatch, never as the
        # text colour: a yellow highlight's label must stay readable on
        # both the light and dark theme surfaces.
        if entry.color:
            item.setIcon(_swatch_icon(entry.color, self._theme.border))
        self._list.addItem(item)

    def count(self) -> int:
        """How many embedded annotations this panel is showing (replies too)."""
        return len(self._annotations)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.ItemDataRole.UserRole)
        if entry is not None:
            self._callbacks.get("activated", lambda _e: None)(entry)
