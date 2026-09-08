"""A small, non-modal card showing one embedded PDF annotation and its replies.

The on-page bubble marker says *that* a highlight carries a comment; this card
says *what* it says. It is a plain ``QFrame`` stacked on the ``PdfView``
viewport rather than a tooltip or a dialog, so the reader can look at the
marked text and the comment at the same time and dismiss it with Esc, a click
elsewhere, or by scrolling away.

Only one card exists per view (``PdfView`` owns it), and it is deliberately
read-only: the app never writes back to the PDF.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .pdf_embedded_annotations import kind_label, summary_text
from .theme import LIGHT, Theme

CARD_WIDTH = 300
CARD_MAX_HEIGHT = 320
_GAP = 8


def _pretty_date(raw: str) -> str:
    """Turn a PDF date (``D:20260908123941+08'00'``) into ``2026-09-08 12:39``."""
    text = str(raw or "").strip()
    if text.startswith("D:"):
        text = text[2:]
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 12:
        return str(raw or "").strip()
    return (
        f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]} {digits[8:10]}:{digits[10:12]}"
    )


def header_text(entry) -> str:
    """The card's first line: type, author, and when it was last edited."""
    parts = [kind_label(entry)]
    if entry.author:
        parts.append(entry.author)
    stamp = _pretty_date(entry.modified)
    if stamp:
        parts.append(stamp)
    return "　·　".join(parts)


def body_text(entry) -> str:
    """The comment itself, falling back to the passage the annotation marks."""
    note = (entry.note_text or "").strip()
    if note:
        return note
    marked = (entry.marked_text or "").strip()
    if marked:
        return f"「{marked}」"
    return (entry.content or "").strip() or "（無文字內容）"


class PdfAnnotationCard(QFrame):
    """Non-modal popup card for one annotation plus its reply thread."""

    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pdfAnnotationCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._theme: Theme = LIGHT
        self._entry = None
        self._replies: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 10, 12, 12)
        self._content_layout.setSpacing(6)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll)
        self.hide()

    # ---------------------------------------------------------------- content
    def entry(self):
        return self._entry

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"#pdfAnnotationCard {{ background: {theme.surface};"
            f" border: 1px solid {theme.border}; border-radius: 6px; }}"
            f"#pdfAnnotationCard QLabel {{ background: transparent;"
            f" color: {theme.text}; }}"
            f"#pdfAnnotationCard QScrollArea {{ background: transparent;"
            " border: none; }"
        )
        self._content.setStyleSheet("background: transparent;")
        if self._entry is not None:
            self._rebuild(self._entry, self._replies)

    def _clear(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _label(self, text: str, *, muted=False, indent=0, bold=False) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        style = f"color: {self._theme.text_muted};" if muted else ""
        if bold:
            style += "font-weight: 600;"
        if indent:
            style += f"padding-left: {indent}px;"
        if style:
            label.setStyleSheet(style)
        return label

    def _rebuild(self, entry, replies) -> None:
        self._clear()
        self._content_layout.addWidget(
            self._label(header_text(entry), muted=True, bold=True)
        )
        if entry.note_text and entry.marked_text:
            # Both exist: quote the marked passage, then the comment about it.
            self._content_layout.addWidget(
                self._label(f"「{entry.marked_text}」", muted=True)
            )
        self._content_layout.addWidget(self._label(body_text(entry)))
        for reply in replies or ():
            line = header_text(reply)
            self._content_layout.addWidget(
                self._label(f"↳ {line}", muted=True, indent=14)
            )
            self._content_layout.addWidget(
                self._label(
                    summary_text(reply) or "（無文字內容）", indent=14
                )
            )
        self._content_layout.addStretch(1)

    def _resize_for(self, entry, replies, bounds: QRect) -> tuple[int, int]:
        self._entry = entry
        self._replies = list(replies or ())
        self._rebuild(entry, self._replies)
        width = min(CARD_WIDTH, max(180, bounds.width() - 2 * _GAP))
        self.setFixedWidth(width)
        height = min(
            CARD_MAX_HEIGHT, max(70, self._content.sizeHint().height() + 4)
        )
        self.setFixedHeight(height)
        return width, height

    def show_for(self, entry, replies, anchor: QRect, bounds: QRect) -> None:
        """Show the card for *entry*, pinned beside *anchor* inside *bounds*."""
        width, height = self._resize_for(entry, replies, bounds)
        self.move(self._placement(anchor, bounds, width, height))
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def show_pinned(self, entry, replies, where: QRect, bounds: QRect) -> None:
        """Show the card at the author's own ``/Popup`` position.

        Acrobat parks an open popup off the right edge of the page, so *where*
        routinely falls outside the viewport; it is clamped in rather than
        re-anchored, which keeps the card where the author put it whenever
        there is room. Never takes focus: these open by themselves on load and
        must not steal the keyboard from the page.
        """
        width, height = self._resize_for(entry, replies, bounds)
        x = max(bounds.left() + _GAP, min(where.left(), bounds.right() - width - _GAP))
        y = max(bounds.top() + _GAP, min(where.top(), bounds.bottom() - height - _GAP))
        self.move(QPoint(int(x), int(y)))
        self.show()
        self.raise_()

    def _placement(self, anchor: QRect, bounds: QRect, w: int, h: int) -> QPoint:
        """Prefer below-right of the marker; flip when that leaves the view."""
        x = anchor.right() + _GAP
        if x + w > bounds.right():
            x = anchor.left() - _GAP - w
        x = max(bounds.left() + _GAP, min(x, bounds.right() - w - _GAP))
        y = anchor.bottom() + _GAP
        if y + h > bounds.bottom():
            y = anchor.top() - _GAP - h
        y = max(bounds.top() + _GAP, min(y, bounds.bottom() - h - _GAP))
        return QPoint(int(x), int(y))

    # ------------------------------------------------------------------ close
    def dismiss(self) -> None:
        if not self.isVisible() and self._entry is None:
            return
        self._entry = None
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            event.accept()
            return
        super().keyPressEvent(event)
