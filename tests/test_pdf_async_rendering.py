"""Deterministic integration contracts for the asynchronous PDF canvas.

The tests use a tiny fake scheduler so failures describe viewport/cache state,
not worker timing or the speed of the machine running the suite.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtPdf import QPdfDocument

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_render_cache import PdfRenderMeta
from app.pdf_render_scheduler import PdfRenderSpec
from app.pdf_view import PdfView


class _FakeScheduler:
    """Small bounded queue implementing the interface consumed by PdfView."""

    def __init__(self, max_inflight=2):
        self.max_inflight = int(max_inflight)
        self.generation = -1
        self.requests: list[PdfRenderSpec] = []
        self.pending: dict[object, PdfRenderSpec] = {}
        self.max_seen = 0

    def begin_document(self, generation, _path, _password=""):
        self.generation = int(generation)
        return True

    def invalidate(self):
        self.generation = -1
        self.pending.clear()

    def is_pending(self, generation, key):
        return int(generation) == self.generation and key in self.pending

    def has_capacity(self, generation=None):
        generation = self.generation if generation is None else int(generation)
        return generation == self.generation and len(self.pending) < self.max_inflight

    def request(self, spec):
        if (
            spec.generation != self.generation
            or spec.key in self.pending
            or not self.has_capacity(spec.generation)
        ):
            return False
        self.requests.append(spec)
        self.pending[spec.key] = spec
        self.max_seen = max(self.max_seen, len(self.pending))
        return True

    def complete(self, key):
        self.pending.pop(key)


class _MainDocumentRenderProbe:
    """Ready document facade whose render calls are observable."""

    def __init__(self):
        self.render_calls = []

    def status(self):
        return QPdfDocument.Status.Ready

    def render(self, *args, **kwargs):
        self.render_calls.append((args, kwargs))
        return QImage()


@pytest.fixture
def one_page_pdf(tmp_path):
    path = tmp_path / "async-render.pdf"
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((72, 72), "asynchronous PDF rendering")
    document.save(str(path))
    document.close()
    return path


@pytest.fixture
def loaded_view(qapp, one_page_pdf):
    view = PdfView()
    scheduler = _FakeScheduler(max_inflight=2)
    # The production scheduler created by PdfView has no document yet. Replacing
    # this attribute lets load/layout run normally while all raster requests stay
    # deterministic and observable.
    view._render_scheduler = scheduler
    view.resize(760, 520)
    assert view.load(one_page_pdf) is True
    view._render_dispatch_timer.stop()
    scheduler.requests.clear()
    scheduler.pending.clear()
    try:
        yield view, scheduler
    finally:
        view._render_idle_timer.stop()
        view._render_dispatch_timer.stop()
        view._outline_submit_timer.stop()
        view._outline_tasks.clear()
        view.close()
        qapp.processEvents()


def _solid_pixmap(size: tuple[int, int], color: str, dpr100: int) -> QPixmap:
    pixmap = QPixmap(*size)
    pixmap.fill(QColor(color))
    pixmap.setDevicePixelRatio(dpr100 / 100.0)
    return pixmap


def _image(size: tuple[int, int], color="#4c8bf5") -> QImage:
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return image


def _spec(
    key,
    *,
    generation=1,
    layout_epoch=1,
    dpr100=100,
    page_px=(40, 60),
):
    return PdfRenderSpec(
        key=key,
        generation=generation,
        layout_epoch=layout_epoch,
        page=0,
        kind="page",
        dpr100=dpr100,
        page_px=page_px,
    )


def test_paint_never_calls_render_on_the_main_document(qapp, loaded_view):
    view, scheduler = loaded_view
    original_document = view._doc
    probe = _MainDocumentRenderProbe()
    view._doc = probe
    view._render_backend_ready = False
    try:
        target = QPixmap(view.size())
        target.fill(QColor("#000000"))
        view.render(target)
        qapp.processEvents()

        assert probe.render_calls == []
        assert scheduler.requests == []
    finally:
        view._doc = original_document


def test_zoom_keeps_old_page_preview_and_defers_exact_until_idle(loaded_view):
    view, scheduler = loaded_view
    dpr100 = view._dpr100()
    old_page_px = view._page_physical_size(0, dpr100)
    old_key = view._page_render_key(0, old_page_px, dpr100)
    old_pixmap = _solid_pixmap(old_page_px, "#e53935", dpr100)
    assert view._cache.put(
        old_key,
        old_pixmap,
        old_page_px[0] * old_page_px[1] * 4,
        PdfRenderMeta(
            view._load_generation,
            0,
            "page",
            dpr100,
            old_page_px,
        ),
    )

    view.set_zoom_factor(1.25)

    assert view._cache.get(old_key) is old_pixmap
    assert view._render_idle_timer.interval() == 120
    assert view._render_idle_timer.isActive()
    assert scheduler.requests == []

    logical_w, logical_h = view._page_pix[0]
    canvas = _image((logical_w, logical_h), "#ffffff")
    painter = QPainter(canvas)
    drawn = view._paint_page_raster(
        painter,
        0,
        0.0,
        0.0,
        logical_w,
        logical_h,
    )
    painter.end()
    center = canvas.pixelColor(logical_w // 2, logical_h // 2)
    assert drawn is True
    assert center.red() > 200 and center.green() < 100
    assert scheduler.requests == []

    # Fire the idle transition directly: the assertion is about the state
    # machine, not whether a CI timer wakes at exactly 120 ms.
    view._render_idle_timer.stop()
    view._rebuild_render_queue()

    assert len(scheduler.requests) == 1
    exact = scheduler.requests[0]
    assert exact.kind == "page"
    assert exact.layout_epoch == view._layout_epoch
    assert exact.page_px == view._page_physical_size(0, dpr100)
    assert exact.key != old_key


def test_dpr2_cached_pixmap_draws_the_complete_physical_source(qapp):
    """A DPR pixmap source rect is physical, not device-independent pixels."""
    source = QImage(4, 4, QImage.Format.Format_ARGB32)
    for y in range(4):
        for x in range(4):
            if x < 2 and y < 2:
                color = QColor("#f44336")
            elif y < 2:
                color = QColor("#4caf50")
            elif x < 2:
                color = QColor("#2196f3")
            else:
                color = QColor("#ffeb3b")
            source.setPixelColor(x, y, color)
    pixmap = QPixmap.fromImage(source)
    pixmap.setDevicePixelRatio(2.0)

    target = _image((20, 20), "#000000")
    painter = QPainter(target)
    PdfView._draw_cached_pixmap(
        painter,
        QRectF(0.0, 0.0, 20.0, 20.0),
        pixmap,
        smooth=False,
    )
    painter.end()

    assert target.pixelColor(5, 5) == QColor("#f44336")
    assert target.pixelColor(15, 5) == QColor("#4caf50")
    assert target.pixelColor(5, 15) == QColor("#2196f3")
    assert target.pixelColor(15, 15) == QColor("#ffeb3b")


def test_transparent_exact_tile_composites_once_over_white_not_preview(
    qapp, monkeypatch
):
    view = PdfView()
    monkeypatch.setattr(view, "_dpr100", lambda: 100)
    monkeypatch.setattr(view, "_uses_tiles", lambda _page_px: True)
    view._load_generation = 2
    view._page_pix = [(4, 4)]
    page_px = (4, 4)
    try:
        preview_key = view._preview_render_key(0, page_px, 100)
        preview = _solid_pixmap(page_px, "#000000", 100)
        assert view._cache.put(
            preview_key,
            preview,
            4 * 4 * 4,
            PdfRenderMeta(2, 0, "preview", 100, page_px),
        )

        tile_rect = (0, 0, 2, 4)
        tile_key = view._tile_render_key(0, page_px, 100, tile_rect)
        tile_image = QImage(2, 4, QImage.Format.Format_ARGB32)
        tile_image.fill(QColor(255, 0, 0, 128))
        tile = QPixmap.fromImage(tile_image)
        tile.setDevicePixelRatio(1.0)
        assert view._cache.put(
            tile_key,
            tile,
            int(tile_image.sizeInBytes()),
            PdfRenderMeta(2, 0, "tile", 100, page_px, tile_rect),
        )

        canvas = _image(page_px, "#ffffff")
        painter = QPainter(canvas)
        assert view._paint_page_raster(painter, 0, 0.0, 0.0, 4, 4)
        painter.end()

        exact_pixel = canvas.pixelColor(0, 2)
        assert exact_pixel.alpha() == 255
        assert exact_pixel.red() == 255
        assert exact_pixel.green() in (127, 128)
        assert exact_pixel.blue() in (127, 128)
        # Outside the exact tile the old black preview remains visible.
        assert canvas.pixelColor(3, 2) == QColor("#000000")
    finally:
        view.close()
        qapp.processEvents()


def test_tile_threshold_is_strictly_over_4096_or_32_mib(qapp):
    view = PdfView()
    try:
        # 4096 x 2048 x 4 is exactly 32 MiB: equality remains a whole page.
        assert view._uses_tiles((4096, 2048)) is False
        assert view._uses_tiles((4097, 1)) is True
        assert view._uses_tiles((4096, 2049)) is True
    finally:
        view.close()
        qapp.processEvents()


def test_visible_tiles_cover_viewport_and_precede_prefetch(qapp):
    view = PdfView()
    view.resize(1024, 768)
    view._dpr100 = lambda: 100
    try:
        page_px = (5000, 5000)
        view._load_generation = 4
        view._layout_epoch = 7
        view._page_sizes = [QSizeF(*page_px)]
        view._page_tops = [0]
        view._page_lefts = [0]
        view._page_pix = [page_px]
        view._content_w, view._content_h = page_px
        view._update_scrollbars()
        qapp.processEvents()
        view._update_scrollbars()
        view.horizontalScrollBar().setValue(730)
        view.verticalScrollBar().setValue(910)

        visible, prefetch = view._tile_specs_for_page(0, page_px, 100)
        assert visible
        assert prefetch

        tile = view._TILE_SIZE
        left = view.horizontalScrollBar().value()
        top = view.verticalScrollBar().value()
        right = left + view.viewport().width()
        bottom = top + view.viewport().height()
        expected_cells = {
            (column, row)
            for row in range(top // tile, (bottom - 1) // tile + 1)
            for column in range(left // tile, (right - 1) // tile + 1)
        }
        visible_cells = {
            (spec.content_rect[0] // tile, spec.content_rect[1] // tile)
            for spec in visible
        }
        prefetch_cells = {
            (spec.content_rect[0] // tile, spec.content_rect[1] // tile)
            for spec in prefetch
        }
        assert visible_cells == expected_cells
        assert visible_cells.isdisjoint(prefetch_cells)

        center_x = left + view.viewport().width() / 2
        center_y = top + view.viewport().height() / 2

        def distance(spec):
            x, y, width, height = spec.content_rect
            return (x + width / 2 - center_x) ** 2 + (
                y + height / 2 - center_y
            ) ** 2

        assert [distance(spec) for spec in visible] == sorted(
            distance(spec) for spec in visible
        )

        # With a whole-page preview already available, the public render plan
        # consists of all visible exact tiles before its speculative ring.
        preview_px = view._preview_size(page_px)
        preview_key = view._preview_render_key(0, preview_px, 100)
        preview = _solid_pixmap((1, 1), "#cccccc", 100)
        assert view._cache.put(
            preview_key,
            preview,
            4,
            PdfRenderMeta(4, 0, "preview", 100, preview_px),
        )
        plan = view._visible_render_specs()
        assert [spec.key for spec in plan[: len(visible)]] == [
            spec.key for spec in visible
        ]
        assert all(spec.key in {item.key for item in plan[len(visible) :]}
                   for spec in prefetch)
    finally:
        view._render_dispatch_timer.stop()
        view._render_idle_timer.stop()
        view.close()
        qapp.processEvents()


def test_render_result_rejects_stale_generation_epoch_size_and_dpr(
    qapp, monkeypatch
):
    view = PdfView()
    scheduler = _FakeScheduler()
    view._render_scheduler = scheduler
    view._load_generation = 8
    view._layout_epoch = 13
    monkeypatch.setattr(view, "_dpr100", lambda: 200)
    try:
        current = _spec(
            "current",
            generation=8,
            layout_epoch=13,
            dpr100=200,
            page_px=(40, 60),
        )

        rejected = (
            replace(current, key="old-generation", generation=7),
            replace(current, key="old-epoch", layout_epoch=12),
            replace(current, key="old-dpr", dpr100=100),
        )
        for spec in rejected:
            view._wanted_render_keys = {spec.key}
            view._on_rendered_image(spec, _image(spec.page_px))
            assert view._cache.get(spec.key) is None

        view._wanted_render_keys = {"wrong-image-size"}
        wrong_size = replace(current, key="wrong-image-size")
        view._on_rendered_image(wrong_size, _image((39, 60)))
        assert view._cache.get(wrong_size.key) is None
        assert wrong_size.key in view._failed_render_keys

        # Positive control: matching metadata is admitted and keeps its DPR.
        view._wanted_render_keys = {current.key}
        view._on_rendered_image(current, _image(current.page_px))
        cached = view._cache.get(current.key)
        assert cached is not None
        assert cached.devicePixelRatioF() == pytest.approx(2.0)
    finally:
        view.close()
        qapp.processEvents()


def test_same_key_old_epoch_completion_rebuilds_and_can_request_again(
    loaded_view
):
    view, scheduler = loaded_view
    current = view._visible_render_specs()[0]
    old = replace(current, layout_epoch=current.layout_epoch - 1)
    assert old.key == current.key

    # A stable-size relayout retains the render key. While Qt owns the old
    # epoch request, rebuilding correctly avoids submitting the duplicate.
    scheduler.requests[:] = [old]
    scheduler.pending[old.key] = old
    view._rebuild_render_queue()
    assert scheduler.requests == [old]
    assert view._wanted_render_specs == []

    # Match the real signal order: the scheduler releases the request ID/key,
    # then emits the old result. The view must reject it and schedule a fresh
    # rebuild, otherwise this key remains permanently blank.
    scheduler.complete(old.key)
    view._on_rendered_image(old, _image(old.page_px))
    assert view._cache.get(old.key) is None
    assert view._render_dispatch_timer.isActive()

    view._render_dispatch_timer.stop()
    view._rebuild_render_queue()

    assert len(scheduler.requests) == 2
    retried = scheduler.requests[-1]
    assert retried.key == current.key
    assert retried.layout_epoch == view._layout_epoch
    assert scheduler.pending[current.key] == retried


def test_view_never_submits_more_than_two_inflight_requests(qapp):
    view = PdfView()
    scheduler = _FakeScheduler(max_inflight=2)
    view._render_scheduler = scheduler
    view._load_generation = 3
    view._layout_epoch = 5
    scheduler.generation = 3
    specs = [
        _spec(
            f"tile-{index}",
            generation=3,
            layout_epoch=5,
            page_px=(512, 512),
        )
        for index in range(10)
    ]
    view._wanted_render_specs = list(specs)
    view._wanted_render_keys = {spec.key for spec in specs}
    try:
        view._pump_render_queue()
        assert len(scheduler.requests) == 2
        assert len(scheduler.pending) == 2
        assert scheduler.max_seen == 2

        scheduler.complete(scheduler.requests[0].key)
        view._pump_render_queue()
        assert len(scheduler.requests) == 3
        assert len(scheduler.pending) == 2
        assert scheduler.max_seen == 2
    finally:
        view.close()
        qapp.processEvents()


def test_tiled_paint_skips_cached_tiles_outside_the_viewport(
    qapp, monkeypatch
):
    view = PdfView()
    view.resize(640, 480)
    view._dpr100 = lambda: 100
    view._load_generation = 2
    page_px = (5000, 5000)
    view._page_pix = [page_px]
    visible_key = "visible"
    hidden_key = "offscreen"
    tile = _solid_pixmap((512, 512), "#336699", 100)
    assert view._cache.put(
        visible_key,
        tile,
        512 * 512 * 4,
        PdfRenderMeta(2, 0, "tile", 100, page_px, (0, 0, 512, 512)),
    )
    assert view._cache.put(
        hidden_key,
        tile,
        512 * 512 * 4,
        PdfRenderMeta(
            2,
            0,
            "tile",
            100,
            page_px,
            (4096, 4096, 512, 512),
        ),
    )
    draws = []
    monkeypatch.setattr(
        view,
        "_draw_cached_pixmap",
        lambda _painter, target, _pixmap, *, smooth: draws.append(
            (target, smooth)
        ),
    )
    try:
        canvas = _image(view.viewport().size().toTuple(), "#ffffff")
        painter = QPainter(canvas)
        assert view._paint_page_raster(
            painter, 0, 0.0, 0.0, page_px[0], page_px[1]
        )
        painter.end()

        assert len(draws) == 1
        assert draws[0][0].topLeft().toTuple() == (0.0, 0.0)
        assert draws[0][1] is False
    finally:
        view.close()
        qapp.processEvents()
