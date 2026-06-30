"""Custom paged PDF viewer with text selection, copy, and persistent highlights.

The previous implementation wrapped Qt's ``QPdfView`` widget, which renders and
searches PDFs but exposes **no** interactive text selection and no widget->page
coordinate transform — so copying text or drawing a highlight over the exact
selected glyphs was impossible (see the old ``pdf_notes.py`` note).

This version renders each page itself with ``QPdfDocument.render()`` and lays the
pages out in a ``QAbstractScrollArea``. Because we own the layout, mapping a
mouse position to a page coordinate is exact, which unlocks:

* drag-to-select text (``QPdfDocument.getSelection`` in PDF-point space),
* Ctrl+C / context-menu copy,
* colored highlights that pin to the selected text geometry and persist.

The public API (``load``/``search``/``jump_to_page``/``set_zoom_factor`` …) is
kept identical to the old widget so the surrounding window wiring — sidebar TOC,
shared search bar, remembered page, zoom — keeps working unchanged. Search reuses
``QPdfSearchModel`` purely as a (still asynchronous) result model; we draw the
hit rectangles ourselves.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeySequence, QPainter, QPixmap
from PyQt6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions, QPdfSearchModel
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QInputDialog,
    QLineEdit,
    QMenu,
)

try:  # outline extraction (already a project dependency)
    import pymupdf
except Exception:  # pragma: no cover - import guard
    pymupdf = None

from .pdf_highlights import DEFAULT_COLOR
from .theme import LIGHT, Theme

# Highlighter palette shared with the markdown annotation layer.
PALETTE: list[tuple[str, str]] = [
    ("#ffd54f", "黃"),
    ("#a5d6a7", "綠"),
    ("#90caf9", "藍"),
    ("#f48fb1", "粉"),
    ("#ce93d8", "紫"),
]


def extract_outline(path, password: str = "") -> list[tuple[int, str, int]]:
    """Return [(level, title, page0), ...] from a PDF's bookmarks.

    Pure function (no Qt) so it is testable without constructing a widget.
    *password* unlocks an encrypted PDF before its bookmarks can be read; pass
    the password the viewer already accepted (empty string for normal files).
    Encrypted PDFs raise from ``get_toc`` until authenticated, so an empty or
    wrong password degrades to an empty outline rather than crashing.
    """
    if pymupdf is None or not path:
        return []
    try:
        with pymupdf.open(str(path)) as doc:
            if doc.needs_pass and not doc.authenticate(password or ""):
                return []
            toc = doc.get_toc()  # [[level, title, page1based], ...]
    except Exception:
        return []
    return [
        (max(1, int(level)), str(title), max(0, int(page) - 1))
        for level, title, page in toc
    ]


class PdfView(QAbstractScrollArea):
    page_changed = pyqtSignal(int)          # 0-based current page
    search_count_changed = pyqtSignal(int)  # number of matches
    selection_changed = pyqtSignal(bool)    # True when a non-empty selection exists
    highlight_requested = pyqtSignal(object)  # {page, rects:[(x,y,w,h)], text, color}

    PAGE_MARGIN = 12   # gutter around the page column (px)
    PAGE_SPACING = 12  # gap between pages (px)
    _CACHE_LIMIT = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = QPdfDocument(self)
        self._doc.statusChanged.connect(self._on_status)

        self._search = QPdfSearchModel(self)
        self._search.setDocument(self._doc)
        self._search.countChanged.connect(self._on_search_count)

        self._path: Path | None = None
        self._theme: Theme = LIGHT
        self._password = ""      # open-password accepted for the current file
        self._locked = False     # encrypted file left unopened (needs password)
        self._load_failed = False  # last load failed (locked, corrupt, missing)
        # Overridable so tests can unlock without a modal dialog. Called as
        # ``(file_name, attempt_index) -> str | None``; None means cancel.
        self._password_prompt = self._default_password_prompt

        # --- layout state (content == scaled pixel space) ---
        self._page_sizes: list = []   # QSizeF per page, in points
        self._page_tops: list[int] = []
        self._page_lefts: list[int] = []
        self._page_pix: list[tuple[int, int]] = []
        self._content_w = 0
        self._content_h = 0
        self._zoom_factor = 1.0
        self._scale = 1.0
        self._pending_page: int | None = None
        self._current_page = 0

        # --- selection ---
        self._dragging = False
        self._sel_page = -1
        self._sel_start: QPointF | None = None
        self._selection = None  # QPdfSelection
        self._text_bounds: dict[int, QRectF | None] = {}

        # --- highlighter ---
        self._highlights: list = []  # PdfHighlight (drawing copy; window owns truth)
        self._pen_mode = False
        self._pen_color = DEFAULT_COLOR

        # --- search results: list of (page, [QRectF, ...]) per match ---
        self._search_results: list = []
        self._search_index = -1

        self._cache: dict = {}

        self._bg = QColor(self._theme.surface_alt)
        self.viewport().setMouseTracking(True)
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(40)

    # ================= loading =================
    def load(self, path) -> bool:
        """Load *path*, prompting for a password when the PDF is encrypted.

        Returns True when the document opened; False when it is still locked —
        the user cancelled the prompt or the file could not be read.
        """
        path = Path(path)
        # Reuse a previously-accepted password when reloading the same file, so a
        # reload (button / external change) of an unlocked PDF doesn't re-prompt.
        candidate = self._password if path == self._path else ""
        self._path = path
        self._search_index = -1
        self._search_results = []
        self._search.setSearchString("")
        self._clear_selection()
        self.selection_changed.emit(False)
        self._highlights = []
        self._cache.clear()
        self._text_bounds.clear()
        self._password = ""
        self._locked = False
        self._load_failed = False
        # A page-restore request belongs to the file being loaded; never let a
        # previous (e.g. cancelled-encrypted) file's pending page leak into this
        # one and scroll it to the wrong page.
        self._pending_page = None
        err = self._authenticate_and_load(candidate)
        if err == QPdfDocument.Error.None_:
            return True
        # Failed to open. Distinguish "needs a password" (cancelled encrypted
        # file) from "cannot read" (corrupt / missing) so the placeholder and
        # the status message don't tell the user to enter a non-existent password.
        self._locked = err == QPdfDocument.Error.IncorrectPassword
        self._load_failed = True
        self._page_sizes = []
        self._relayout()
        self.viewport().update()
        return False

    def _authenticate_and_load(self, candidate: str):
        """Load the document, looping a password prompt while it stays locked.

        Returns the final ``QPdfDocument.Error``: ``None_`` on success,
        ``IncorrectPassword`` when the user cancelled an encrypted file, or the
        underlying error code for a corrupt / missing file.
        """
        err = self._try_password(candidate)
        if err == QPdfDocument.Error.None_:
            self._password = candidate
            return err
        if err != QPdfDocument.Error.IncorrectPassword:
            return err  # missing / corrupt file — not a password problem
        name = self._path.name if self._path else ""
        attempt = 0
        while True:
            pwd = self._password_prompt(name, attempt)
            if pwd is None:
                return QPdfDocument.Error.IncorrectPassword  # cancelled -> locked
            err = self._try_password(pwd)
            if err == QPdfDocument.Error.None_:
                self._password = pwd
                return err
            if err != QPdfDocument.Error.IncorrectPassword:
                return err
            attempt += 1

    def _try_password(self, pwd: str):
        """Set *pwd* and (re)load the document; return the QPdfDocument error.

        Re-loading a document that is already ``Ready`` returns a spurious
        ``IncorrectPassword`` for encrypted files (Qt quirk), so close it first.
        Loading from the Null/Error state needs no close — that is the normal
        first-load and wrong-password-retry path.
        """
        if self._doc.status() == QPdfDocument.Status.Ready:
            self._doc.close()
        self._doc.setPassword(pwd or "")
        return self._doc.load(str(self._path))

    def _default_password_prompt(self, name: str, attempt: int) -> str | None:
        """Modal password prompt; returns the entered password or None to cancel."""
        if attempt == 0:
            prompt = f"「{name}」受密碼保護，請輸入開啟密碼："
        else:
            prompt = f"密碼錯誤，請重新輸入「{name}」的開啟密碼："
        pwd, ok = QInputDialog.getText(
            self, "需要密碼", prompt, QLineEdit.EchoMode.Password
        )
        return pwd if ok else None

    def is_locked(self) -> bool:
        """True when the current file is an encrypted PDF awaiting a password."""
        return self._locked

    def _on_status(self, status):
        if status != QPdfDocument.Status.Ready:
            return
        count = self._doc.pageCount()
        self._page_sizes = [self._doc.pagePointSize(i) for i in range(count)]
        self._cache.clear()
        self._text_bounds.clear()
        self._relayout()
        if self._pending_page is not None:
            page = self._pending_page
            self._pending_page = None
            self.jump_to_page(page)
        self._current_page = self.current_page()
        self.viewport().update()

    def restore_page(self, page0: int) -> None:
        """Jump to *page0* now if loaded, otherwise once the document is ready."""
        if page0 <= 0:
            return
        if self._doc.status() == QPdfDocument.Status.Ready and self._page_tops:
            self.jump_to_page(page0)
        else:
            self._pending_page = page0

    # ================= layout =================
    def _relayout(self) -> None:
        self._cache.clear()
        if not self._page_sizes:
            self._page_tops = []
            self._page_lefts = []
            self._page_pix = []
            self._content_w = self._content_h = 0
            self._update_scrollbars()
            return
        vpw = max(1, self.viewport().width())
        max_w = max((s.width() for s in self._page_sizes), default=1.0) or 1.0
        base = (vpw - 2 * self.PAGE_MARGIN) / max_w
        self._scale = max(0.05, base) * self._zoom_factor
        scale = self._scale
        content_w = max(vpw, int(max_w * scale) + 2 * self.PAGE_MARGIN)
        tops, lefts, pix = [], [], []
        y = self.PAGE_MARGIN
        for s in self._page_sizes:
            w = max(1, round(s.width() * scale))
            h = max(1, round(s.height() * scale))
            tops.append(y)
            lefts.append(max(self.PAGE_MARGIN, (content_w - w) // 2))
            pix.append((w, h))
            y += h + self.PAGE_SPACING
        self._page_tops = tops
        self._page_lefts = lefts
        self._page_pix = pix
        self._content_w = content_w
        self._content_h = y - self.PAGE_SPACING + self.PAGE_MARGIN
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        vp = self.viewport().size()
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.setRange(0, max(0, self._content_h - vp.height()))
        vbar.setPageStep(vp.height())
        hbar.setRange(0, max(0, self._content_w - vp.width()))
        hbar.setPageStep(vp.width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        old_h = self._content_h
        frac = (self.verticalScrollBar().value() / old_h) if old_h else 0.0
        self._relayout()
        self.verticalScrollBar().setValue(int(frac * self._content_h))
        self.viewport().update()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.viewport().update()
        cur = self.current_page()
        if cur != self._current_page:
            self._current_page = cur
            self.page_changed.emit(cur)

    # ================= rendering =================
    def _pixmap_for(self, page: int) -> QPixmap | None:
        w, h = self._page_pix[page]
        if w <= 0 or h <= 0:
            return None
        dpr = self.devicePixelRatioF() or 1.0
        key = (page, w, h, round(dpr * 100))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        img = self._doc.render(
            page, QSize(int(w * dpr), int(h * dpr)), QPdfDocumentRenderOptions()
        )
        if img.isNull():
            return None
        # PDFium returns an ARGB image whose page background can be (semi-)
        # transparent. Composite it onto opaque white so transparent regions
        # (logos, soft-masked images) resolve to white like Adobe — instead of
        # letting the gray viewport background bleed through.
        base = QImage(img.size(), QImage.Format.Format_RGB32)
        base.fill(Qt.GlobalColor.white)
        compositor = QPainter(base)
        compositor.drawImage(0, 0, img)
        compositor.end()
        pm = QPixmap.fromImage(base)
        pm.setDevicePixelRatio(dpr)
        if len(self._cache) > self._CACHE_LIMIT:
            self._cache.clear()
        self._cache[key] = pm
        return pm

    def _page_rect_to_screen(self, page, x, y, w, h, ox, oy) -> QRectF:
        s = self._scale
        return QRectF(
            self._page_lefts[page] + x * s - ox,
            self._page_tops[page] + y * s - oy,
            w * s,
            h * s,
        )

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.fillRect(self.viewport().rect(), self._bg)
        if not self._page_tops:
            self._paint_placeholder(painter)
            painter.end()
            return
        ox = self.horizontalScrollBar().value()
        oy = self.verticalScrollBar().value()
        vp_h = self.viewport().height()
        white = QColor("#ffffff")
        for p in range(len(self._page_sizes)):
            top = self._page_tops[p]
            w, h = self._page_pix[p]
            sy = top - oy
            if sy + h < 0 or sy > vp_h:
                continue
            sx = self._page_lefts[p] - ox
            pm = self._pixmap_for(p)
            if pm is not None:
                painter.drawPixmap(int(sx), int(sy), pm)
            else:
                painter.fillRect(int(sx), int(sy), w, h, white)
            self._paint_overlays(painter, p, ox, oy)
        painter.end()

    def _paint_placeholder(self, painter):
        """Draw a centered message for the empty canvas when a load failed."""
        name = self._path.name if self._path else "此檔案"
        if self._locked:
            text = f"🔒 「{name}」受密碼保護，尚未解鎖。\n重新開啟檔案可再次輸入密碼。"
        elif self._load_failed:
            text = f"⚠️ 無法開啟「{name}」。\n檔案可能已損毀或無法讀取。"
        else:
            return  # nothing loaded yet (initial empty state) — leave blank
        painter.setPen(QColor(self._theme.text_subtle))
        font = painter.font()
        font.setPointSize(max(11, font.pointSize() + 1))
        painter.setFont(font)
        painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, text)

    def _paint_overlays(self, painter, p, ox, oy):
        # saved highlights
        for hl in self._highlights:
            if hl.page != p:
                continue
            col = QColor(hl.color)
            col.setAlpha(95)
            for r in hl.rects:
                painter.fillRect(
                    self._page_rect_to_screen(p, r.x, r.y, r.w, r.h, ox, oy), col
                )
        # search hits
        if self._search_results:
            normal = QColor("#ff9632")
            normal.setAlpha(110)
            current = QColor("#ffd200")
            current.setAlpha(160)
            for i, (page, rects) in enumerate(self._search_results):
                if page != p:
                    continue
                col = current if i == self._search_index else normal
                for r in rects:
                    painter.fillRect(
                        self._page_rect_to_screen(
                            p, r.x(), r.y(), r.width(), r.height(), ox, oy
                        ),
                        col,
                    )
        # live selection
        if (
            self._selection is not None
            and self._sel_page == p
            and self._selection.isValid()
        ):
            col = QColor(self._pen_color if self._pen_mode else "#3573e6")
            col.setAlpha(80)
            for poly in self._selection.bounds():
                br = poly.boundingRect()
                painter.fillRect(
                    self._page_rect_to_screen(
                        p, br.x(), br.y(), br.width(), br.height(), ox, oy
                    ),
                    col,
                )

    # ================= coordinate mapping =================
    def _point_on_page(self, page: int, pos) -> QPointF:
        """Map a viewport pixel to *page*'s point space (no band check)."""
        ox = self.horizontalScrollBar().value()
        oy = self.verticalScrollBar().value()
        s = self._scale or 1.0
        px = (pos.x() + ox - self._page_lefts[page]) / s
        py = (pos.y() + oy - self._page_tops[page]) / s
        return QPointF(px, py)

    def _pos_to_page(self, pos):
        """Return (page, QPointF) for a viewport pixel inside a page, else (None, None)."""
        oy = self.verticalScrollBar().value()
        cy = pos.y() + oy
        for p in range(len(self._page_tops)):
            top = self._page_tops[p]
            h = self._page_pix[p][1]
            if top <= cy <= top + h:
                return p, self._point_on_page(p, pos)
        return None, None

    def _text_bounds_for(self, page: int):
        if page in self._text_bounds:
            return self._text_bounds[page]
        try:
            br = self._doc.getAllText(page).boundingRectangle()
        except Exception:
            br = None
        if br is not None and br.width() <= 0:
            br = None
        self._text_bounds[page] = br
        return br

    def _clamp_point(self, page: int, pt: QPointF) -> QPointF:
        s = self._page_sizes[page]
        return QPointF(
            min(max(pt.x(), 0.0), s.width()),
            min(max(pt.y(), 0.0), s.height()),
        )

    def _clamp_end(self, page: int, pt: QPointF) -> QPointF:
        # getSelection returns an empty selection when the end point lands well
        # past the last glyph; clamp into the text bounds to avoid that.
        pt = self._clamp_point(page, pt)
        tb = self._text_bounds_for(page)
        if tb is not None:
            x = min(max(pt.x(), tb.left()), tb.right())
            y = min(max(pt.y(), tb.top() - 2), tb.bottom() + 2)
            return QPointF(x, y)
        return pt

    # ================= mouse / selection =================
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus()
        page, pt = self._pos_to_page(event.position().toPoint())
        if page is None:
            self._clear_selection()
            self.selection_changed.emit(False)
            self.viewport().update()
            return
        self._dragging = True
        self._sel_page = page
        self._sel_start = self._clamp_point(page, pt)
        self._selection = None
        self.viewport().update()

    def mouseMoveEvent(self, event):
        if not self._dragging or self._sel_page < 0 or self._sel_start is None:
            super().mouseMoveEvent(event)
            return
        end = self._clamp_end(self._sel_page, self._point_on_page(self._sel_page, event.position().toPoint()))
        sel = self._doc.getSelection(self._sel_page, self._sel_start, end)
        if sel.isValid():
            self._selection = sel
        self.viewport().update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        self._dragging = False
        if was_dragging and self._pen_mode and self.has_selection():
            self._emit_highlight(self._pen_color)
            self._clear_selection()
            self.selection_changed.emit(False)
        else:
            self.selection_changed.emit(self.has_selection())
        self.viewport().update()

    def has_selection(self) -> bool:
        return (
            self._selection is not None
            and self._selection.isValid()
            and bool(self._selection.text().strip())
        )

    def copy_selection(self) -> bool:
        if self.has_selection():
            QApplication.clipboard().setText(self._selection.text())
            return True
        return False

    def _emit_highlight(self, color: str) -> None:
        if not self.has_selection():
            return
        rects = []
        for poly in self._selection.bounds():
            br = poly.boundingRect()
            rects.append((br.x(), br.y(), br.width(), br.height()))
        if not rects:
            return
        self.highlight_requested.emit(
            {
                "page": self._sel_page,
                "rects": rects,
                "text": self._selection.text(),
                "color": color,
            }
        )

    def highlight_selection(self, color: str | None = None) -> bool:
        """Turn the current selection into a highlight request (manual trigger)."""
        if not self.has_selection():
            return False
        color = color or self._pen_color
        self._pen_color = color
        self._emit_highlight(color)
        self._clear_selection()
        self.selection_changed.emit(False)
        self.viewport().update()
        return True

    def _clear_selection(self):
        self._selection = None
        self._sel_page = -1
        self._sel_start = None
        self._dragging = False

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy) and self.copy_selection():
            event.accept()
            return
        if (
            event.key() == Qt.Key.Key_H
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and self.highlight_selection()
        ):
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {self._theme.surface};"
            f" border: 1px solid {self._theme.border}; color: {self._theme.text}; }}"
            f"QMenu::item:selected {{ background: {self._theme.surface_hover}; }}"
            f"QMenu::item:disabled {{ color: {self._theme.text_subtle}; }}"
        )
        if self.has_selection():
            copy = menu.addAction("複製")
            copy.triggered.connect(self.copy_selection)
            sub = menu.addMenu("螢光標記")
            for hex_color, label in PALETTE:
                act = sub.addAction(label)
                act.triggered.connect(
                    lambda _checked=False, c=hex_color: self.highlight_selection(c)
                )
        else:
            hint = menu.addAction("（先用滑鼠拖曳選取文字）")
            hint.setEnabled(False)
        menu.exec(event.globalPos())

    # ================= highlighter state =================
    def set_highlights(self, highlights) -> None:
        self._highlights = list(highlights or [])
        self.viewport().update()

    def set_pen_mode(self, on: bool) -> None:
        self._pen_mode = bool(on)
        self.viewport().setCursor(
            Qt.CursorShape.CrossCursor if on else Qt.CursorShape.IBeamCursor
        )

    def pen_mode(self) -> bool:
        return self._pen_mode

    def set_pen_color(self, color: str) -> None:
        self._pen_color = color or DEFAULT_COLOR

    def pen_color(self) -> str:
        return self._pen_color

    # ================= navigation =================
    def jump_to_page(self, page0: int) -> None:
        if not self._page_tops:
            return
        page0 = max(0, min(int(page0), len(self._page_tops) - 1))
        self.verticalScrollBar().setValue(
            max(0, int(self._page_tops[page0]) - self.PAGE_MARGIN)
        )

    def reveal(self, page: int, x: float, y: float, w: float, h: float) -> None:
        """Scroll so the page-point rect (x,y,w,h) is centered in the viewport."""
        if not self._page_tops or not (0 <= page < len(self._page_tops)):
            return
        s = self._scale
        cy = self._page_tops[page] + (y + h / 2) * s
        cx = self._page_lefts[page] + (x + w / 2) * s
        self.verticalScrollBar().setValue(int(cy - self.viewport().height() / 2))
        self.horizontalScrollBar().setValue(int(cx - self.viewport().width() / 2))

    def current_page(self) -> int:
        if not self._page_tops:
            return 0
        center = self.verticalScrollBar().value() + self.viewport().height() // 2
        for p in range(len(self._page_tops)):
            top = self._page_tops[p]
            h = self._page_pix[p][1]
            if top <= center < top + h + self.PAGE_SPACING:
                return p
        return min(
            range(len(self._page_tops)),
            key=lambda p: abs(self._page_tops[p] - self.verticalScrollBar().value()),
        )

    def page_count(self) -> int:
        return self._doc.pageCount()

    def outline(self) -> list[tuple[int, str, int]]:
        return extract_outline(self._path, self._password)

    # ================= search =================
    def search(self, text: str) -> None:
        self._search_index = -1
        self._search_results = []
        self._search.setSearchString(text or "")
        if not text:
            self.search_count_changed.emit(0)
            self.viewport().update()

    def _on_search_count(self):
        count = self._search.count()
        self._rebuild_search_results(count)
        self.search_count_changed.emit(count)
        if count > 0 and self._search_index < 0:
            self._search_index = 0
            self._scroll_to_search(0)
        self.viewport().update()

    def _rebuild_search_results(self, count: int) -> None:
        results = []
        for i in range(count):
            link = self._search.resultAtIndex(i)
            rects = list(link.rectangles())
            results.append((link.page(), rects))
        self._search_results = results

    def search_next(self) -> None:
        n = len(self._search_results)
        if n <= 0:
            return
        self._search_index = (self._search_index + 1) % n
        self._scroll_to_search(self._search_index)
        self.viewport().update()

    def search_prev(self) -> None:
        n = len(self._search_results)
        if n <= 0:
            return
        self._search_index = (self._search_index - 1) % n
        self._scroll_to_search(self._search_index)
        self.viewport().update()

    def clear_search(self) -> None:
        self._search_index = -1
        self._search_results = []
        self._search.setSearchString("")
        self.viewport().update()

    def _scroll_to_search(self, idx: int) -> None:
        if not (0 <= idx < len(self._search_results)) or not self._page_tops:
            return
        page, rects = self._search_results[idx]
        if not rects:
            self.jump_to_page(page)
            return
        r = rects[0]
        s = self._scale
        cy = self._page_tops[page] + (r.y() + r.height() / 2) * s
        cx = self._page_lefts[page] + (r.x() + r.width() / 2) * s
        self.verticalScrollBar().setValue(int(cy - self.viewport().height() / 2))
        self.horizontalScrollBar().setValue(int(cx - self.viewport().width() / 2))

    # ================= zoom / theme =================
    def set_zoom_factor(self, factor: float) -> None:
        factor = max(0.25, min(5.0, factor))
        if abs(factor - self._zoom_factor) < 1e-6:
            return
        cur = self.current_page()
        self._zoom_factor = factor
        self._relayout()
        self.jump_to_page(cur)
        self.viewport().update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._bg = QColor(theme.surface_alt)
        self.viewport().update()
