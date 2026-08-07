"""Deterministic contracts for asynchronous PDF page and tile scheduling."""

from dataclasses import dataclass

import pytest
from PySide6.QtCore import QObject, QRect, QSize, Signal, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPdf import (
    QPdfDocumentRenderOptions,
    QPdfPageRenderer as _RealQPdfPageRenderer,
)

pymupdf = pytest.importorskip("pymupdf")

from app import pdf_render_scheduler as scheduler_mod
from app.pdf_render_scheduler import PdfRenderScheduler, PdfRenderSpec


@dataclass
class _RecordedRequest:
    request_id: int
    page: int
    image_size: QSize
    options: QPdfDocumentRenderOptions


class _FakePageRenderer(QObject):
    RenderMode = _RealQPdfPageRenderer.RenderMode
    pageRendered = Signal(int, QSize, QImage, object, int)
    instances = []
    queued_request_ids = []

    def __init__(self, parent=None):
        super().__init__(parent)
        self.documents = []
        self.render_modes = []
        self.requests = []
        self._next_request_id = 1
        type(self).instances.append(self)

    def setDocument(self, document):
        self.documents.append(document)

    def setRenderMode(self, mode):
        self.render_modes.append(mode)

    def requestPage(self, page, image_size, options):
        if type(self).queued_request_ids:
            request_id = type(self).queued_request_ids.pop(0)
        else:
            request_id = self._next_request_id
            self._next_request_id += 1
        self.requests.append(
            _RecordedRequest(
                request_id=request_id,
                page=int(page),
                image_size=QSize(image_size),
                options=options,
            )
        )
        return request_id

    def complete(self, recorded, image=None):
        image = image if image is not None else QImage(recorded.image_size, QImage.Format.Format_ARGB32)
        self.pageRendered.emit(
            recorded.page,
            recorded.image_size,
            image,
            recorded.options,
            recorded.request_id,
        )


@pytest.fixture
def fake_page_renderer(monkeypatch):
    _FakePageRenderer.instances.clear()
    _FakePageRenderer.queued_request_ids.clear()
    monkeypatch.setattr(scheduler_mod, "QPdfPageRenderer", _FakePageRenderer)
    return _FakePageRenderer


@pytest.fixture
def quadrant_pdf(tmp_path):
    path = tmp_path / "quadrants.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=200)
    for rect, color in (
        ((0, 0, 200, 100), (1, 0, 0)),
        ((200, 0, 400, 100), (0, 1, 0)),
        ((0, 100, 200, 200), (0, 0, 1)),
        ((200, 100, 400, 200), (1, 1, 0)),
    ):
        page.draw_rect(rect, color=color, fill=color, width=0, overlay=True)
    document.save(str(path))
    document.close()
    return path


def _tile_spec(key, content_rect, *, generation=1, layout_epoch=1):
    return PdfRenderSpec(
        key=key,
        generation=generation,
        layout_epoch=layout_epoch,
        page=0,
        kind="tile",
        dpr100=100,
        page_px=(400, 200),
        content_rect=content_rect,
    )


def _finish_requests_and_invalidate(scheduler, renderer):
    for request in list(renderer.requests):
        renderer.complete(request)
    scheduler.invalidate()


def _image_bytes(image):
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return bytes(converted.constBits()[: converted.sizeInBytes()])


def test_scheduler_explicitly_enables_multithreaded_rendering(
    qapp, quadrant_pdf, fake_page_renderer
):
    scheduler = PdfRenderScheduler()
    assert scheduler.begin_document(1, quadrant_pdf)
    renderer = fake_page_renderer.instances[-1]
    try:
        assert renderer.render_modes == [
            _RealQPdfPageRenderer.RenderMode.MultiThreaded
        ]
    finally:
        scheduler.invalidate()


def test_request_id_zero_is_rejected_without_consuming_capacity(
    qapp, quadrant_pdf, fake_page_renderer
):
    fake_page_renderer.queued_request_ids[:] = [0]
    scheduler = PdfRenderScheduler(max_inflight=1)
    assert scheduler.begin_document(1, quadrant_pdf)
    renderer = fake_page_renderer.instances[-1]
    spec = _tile_spec("zero", (0, 0, 200, 100))
    try:
        assert scheduler.request(spec) is False
        assert scheduler.pending_count(1) == 0
        assert scheduler.is_pending(1, spec.key) is False
        assert scheduler.has_capacity(1) is True
    finally:
        # Harmless for the fixed implementation and drains the buggy request-id
        # zero bookkeeping while this regression test is being developed.
        renderer.complete(renderer.requests[0])
        scheduler.invalidate()


def test_tile_request_uses_full_scaled_size_and_qrect_plus_one_workaround(
    qapp, quadrant_pdf, fake_page_renderer
):
    scheduler = PdfRenderScheduler()
    assert scheduler.begin_document(1, quadrant_pdf)
    renderer = fake_page_renderer.instances[-1]
    spec = _tile_spec("bottom-right", (200, 100, 200, 100))
    try:
        assert scheduler.request(spec)
        request = renderer.requests[-1]

        assert request.page == 0
        assert request.image_size == QSize(200, 100)
        assert request.options.scaledSize() == QSize(400, 200)
        assert request.options.scaledClipRect() == QRect(200, 100, 201, 101)
    finally:
        _finish_requests_and_invalidate(scheduler, renderer)


def test_scheduler_tile_options_reassemble_to_the_exact_full_page_render(
    qapp, quadrant_pdf, fake_page_renderer
):
    scheduler = PdfRenderScheduler(max_inflight=4)
    assert scheduler.begin_document(1, quadrant_pdf)
    renderer = fake_page_renderer.instances[-1]
    specs = [
        _tile_spec("top-left", (0, 0, 200, 100)),
        _tile_spec("top-right", (200, 0, 200, 100)),
        _tile_spec("bottom-left", (0, 100, 200, 100)),
        _tile_spec("bottom-right", (200, 100, 200, 100)),
    ]
    try:
        assert all(scheduler.request(spec) for spec in specs)
        document = scheduler._sessions_by_generation[1].document
        full = document.render(
            0,
            QSize(400, 200),
            QPdfDocumentRenderOptions(),
        )
        assert not full.isNull()

        tiled = QImage(full.size(), full.format())
        tiled.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tiled)
        for spec, request in zip(specs, renderer.requests):
            tile = document.render(
                request.page,
                request.image_size,
                request.options,
            )
            assert not tile.isNull()
            x, y, _width, _height = spec.content_rect
            painter.drawImage(x, y, tile)
        painter.end()

        assert _image_bytes(tiled) == _image_bytes(full)
    finally:
        _finish_requests_and_invalidate(scheduler, renderer)
