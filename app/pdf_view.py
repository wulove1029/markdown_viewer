"""Custom paged PDF viewer with text selection, copy, and persistent highlights.

The previous implementation wrapped Qt's ``QPdfView`` widget, which renders and
searches PDFs but exposes **no** interactive text selection and no widget->page
coordinate transform — so copying text or drawing a highlight over the exact
selected glyphs was impossible (see the old ``pdf_notes.py`` note).

This version owns the page layout in a ``QAbstractScrollArea`` and uses an
asynchronous full-page/tile raster pipeline. Cached pixels provide an immediate
zoom preview while exact visible regions render away from the GUI thread.
Because we own the layout, mapping a mouse position to a page coordinate is
exact, which unlocks:

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

from math import ceil, floor
from pathlib import Path

from PySide6.QtCore import (
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtWidgets import (
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
from .pdf_render_cache import PdfRenderCache, PdfRenderMeta
from .pdf_render_scheduler import PdfRenderScheduler, PdfRenderSpec
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


class _PdfOutlineSignals(QObject):
    finished = Signal(int, object, object)


class _PdfOutlineTask(QRunnable):
    """Extract a PDF outline away from the GUI thread."""

    def __init__(self, generation: int, path: Path, password: str):
        super().__init__()
        self.generation = generation
        self.path = path
        self.password = password
        self.signals = _PdfOutlineSignals()

    def run(self):
        entries = extract_outline(self.path, self.password)
        self.signals.finished.emit(self.generation, self.path, entries)


class PdfView(QAbstractScrollArea):
    page_changed = Signal(int)          # 0-based current page
    search_count_changed = Signal(int)  # number of matches
    selection_changed = Signal(bool)    # True when a non-empty selection exists
    highlight_requested = Signal(object)  # {page, rects:[(x,y,w,h)], text, color}
    highlight_delete_requested = Signal(str)
    outline_ready = Signal(int, object, object)  # generation, path, entries
    zoom_changed = Signal(float)  # user-initiated wheel zoom

    PAGE_MARGIN = 12   # gutter around the page column (px)
    PAGE_SPACING = 12  # gap between pages (px)
    _CACHE_BUDGET_BYTES = 192 * 1024 * 1024
    _RENDER_IDLE_MS = 120
    _RENDER_DISPATCH_MS = 0
    _TILE_SIZE = 512  # physical pixels
    _TILE_DIMENSION_LIMIT = 4096
    _TILE_BYTE_LIMIT = 32 * 1024 * 1024
    _PREVIEW_MAX_DIMENSION = 2048  # physical pixels
    _WHEEL_ZOOM_STEP = 1.1
    _WHEEL_MIN_ZOOM = 0.5
    _WHEEL_MAX_ZOOM = 3.0
    _WHEEL_FRAME_MS = 16

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

        # PyMuPDF must reopen the file to extract bookmarks. Submit that work
        # only after visible PDF content has painted, and keep it off the GUI
        # thread. Generation/path checks discard late results after a tab switch
        # or reload, including the same path loaded again.
        self._load_generation = 0
        self._first_painted_generation = -1
        self._outline_requested_generation = -1
        self._outline_tasks: dict[int, _PdfOutlineTask] = {}
        self._outline_pool = QThreadPool.globalInstance()
        self._outline_submit_generation = -1
        self._outline_submit_timer = QTimer(self)
        self._outline_submit_timer.setSingleShot(True)
        self._outline_submit_timer.timeout.connect(self._submit_painted_outline)

        # High-resolution wheels/touchpads can deliver many deltas per frame.
        # Coalesce them so layout, cache invalidation, and PDF rendering happen
        # at most once per short frame instead of once per input packet.
        self._pending_wheel_zoom: float | None = None
        self._pending_wheel_zoom_raw: float | None = None
        self._pending_wheel_anchor = None
        self._wheel_zoom_timer = QTimer(self)
        self._wheel_zoom_timer.setSingleShot(True)
        self._wheel_zoom_timer.setInterval(self._WHEEL_FRAME_MS)
        self._wheel_zoom_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._wheel_zoom_timer.timeout.connect(self._apply_pending_wheel_zoom)

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

        # Rendering is two-stage: paint the closest cached page immediately,
        # then replace it with exact full-page or visible-tile images produced
        # off the GUI thread.  The byte-budgeted cache can later store texture
        # handles instead of QPixmaps when the compositor moves to RHI.
        self._cache = PdfRenderCache(self._CACHE_BUDGET_BYTES)
        self._render_scheduler = PdfRenderScheduler(self, max_inflight=2)
        self._render_scheduler.rendered.connect(self._on_rendered_image)
        self._render_scheduler.capacity_available.connect(
            self._pump_render_queue
        )
        self._render_backend_ready = False
        self._layout_epoch = 0
        self._wanted_render_specs: list[PdfRenderSpec] = []
        self._wanted_render_keys: set = set()
        self._failed_render_keys: set = set()
        self._last_dpr100 = self._dpr100()
        self._render_idle_timer = QTimer(self)
        self._render_idle_timer.setSingleShot(True)
        self._render_idle_timer.setInterval(self._RENDER_IDLE_MS)
        self._render_idle_timer.timeout.connect(self._rebuild_render_queue)
        self._render_dispatch_timer = QTimer(self)
        self._render_dispatch_timer.setSingleShot(True)
        self._render_dispatch_timer.timeout.connect(
            self._rebuild_render_queue
        )

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
        # Preserve the final wheel delta when a reload or tab switch happens
        # before the short frame timer has fired.
        self.flush_pending_wheel_zoom()
        self._reset_render_pipeline(clear_cache=True)
        self._load_generation += 1
        self._outline_submit_timer.stop()
        self._outline_submit_generation = -1
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
            self._render_backend_ready = self._render_scheduler.begin_document(
                self._load_generation,
                path,
                self._password,
            )
            self._schedule_render_dispatch()
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
        self._layout_epoch += 1
        self._wanted_render_specs.clear()
        self._wanted_render_keys.clear()
        self._failed_render_keys.clear()
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
        if self._cache:
            self._defer_exact_render()
        else:
            self._schedule_render_dispatch()
        self.viewport().update()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._schedule_render_dispatch()
        self.viewport().update()
        cur = self.current_page()
        if cur != self._current_page:
            self._current_page = cur
            self.page_changed.emit(cur)

    def wheelEvent(self, event):
        if not (
            event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta:
            base = (
                self._pending_wheel_zoom_raw
                if self._pending_wheel_zoom_raw is not None
                else self._zoom_factor
            )
            raw_target = base * (
                self._WHEEL_ZOOM_STEP ** (delta / 120.0)
            )
            target = max(
                self._WHEEL_MIN_ZOOM,
                min(self._WHEEL_MAX_ZOOM, raw_target),
            )
            if abs(target - self._zoom_factor) < 1e-6:
                # An outward gesture at a limit, or opposite deltas within the
                # same frame, has no visual work to do. Do not leave stale
                # pending state behind for a later file switch to flush.
                self._cancel_pending_wheel_zoom()
            else:
                self._pending_wheel_zoom = target
                # Keep the unclamped value within this frame so opposite wheel
                # packets cancel exactly even when the first one hit a limit.
                self._pending_wheel_zoom_raw = raw_target
                self._pending_wheel_anchor = event.position()
                if not self._wheel_zoom_timer.isActive():
                    self._wheel_zoom_timer.start()
        # Ctrl+wheel is reserved for zoom. Accept it even at a limit so the
        # same gesture never falls through and unexpectedly scrolls the page.
        event.accept()

    def _cancel_pending_wheel_zoom(self) -> None:
        self._wheel_zoom_timer.stop()
        self._pending_wheel_zoom = None
        self._pending_wheel_zoom_raw = None
        self._pending_wheel_anchor = None

    def flush_pending_wheel_zoom(self) -> None:
        """Apply the last coalesced wheel delta immediately, if one exists."""
        self._apply_pending_wheel_zoom()

    def _apply_pending_wheel_zoom(self) -> None:
        self._wheel_zoom_timer.stop()
        target = self._pending_wheel_zoom
        anchor = self._pending_wheel_anchor
        self._pending_wheel_zoom = None
        self._pending_wheel_zoom_raw = None
        self._pending_wheel_anchor = None
        if target is None or abs(target - self._zoom_factor) < 1e-6:
            return
        self.set_zoom_factor(target, anchor=anchor)
        self.zoom_changed.emit(self._zoom_factor)

    # ================= rendering =================
    def _dpr100(self) -> int:
        dpr = self.viewport().devicePixelRatioF() or 1.0
        return max(1, round(dpr * 100))

    def _reset_render_pipeline(self, *, clear_cache: bool) -> None:
        """Invalidate queued raster work without waiting for worker threads."""
        self._render_backend_ready = False
        self._render_idle_timer.stop()
        self._render_dispatch_timer.stop()
        self._wanted_render_specs.clear()
        self._wanted_render_keys.clear()
        self._failed_render_keys.clear()
        self._render_scheduler.invalidate()
        if clear_cache:
            self._cache.clear()

    def _schedule_render_dispatch(self) -> None:
        """Queue exact work soon when the viewport is at a stable scale."""
        if (
            not self._render_backend_ready
            or self._doc.status() != QPdfDocument.Status.Ready
            or not self._page_pix
            or self._render_idle_timer.isActive()
        ):
            return
        if not self._render_dispatch_timer.isActive():
            self._render_dispatch_timer.start(self._RENDER_DISPATCH_MS)

    def _defer_exact_render(self) -> None:
        """Keep old pixels as a preview until zoom/resize input becomes idle."""
        self._render_dispatch_timer.stop()
        self._wanted_render_specs.clear()
        self._wanted_render_keys.clear()
        self._failed_render_keys.clear()
        if (
            self._render_backend_ready
            and self._doc.status() == QPdfDocument.Status.Ready
            and self._page_pix
        ):
            self._render_idle_timer.start(self._RENDER_IDLE_MS)

    def _page_physical_size(
        self,
        page: int,
        dpr100: int | None = None,
    ) -> tuple[int, int]:
        dpr = (dpr100 or self._dpr100()) / 100.0
        width, height = self._page_pix[page]
        return max(1, round(width * dpr)), max(1, round(height * dpr))

    def _page_render_key(
        self,
        page: int,
        page_px: tuple[int, int],
        dpr100: int,
    ):
        return (
            "page",
            self._load_generation,
            page,
            page_px[0],
            page_px[1],
            dpr100,
        )

    def _preview_render_key(
        self,
        page: int,
        page_px: tuple[int, int],
        dpr100: int,
    ):
        return (
            "preview",
            self._load_generation,
            page,
            page_px[0],
            page_px[1],
            dpr100,
        )

    def _tile_render_key(
        self,
        page: int,
        page_px: tuple[int, int],
        dpr100: int,
        content_rect: tuple[int, int, int, int],
    ):
        return (
            "tile",
            self._load_generation,
            page,
            page_px[0],
            page_px[1],
            dpr100,
            *content_rect,
        )

    def _uses_tiles(self, page_px: tuple[int, int]) -> bool:
        width, height = page_px
        return (
            max(width, height) > self._TILE_DIMENSION_LIMIT
            or width * height * 4 > self._TILE_BYTE_LIMIT
        )

    def _preview_size(self, page_px: tuple[int, int]) -> tuple[int, int]:
        width, height = page_px
        ratio = min(1.0, self._PREVIEW_MAX_DIMENSION / max(width, height))
        return max(1, round(width * ratio)), max(1, round(height * ratio))

    def _visible_pages(self) -> list[int]:
        if not self._page_tops:
            return []
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        pages = [
            page
            for page, page_top in enumerate(self._page_tops)
            if page_top + self._page_pix[page][1] > top and page_top < bottom
        ]
        if not pages:
            page = self.current_page()
            if 0 <= page < len(self._page_tops):
                pages.append(page)
        return pages

    def _tile_specs_for_page(
        self,
        page: int,
        page_px: tuple[int, int],
        dpr100: int,
    ) -> tuple[list[PdfRenderSpec], list[PdfRenderSpec]]:
        """Return visible tiles and a one-tile prefetch ring, center first."""
        page_left = self._page_lefts[page]
        page_top = self._page_tops[page]
        logical_w, logical_h = self._page_pix[page]
        physical_per_x = page_px[0] / max(1, logical_w)
        physical_per_y = page_px[1] / max(1, logical_h)
        view_left = self.horizontalScrollBar().value()
        view_top = self.verticalScrollBar().value()
        view_right = view_left + self.viewport().width()
        view_bottom = view_top + self.viewport().height()

        local_left = max(0.0, view_left - page_left)
        local_top = max(0.0, view_top - page_top)
        local_right = min(float(logical_w), view_right - page_left)
        local_bottom = min(float(logical_h), view_bottom - page_top)
        if local_right <= local_left or local_bottom <= local_top:
            return [], []

        physical_left = max(0, floor(local_left * physical_per_x))
        physical_top = max(0, floor(local_top * physical_per_y))
        physical_right = min(page_px[0], ceil(local_right * physical_per_x))
        physical_bottom = min(page_px[1], ceil(local_bottom * physical_per_y))
        tile = self._TILE_SIZE
        columns = ceil(page_px[0] / tile)
        rows = ceil(page_px[1] / tile)
        x0 = max(0, physical_left // tile)
        y0 = max(0, physical_top // tile)
        x1 = min(columns - 1, max(physical_left, physical_right - 1) // tile)
        y1 = min(rows - 1, max(physical_top, physical_bottom - 1) // tile)
        visible_cells = {
            (column, row)
            for row in range(y0, y1 + 1)
            for column in range(x0, x1 + 1)
        }
        ring_cells = set()
        for column, row in visible_cells:
            for near_x in range(max(0, column - 1), min(columns, column + 2)):
                for near_y in range(max(0, row - 1), min(rows, row + 2)):
                    if (near_x, near_y) not in visible_cells:
                        ring_cells.add((near_x, near_y))

        center_x = (
            view_left + self.viewport().width() / 2 - page_left
        ) * physical_per_x
        center_y = (
            view_top + self.viewport().height() / 2 - page_top
        ) * physical_per_y

        def distance(cell):
            column, row = cell
            tile_center_x = min(page_px[0], (column + 0.5) * tile)
            tile_center_y = min(page_px[1], (row + 0.5) * tile)
            return (tile_center_x - center_x) ** 2 + (tile_center_y - center_y) ** 2

        def make_spec(cell):
            column, row = cell
            x = column * tile
            y = row * tile
            rect = (
                x,
                y,
                min(tile, page_px[0] - x),
                min(tile, page_px[1] - y),
            )
            return PdfRenderSpec(
                self._tile_render_key(page, page_px, dpr100, rect),
                self._load_generation,
                self._layout_epoch,
                page,
                "tile",
                dpr100,
                page_px,
                rect,
            )

        visible = [make_spec(cell) for cell in sorted(visible_cells, key=distance)]
        # Bound speculative work; every currently visible tile remains wanted.
        prefetch = [
            make_spec(cell)
            for cell in sorted(ring_cells, key=distance)[:32]
        ]
        return visible, prefetch

    def _visible_render_specs(self) -> list[PdfRenderSpec]:
        dpr100 = self._dpr100()
        visible_pages = self._visible_pages()
        if not visible_pages:
            return []
        previews: list[PdfRenderSpec] = []
        visible_exact: list[PdfRenderSpec] = []
        prefetch: list[PdfRenderSpec] = []

        for page in visible_pages:
            page_px = self._page_physical_size(page, dpr100)
            if not self._uses_tiles(page_px):
                visible_exact.append(
                    PdfRenderSpec(
                        self._page_render_key(page, page_px, dpr100),
                        self._load_generation,
                        self._layout_epoch,
                        page,
                        "page",
                        dpr100,
                        page_px,
                    )
                )
                continue

            # A reduced whole-page image gives immediate visual continuity
            # while exact visible tiles arrive. Reuse any older whole-page
            # level before spending another render request on a preview.
            if self._cache.best_page_preview(
                self._load_generation, page, dpr100, page_px
            ) is None:
                preview_px = self._preview_size(page_px)
                previews.append(
                    PdfRenderSpec(
                        self._preview_render_key(page, preview_px, dpr100),
                        self._load_generation,
                        self._layout_epoch,
                        page,
                        "preview",
                        dpr100,
                        preview_px,
                    )
                )
            page_visible, page_prefetch = self._tile_specs_for_page(
                page, page_px, dpr100
            )
            visible_exact.extend(page_visible)
            prefetch.extend(page_prefetch)

        # Preload one neighbouring page after all pixels in the viewport.
        neighbours = set()
        for page in visible_pages:
            if page > 0:
                neighbours.add(page - 1)
            if page + 1 < len(self._page_pix):
                neighbours.add(page + 1)
        neighbours.difference_update(visible_pages)
        for page in sorted(neighbours):
            page_px = self._page_physical_size(page, dpr100)
            if self._uses_tiles(page_px):
                preview_px = self._preview_size(page_px)
                prefetch.append(
                    PdfRenderSpec(
                        self._preview_render_key(page, preview_px, dpr100),
                        self._load_generation,
                        self._layout_epoch,
                        page,
                        "preview",
                        dpr100,
                        preview_px,
                    )
                )
            else:
                prefetch.append(
                    PdfRenderSpec(
                        self._page_render_key(page, page_px, dpr100),
                        self._load_generation,
                        self._layout_epoch,
                        page,
                        "page",
                        dpr100,
                        page_px,
                    )
                )
        return previews + visible_exact + prefetch

    def _rebuild_render_queue(self) -> None:
        self._render_dispatch_timer.stop()
        if (
            not self._render_backend_ready
            or self._doc.status() != QPdfDocument.Status.Ready
            or not self._page_pix
        ):
            return
        dpr100 = self._dpr100()
        if dpr100 != self._last_dpr100:
            self._last_dpr100 = dpr100
            self._layout_epoch += 1
            self._failed_render_keys.clear()

        desired = self._visible_render_specs()
        self._wanted_render_keys = {spec.key for spec in desired}
        self._wanted_render_specs = [
            spec
            for spec in desired
            if self._cache.get(spec.key) is None
            and not self._render_scheduler.is_pending(spec.generation, spec.key)
            and spec.key not in self._failed_render_keys
        ]
        self._pump_render_queue()

    def _pump_render_queue(self) -> None:
        generation = self._load_generation
        while (
            self._wanted_render_specs
            and self._render_scheduler.has_capacity(generation)
        ):
            spec = self._wanted_render_specs.pop(0)
            if (
                spec.generation != generation
                or spec.layout_epoch != self._layout_epoch
                or spec.key not in self._wanted_render_keys
                or self._cache.get(spec.key) is not None
                or self._render_scheduler.is_pending(generation, spec.key)
            ):
                continue
            if not self._render_scheduler.request(spec):
                self._failed_render_keys.add(spec.key)

    def _on_rendered_image(self, spec: PdfRenderSpec, image: QImage) -> None:
        if spec.generation != self._load_generation:
            return
        if (
            spec.layout_epoch != self._layout_epoch
            or spec.dpr100 != self._dpr100()
            or spec.key not in self._wanted_render_keys
        ):
            # A stable-size resize can produce the same render key in a newer
            # epoch. The idle rebuild skipped it while Qt still owned the old
            # request, so rebuild again now that the slot is free.
            self._schedule_render_dispatch()
            return
        expected = (
            spec.content_rect[2:]
            if spec.content_rect is not None
            else spec.page_px
        )
        if (
            image.isNull()
            or image.width() != expected[0]
            or image.height() != expected[1]
        ):
            self._failed_render_keys.add(spec.key)
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._failed_render_keys.add(spec.key)
            return
        pixmap.setDevicePixelRatio(spec.dpr100 / 100.0)
        meta = PdfRenderMeta(
            spec.generation,
            spec.page,
            spec.kind,
            spec.dpr100,
            spec.page_px,
            spec.content_rect,
        )
        if self._cache.put(
            spec.key,
            pixmap,
            int(image.sizeInBytes()),
            meta,
        ):
            self._failed_render_keys.discard(spec.key)
            self.viewport().update()
        self._pump_render_queue()

    def _pixmap_for(self, page: int) -> QPixmap | None:
        """Return a cached whole-page raster; never render in paintEvent."""
        if not (0 <= page < len(self._page_pix)):
            return None
        dpr100 = self._dpr100()
        page_px = self._page_physical_size(page, dpr100)
        key = self._page_render_key(page, page_px, dpr100)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        preview = self._cache.best_page_preview(
            self._load_generation, page, dpr100, page_px
        )
        return preview[1] if preview is not None else None

    @staticmethod
    def _draw_cached_pixmap(
        painter: QPainter,
        target: QRectF,
        pixmap: QPixmap,
        *,
        smooth: bool,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        painter.drawPixmap(
            target,
            pixmap,
            # QPainter's source rectangle is expressed in physical pixmap
            # pixels even when the pixmap carries a devicePixelRatio.
            QRectF(0.0, 0.0, pixmap.width(), pixmap.height()),
        )

    def _paint_page_raster(
        self,
        painter: QPainter,
        page: int,
        sx: float,
        sy: float,
        logical_w: int,
        logical_h: int,
    ) -> bool:
        """Paint preview then exact tiles; return whether PDF pixels were drawn."""
        page_target = QRectF(sx, sy, logical_w, logical_h)
        painter.fillRect(page_target, QColor("#ffffff"))
        dpr100 = self._dpr100()
        page_px = self._page_physical_size(page, dpr100)
        content_drawn = False

        exact = self._cache.get(
            self._page_render_key(page, page_px, dpr100)
        )
        if exact is not None:
            self._draw_cached_pixmap(
                painter, page_target, exact, smooth=False
            )
            return True

        preview = self._cache.best_page_preview(
            self._load_generation, page, dpr100, page_px
        )
        if preview is not None:
            self._draw_cached_pixmap(
                painter, page_target, preview[1], smooth=True
            )
            content_drawn = True

        if self._uses_tiles(page_px):
            viewport_rect = QRectF(self.viewport().rect())
            for key, _value, meta in self._cache.items_for_page(
                self._load_generation, page, ("tile",)
            ):
                if meta.dpr100 != dpr100 or meta.page_px != page_px:
                    continue
                if meta.clip is None:
                    continue
                x, y, width, height = meta.clip
                logical_per_x = logical_w / page_px[0]
                logical_per_y = logical_h / page_px[1]
                target = QRectF(
                    sx + x * logical_per_x,
                    sy + y * logical_per_y,
                    width * logical_per_x,
                    height * logical_per_y,
                )
                if not target.intersects(viewport_rect):
                    continue
                pixmap = self._cache.get(key)
                if pixmap is None:
                    continue
                # PDFium returns transparent ARGB tiles. Clear the preview
                # beneath the exact tile so transparent or soft-masked pixels
                # composite once onto white instead of darkening twice.
                painter.fillRect(target, QColor("#ffffff"))
                self._draw_cached_pixmap(
                    painter, target, pixmap, smooth=False
                )
                content_drawn = True
        return content_drawn

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
        content_page_seen = False
        for p in range(len(self._page_sizes)):
            top = self._page_tops[p]
            w, h = self._page_pix[p]
            sy = top - oy
            if sy + h < 0 or sy > vp_h:
                continue
            sx = self._page_lefts[p] - ox
            content_page_seen = self._paint_page_raster(
                painter, p, sx, sy, w, h
            ) or content_page_seen
            self._paint_overlays(painter, p, ox, oy)
        painter.end()
        self._schedule_render_dispatch()
        if (
            self._doc.status() == QPdfDocument.Status.Ready
            and content_page_seen
            and self._first_painted_generation != self._load_generation
        ):
            self._first_painted_generation = self._load_generation
            self._outline_submit_generation = self._load_generation
            # Queue submission so paintEvent returns before the worker can
            # contend for the GIL while PyMuPDF opens the file.
            self._outline_submit_timer.start(0)

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

    def highlight_at(self, pos) -> str | None:
        """Return the id of the saved highlight under a viewport position."""
        page, pt = self._pos_to_page(pos)
        if page is None or pt is None:
            return None
        for hl in reversed(self._highlights):
            if hl.page != page:
                continue
            for r in hl.rects:
                if QRectF(r.x, r.y, r.w, r.h).contains(pt):
                    return hl.id
        return None

    def _latest_highlight_id(self) -> str | None:
        if not self._highlights:
            return None
        idx, highlight = max(
            enumerate(self._highlights),
            key=lambda item: (item[1].created or "", item[0]),
        )
        return highlight.id

    def undo_last_highlight(self) -> bool:
        hid = self._latest_highlight_id()
        if not hid:
            return False
        self.highlight_delete_requested.emit(hid)
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
            self._pen_mode
            and event.matches(QKeySequence.StandardKey.Undo)
            and self.undo_last_highlight()
        ):
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
        # QContextMenuEvent has no position(); map the global point so the
        # hit-test works no matter which widget the event was delivered to.
        hit_highlight_id = self.highlight_at(
            self.viewport().mapFromGlobal(event.globalPos())
        )
        if hit_highlight_id:
            delete = menu.addAction("刪除此螢光標記")
            delete.triggered.connect(
                lambda _checked=False, hid=hit_highlight_id: (
                    self.highlight_delete_requested.emit(hid)
                )
            )
            menu.addSeparator()
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

    def load_generation(self) -> int:
        """Generation token for the current load, used to reject stale UI work."""
        return self._load_generation

    def request_outline(self) -> bool:
        """Start one background outline request for the current loaded file."""
        generation = self._load_generation
        if (
            not self._path
            or self._doc.status() != QPdfDocument.Status.Ready
            or self._outline_requested_generation == generation
        ):
            return False
        self._outline_requested_generation = generation
        task = _PdfOutlineTask(generation, self._path, self._password)
        self._outline_tasks[generation] = task
        task.signals.finished.connect(self._on_outline_finished)
        self._outline_pool.start(task)
        return True

    def _submit_painted_outline(self) -> None:
        if self._outline_submit_generation != self._load_generation:
            return
        self.request_outline()

    def _on_outline_finished(self, generation: int, path, entries) -> None:
        self._outline_tasks.pop(generation, None)
        if generation != self._load_generation or Path(path) != self._path:
            return
        self.outline_ready.emit(generation, path, entries)

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
    def zoom_factor(self) -> float:
        return self._zoom_factor

    def set_zoom_factor(self, factor: float, anchor=None) -> None:
        self._cancel_pending_wheel_zoom()
        factor = max(0.25, min(5.0, factor))
        if abs(factor - self._zoom_factor) < 1e-6:
            return
        cur = self.current_page()
        anchor_pos = None
        anchor_page = None
        anchor_point = None
        if anchor is not None and self._page_tops:
            anchor_pos = anchor.toPoint() if hasattr(anchor, "toPoint") else anchor
            anchor_page, anchor_point = self._pos_to_page(anchor_pos)
        self._zoom_factor = factor
        self._relayout()
        if (
            anchor_pos is not None
            and anchor_page is not None
            and anchor_point is not None
            and 0 <= anchor_page < len(self._page_tops)
        ):
            content_x = (
                self._page_lefts[anchor_page] + anchor_point.x() * self._scale
            )
            content_y = (
                self._page_tops[anchor_page] + anchor_point.y() * self._scale
            )
            self.horizontalScrollBar().setValue(
                round(content_x - anchor_pos.x())
            )
            self.verticalScrollBar().setValue(
                round(content_y - anchor_pos.y())
            )
        else:
            self.jump_to_page(cur)
        self._defer_exact_render()
        self.viewport().update()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._bg = QColor(theme.surface_alt)
        self.viewport().update()
