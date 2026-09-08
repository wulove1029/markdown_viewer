"""An Acrobat-style comment card for one embedded PDF annotation.

The on-page bubble marker says *that* a highlight carries a comment; this card
says *what* it says, and lets the reader answer. It is laid out the way
Acrobat's own note card is — a title row with the author, the time and a close
button, the comment itself, the reply thread underneath, and a reply box along
the bottom — and drawn as a rounded, softly shadowed panel rather than a
default frame.

It is a plain ``QFrame`` stacked on the ``PdfView`` viewport rather than a
tooltip or a dialog, so the reader can look at the marked text and the comment
at the same time, drag the card out of the way by its title row, and dismiss it
with Esc or a click elsewhere.

The card never touches the PDF itself: it emits what the reader asked for and
``MainWindow`` performs the (incremental, backed-up) write through
``pdf_annotation_writer``.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .pdf_embedded_annotations import kind_label, summary_text
from .theme import LIGHT, Theme

CARD_WIDTH = 300
CARD_MAX_HEIGHT = 360
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
    """The card's title line: type, author, and when it was last edited."""
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


class _ReplyRow(QWidget):
    """One reply in the thread: author, time, and text (editable if ours)."""

    edit_submitted = Signal(object, str)
    delete_requested = Signal(object)

    def __init__(self, entry, theme: Theme, editable: bool, parent=None):
        super().__init__(parent)
        self._entry = entry
        self._theme = theme
        self._editable = bool(editable)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(1)

        top = QLabel(f"{entry.author or '（未署名）'}　{_pretty_date(entry.modified)}")
        top.setStyleSheet(
            f"color: {theme.text_muted}; font-weight: 600; background: transparent;"
        )
        layout.addWidget(top)

        self._text = QLabel(summary_text(entry) or "（無文字內容）")
        self._text.setWordWrap(True)
        self._text.setStyleSheet(f"color: {theme.text}; background: transparent;")
        layout.addWidget(self._text)

        self._editor = QLineEdit(self)
        self._editor.hide()
        self._editor.returnPressed.connect(self._commit)
        self._editor.editingFinished.connect(self._cancel_if_idle)
        layout.addWidget(self._editor)

        if self._editable:
            self.setToolTip("按兩下可編輯；右鍵可刪除")
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    @property
    def entry(self):
        return self._entry

    def is_editing(self) -> bool:
        return self._editor.isVisible()

    def begin_edit(self) -> bool:
        if not self._editable:
            return False
        self._editor.setText(self._entry.content or "")
        self._text.hide()
        self._editor.show()
        self._editor.setFocus()
        return True

    def _cancel_if_idle(self) -> None:
        if self._editor.isVisible() and not self._editor.hasFocus():
            self._editor.hide()
            self._text.show()

    def _commit(self) -> None:
        text = self._editor.text().strip()
        self._editor.hide()
        self._text.show()
        if text and text != (self._entry.content or ""):
            self.edit_submitted.emit(self._entry, text)

    def mouseDoubleClickEvent(self, event):
        if self.begin_edit():
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        if not self._editable:
            return
        menu = QMenu(self)
        menu.addAction("編輯…", self.begin_edit)
        menu.addAction("刪除", lambda: self.delete_requested.emit(self._entry))
        menu.exec(event.globalPos())


class _CardHeader(QWidget):
    """The draggable title row; drags move the whole card."""

    drag_moved = Signal(QPoint)
    drag_finished = Signal()

    def __init__(self, card: "PdfAnnotationCard"):
        super().__init__(card)
        self._card = card
        self._press: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint() - self._card.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None:
            self.drag_moved.emit(event.globalPosition().toPoint() - self._press)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._press is not None:
            self._press = None
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class PdfAnnotationCard(QFrame):
    """Non-modal Acrobat-style card for one annotation plus its reply thread."""

    closed = Signal()
    moved = Signal()
    reply_submitted = Signal(object, str)     # parent entry, text
    edit_submitted = Signal(object, str)      # entry, new text
    delete_requested = Signal(object)         # entry

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pdfAnnotationCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._theme: Theme = LIGHT
        self._entry = None
        self._replies: list = []
        self._reply_rows: list[_ReplyRow] = []
        self._write_blocked_reason: str = ""
        self._author: str = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- title row -----------------------------------------------------
        self._header = _CardHeader(self)
        self._header.setCursor(Qt.CursorShape.SizeAllCursor)
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 8, 6, 6)
        header_layout.setSpacing(6)
        self._author_label = QLabel()
        self._time_label = QLabel()
        self._close_button = QToolButton()
        self._close_button.setText("×")
        self._close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._close_button.setToolTip("關閉")
        self._close_button.setAutoRaise(True)
        self._close_button.clicked.connect(self.dismiss)
        header_layout.addWidget(self._author_label)
        header_layout.addWidget(self._time_label)
        header_layout.addStretch(1)
        header_layout.addWidget(self._close_button)
        outer.addWidget(self._header)
        self._header.drag_moved.connect(self._on_drag)
        self._header.drag_finished.connect(self.moved.emit)

        # --- body + replies (the scrolling part) ---------------------------
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 2, 12, 8)
        self._content_layout.setSpacing(6)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll, 1)

        # --- reply box -----------------------------------------------------
        self._footer = QWidget(self)
        footer_layout = QHBoxLayout(self._footer)
        footer_layout.setContentsMargins(10, 6, 10, 8)
        footer_layout.setSpacing(6)
        self._reply_edit = QLineEdit()
        self._reply_edit.setPlaceholderText("新增回覆…")
        self._reply_edit.returnPressed.connect(self._submit_reply)
        self._send_button = QToolButton()
        self._send_button.setText("送出")
        self._send_button.clicked.connect(self._submit_reply)
        footer_layout.addWidget(self._reply_edit, 1)
        footer_layout.addWidget(self._send_button)
        outer.addWidget(self._footer)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(shadow)

        self.apply_theme(LIGHT)
        self.hide()

    # ---------------------------------------------------------------- content
    def entry(self):
        return self._entry

    def reply_edit(self) -> QLineEdit:
        return self._reply_edit

    def close_button(self) -> QToolButton:
        return self._close_button

    def header(self) -> QWidget:
        return self._header

    def reply_rows(self) -> list:
        return list(self._reply_rows)

    def set_author(self, author: str) -> None:
        """Whose annotations may be edited/deleted from this card."""
        self._author = str(author or "").strip()

    def set_write_blocked(self, reason: str) -> None:
        """Disable the reply box, showing *reason* instead of the placeholder."""
        self._write_blocked_reason = str(reason or "")
        blocked = bool(self._write_blocked_reason)
        self._reply_edit.setEnabled(not blocked)
        self._send_button.setEnabled(not blocked)
        self._reply_edit.setPlaceholderText(
            self._write_blocked_reason or "新增回覆…"
        )
        self._reply_edit.setToolTip(self._write_blocked_reason)

    def write_blocked_reason(self) -> str:
        return self._write_blocked_reason

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"#pdfAnnotationCard {{ background: {theme.surface};"
            f" border: 1px solid {theme.border}; border-radius: 8px; }}"
            f"#pdfAnnotationCard QLabel {{ background: transparent;"
            f" color: {theme.text}; }}"
            f"#pdfAnnotationCard QScrollArea {{ background: transparent;"
            " border: none; }"
            f"#pdfAnnotationCard QToolButton {{ background: transparent;"
            f" color: {theme.text_muted}; border: none; padding: 2px 6px;"
            " border-radius: 4px; }"
            f"#pdfAnnotationCard QToolButton:hover {{"
            f" background: {theme.surface_hover}; color: {theme.text}; }}"
            f"#pdfAnnotationCard QLineEdit {{ background: {theme.surface_hover};"
            f" color: {theme.text}; border: 1px solid {theme.border};"
            " border-radius: 4px; padding: 4px 6px; }"
        )
        self._content.setStyleSheet("background: transparent;")
        self._header.setStyleSheet("background: transparent;")
        self._footer.setStyleSheet(
            f"background: transparent; border-top: 1px solid {theme.border};"
        )
        self._author_label.setStyleSheet(
            f"font-weight: 700; color: {theme.text}; background: transparent;"
        )
        self._time_label.setStyleSheet(
            f"color: {theme.text_muted}; background: transparent;"
        )
        if self._entry is not None:
            self._rebuild(self._entry, self._replies)

    def _clear(self) -> None:
        self._reply_rows = []
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent before the deferred delete, so a widget awaiting
                # deletion cannot be measured as part of the next layout.
                widget.setParent(None)
                widget.deleteLater()

    def _label(self, text: str, *, muted=False, bold=False) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        style = "background: transparent;"
        style += f"color: {self._theme.text_muted};" if muted else ""
        if bold:
            style += "font-weight: 600;"
        label.setStyleSheet(style)
        return label

    def _add(self, widget) -> None:
        """Add a row and un-hide it immediately.

        A widget added to a layout is only shown on the next event-loop turn,
        and a still-hidden widget contributes nothing to the layout's size —
        which made a rebuilt card measure as empty and collapse onto its
        minimum height with a scrollbar over two lines of text.
        """
        self._content_layout.addWidget(widget)
        widget.show()

    def _rebuild(self, entry, replies) -> None:
        self._clear()
        self._author_label.setText(entry.author or kind_label(entry))
        self._time_label.setText(_pretty_date(entry.modified))
        if entry.note_text and entry.marked_text:
            # Both exist: quote the marked passage, then the comment about it.
            self._add(self._label(f"「{entry.marked_text}」", muted=True))
        self._add(self._label(body_text(entry)))
        for reply in replies or ():
            row = _ReplyRow(
                reply,
                self._theme,
                editable=bool(self._author)
                and str(reply.author or "").strip() == self._author,
            )
            row.edit_submitted.connect(self.edit_submitted.emit)
            row.delete_requested.connect(self.delete_requested.emit)
            self._reply_rows.append(row)
            self._add(row)
        self._content_layout.addStretch(1)

    # ----------------------------------------------------------------- layout
    def content_height_for(self, width: int) -> int:
        """Height the scrolling part needs when laid out at *width* pixels.

        Word-wrapped labels only report a useful sizeHint once the layout has
        run at the final width. Reading sizeHint() straight after
        setFixedWidth() returns the pre-layout guess, which is what pinned
        every card at its minimum height with a scrollbar over two lines.
        """
        inner = max(60, int(width) - 2 * self.frameWidth())
        self._content.setFixedWidth(inner)
        layout = self._content.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        self._content.adjustSize()
        return max(
            self._content.sizeHint().height(), self._content.minimumSizeHint().height()
        )

    def chrome_height(self) -> int:
        """Title row plus reply box: the parts that never scroll."""
        return (
            self._header.sizeHint().height()
            + self._footer.sizeHint().height()
            + 2 * self.frameWidth()
        )

    def max_height_for(self, bounds: QRect) -> int:
        """Cap a card at 60% of the view, so a long thread scrolls instead."""
        limit = int(bounds.height() * 0.6) if bounds.height() > 0 else CARD_MAX_HEIGHT
        return max(90, min(CARD_MAX_HEIGHT, limit))

    def _resize_for(self, entry, replies, bounds: QRect) -> tuple[int, int]:
        self._entry = entry
        self._replies = list(replies or ())
        self._rebuild(entry, self._replies)
        width = min(CARD_WIDTH, max(180, bounds.width() - 2 * _GAP))
        self.setFixedWidth(width)
        limit = self.max_height_for(bounds)
        chrome = self.chrome_height()
        needed = self.content_height_for(width) + chrome
        if needed > limit:
            # It will scroll after all: re-wrap in the width the scrollbar
            # leaves behind, so the last column of text is not clipped.
            bar = self._scroll.verticalScrollBar().sizeHint().width()
            needed = self.content_height_for(width - bar) + chrome
        height = min(limit, max(90, needed))
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
        """Show the card at a caller-chosen position, clamped into *bounds*.

        Used for Acrobat's own ``/Popup`` placement (which routinely sits off
        the right edge of the page) and for a card the reader has dragged.
        Never takes focus: these open by themselves and must not steal the
        keyboard from the page.
        """
        width, height = self._resize_for(entry, replies, bounds)
        self.move(self.clamp(QPoint(where.left(), where.top()), bounds))
        self.show()
        self.raise_()

    def clamp(self, position: QPoint, bounds: QRect) -> QPoint:
        x = max(
            bounds.left() + _GAP,
            min(position.x(), bounds.right() - self.width() - _GAP),
        )
        y = max(
            bounds.top() + _GAP,
            min(position.y(), bounds.bottom() - self.height() - _GAP),
        )
        return QPoint(int(x), int(y))

    def _placement(self, anchor: QRect, bounds: QRect, w: int, h: int) -> QPoint:
        """Prefer below-right of the marker; flip when that leaves the view."""
        x = anchor.right() + _GAP
        if x + w > bounds.right():
            x = anchor.left() - _GAP - w
        y = anchor.bottom() + _GAP
        if y + h > bounds.bottom():
            y = anchor.top() - _GAP - h
        return self.clamp(QPoint(int(x), int(y)), bounds)

    def _on_drag(self, position: QPoint) -> None:
        parent = self.parentWidget()
        bounds = parent.rect() if parent is not None else self.geometry()
        self.move(self.clamp(position, bounds))

    # ------------------------------------------------------------------ reply
    def _submit_reply(self) -> None:
        if self._write_blocked_reason or self._entry is None:
            return
        text = self._reply_edit.text().strip()
        if not text:
            return
        # The text is *not* echoed into the thread here: the reply only shows
        # up once the write to the PDF actually succeeded and the annotations
        # have been re-read, so a failed save can never look like a success.
        self.reply_submitted.emit(self._entry, text)

    def clear_reply_input(self) -> None:
        self._reply_edit.clear()

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
