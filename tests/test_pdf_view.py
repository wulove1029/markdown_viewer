"""Tests for PDF outline extraction (PyMuPDF-backed, no Qt widget needed)."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QWheelEvent

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_view import PdfView, extract_outline


def _paint_one_completed_raster(view: PdfView) -> None:
    """Deliver one deterministic async result, then synchronously paint it."""
    view._render_dispatch_timer.stop()
    view._render_idle_timer.stop()
    spec = view._visible_render_specs()[0]
    view._wanted_render_keys = {spec.key}
    view._wanted_render_specs = []
    width, height = (
        spec.content_rect[2:]
        if spec.content_rect is not None
        else spec.page_px
    )
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    view._on_rendered_image(spec, image)
    view.viewport().grab()
    view._submit_painted_outline()


def _wheel_event(
    view: PdfView,
    pos: QPointF,
    *,
    angle_y: int = 0,
    pixel_y: int = 0,
    modifiers=Qt.KeyboardModifier.ControlModifier,
) -> QWheelEvent:
    event = QWheelEvent(
        pos,
        QPointF(view.viewport().mapToGlobal(pos.toPoint())),
        QPoint(0, pixel_y),
        QPoint(0, angle_y),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    event.ignore()
    return event


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.set_toc(
        [
            [1, "Chapter 1", 1],
            [2, "Section 1.1", 2],
            [1, "Chapter 2", 3],
        ]
    )
    doc.save(str(path))
    doc.close()
    return path


def test_outline_extraction(sample_pdf):
    # 1-based PDF pages become 0-based for QPdfPageNavigator.jump().
    assert extract_outline(sample_pdf) == [
        (1, "Chapter 1", 0),
        (2, "Section 1.1", 1),
        (1, "Chapter 2", 2),
    ]


def test_outline_empty_when_no_toc(tmp_path):
    path = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    assert extract_outline(path) == []


def test_outline_none_path_is_empty():
    assert extract_outline(None) == []


def test_outline_bad_file_is_empty(tmp_path):
    bad = tmp_path / "notreally.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    assert extract_outline(bad) == []


def test_outline_starts_once_after_first_page_paints(qapp, sample_pdf):
    view = PdfView()
    started = []

    class _Pool:
        def start(self, task):
            started.append((task, bool(view._cache)))

    view._outline_pool = _Pool()
    view.resize(800, 600)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        assert started == []

        _paint_one_completed_raster(view)

        assert len(started) == 1
        assert started[0][1] is True  # page pixmap existed before submission
        view.viewport().grab()
        view._submit_painted_outline()
        assert len(started) == 1
    finally:
        view._outline_tasks.clear()
        view.close()


def test_stale_outline_result_is_ignored(qapp):
    view = PdfView()
    received = []
    view.outline_ready.connect(
        lambda generation, path, entries: received.append(
            (generation, Path(path), entries)
        )
    )
    view._load_generation = 2
    view._path = Path("current.pdf")
    view._outline_tasks = {1: object(), 2: object()}

    # Same path, older generation: path-only guards are insufficient after a
    # reload or an A -> B -> A tab sequence.
    view._on_outline_finished(1, view._path, [(1, "old", 0)])
    assert received == []
    assert 1 not in view._outline_tasks

    expected = [(1, "current", 0)]
    view._on_outline_finished(2, view._path, expected)
    assert received == [(2, view._path, expected)]
    assert 2 not in view._outline_tasks


def test_ctrl_wheel_zooms_around_cursor_without_losing_page_point(
    qapp, sample_pdf
):
    view = PdfView()

    class _Pool:
        def start(self, _task):
            pass

    view._outline_pool = _Pool()
    view.resize(760, 520)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        qapp.processEvents()
        qapp.processEvents()
        view.verticalScrollBar().setValue(view._page_tops[1])
        pos = QPointF(
            view.viewport().width() * 0.55,
            view.viewport().height() * 0.4,
        )
        before_page, before_point = view._pos_to_page(pos.toPoint())
        assert before_page == 1
        changed = []
        view.zoom_changed.connect(changed.append)

        event = _wheel_event(view, pos, angle_y=120)
        qapp.sendEvent(view.viewport(), event)
        view._apply_pending_wheel_zoom()

        after_page, after_point = view._pos_to_page(pos.toPoint())
        assert event.isAccepted()
        assert view.zoom_factor() == pytest.approx(1.1)
        assert changed == pytest.approx([1.1])
        assert after_page == before_page
        assert after_point.x() == pytest.approx(before_point.x(), abs=2.0)
        assert after_point.y() == pytest.approx(before_point.y(), abs=2.0)

        down_event = _wheel_event(view, pos, angle_y=-120)
        qapp.sendEvent(view.viewport(), down_event)
        view._apply_pending_wheel_zoom()
        round_trip_page, round_trip_point = view._pos_to_page(pos.toPoint())
        assert down_event.isAccepted()
        assert view.zoom_factor() == pytest.approx(1.0)
        assert changed == pytest.approx([1.1, 1.0])
        assert round_trip_page == before_page
        assert round_trip_point.x() == pytest.approx(before_point.x(), abs=2.0)
        assert round_trip_point.y() == pytest.approx(before_point.y(), abs=2.0)
    finally:
        view._outline_tasks.clear()
        view.close()


def test_ctrl_pixel_wheel_zooms_and_limits_never_fall_through_to_scroll(
    qapp, sample_pdf
):
    view = PdfView()

    class _Pool:
        def start(self, _task):
            pass

    view._outline_pool = _Pool()
    view.resize(760, 520)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        qapp.processEvents()
        qapp.processEvents()
        pos = QPointF(view.viewport().rect().center())

        pixel_event = _wheel_event(view, pos, pixel_y=120)
        qapp.sendEvent(view.viewport(), pixel_event)
        view._apply_pending_wheel_zoom()
        assert pixel_event.isAccepted()
        assert view.zoom_factor() == pytest.approx(1.1)

        view.set_zoom_factor(view._WHEEL_MAX_ZOOM)
        view.verticalScrollBar().setValue(
            view.verticalScrollBar().maximum() // 2
        )
        scroll_at_max = view.verticalScrollBar().value()
        max_event = _wheel_event(view, pos, angle_y=120)
        qapp.sendEvent(view.viewport(), max_event)
        assert view._pending_wheel_zoom is None
        assert view._wheel_zoom_timer.isActive() is False
        view._apply_pending_wheel_zoom()
        assert max_event.isAccepted()
        assert view.zoom_factor() == pytest.approx(view._WHEEL_MAX_ZOOM)
        assert view.verticalScrollBar().value() == scroll_at_max

        view.set_zoom_factor(view._WHEEL_MIN_ZOOM)
        scroll_at_min = view.verticalScrollBar().value()
        min_event = _wheel_event(view, pos, angle_y=-120)
        qapp.sendEvent(view.viewport(), min_event)
        assert view._pending_wheel_zoom is None
        assert view._wheel_zoom_timer.isActive() is False
        view._apply_pending_wheel_zoom()
        assert min_event.isAccepted()
        assert view.zoom_factor() == pytest.approx(view._WHEEL_MIN_ZOOM)
        assert view.verticalScrollBar().value() == scroll_at_min
    finally:
        view._outline_tasks.clear()
        view.close()


def test_ctrl_wheel_burst_coalesces_into_one_layout(qapp, sample_pdf):
    view = PdfView()

    class _Pool:
        def start(self, _task):
            pass

    view._outline_pool = _Pool()
    view.resize(760, 520)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        qapp.processEvents()
        qapp.processEvents()
        assert (
            view._wheel_zoom_timer.timerType()
            == Qt.TimerType.PreciseTimer
        )
        pos = QPointF(view.viewport().rect().center())
        original_relayout = view._relayout
        relayout_calls = []

        def counted_relayout():
            relayout_calls.append(True)
            return original_relayout()

        view._relayout = counted_relayout
        changed = []
        view.zoom_changed.connect(changed.append)

        for _ in range(8):
            qapp.sendEvent(
                view.viewport(), _wheel_event(view, pos, angle_y=15)
            )

        assert view.zoom_factor() == pytest.approx(1.0)
        assert view._pending_wheel_zoom == pytest.approx(1.1)
        assert view._wheel_zoom_timer.isActive()
        assert relayout_calls == []
        assert changed == []

        view._apply_pending_wheel_zoom()

        assert view.zoom_factor() == pytest.approx(1.1)
        assert view._wheel_zoom_timer.isActive() is False
        assert relayout_calls == [True]
        assert changed == pytest.approx([1.1])

        # Raw deltas must cancel within a frame even if the first event clamps
        # at the upper wheel-zoom limit.
        view.set_zoom_factor(2.9)
        relayout_calls.clear()
        changed.clear()
        qapp.sendEvent(
            view.viewport(), _wheel_event(view, pos, angle_y=120)
        )
        qapp.sendEvent(
            view.viewport(), _wheel_event(view, pos, angle_y=-120)
        )
        assert view.zoom_factor() == pytest.approx(2.9)
        assert view._pending_wheel_zoom is None
        assert view._wheel_zoom_timer.isActive() is False
        assert relayout_calls == []
        assert changed == []

        # A timeout already queued by Qt is harmless after cancellation.
        view._apply_pending_wheel_zoom()
        assert view.zoom_factor() == pytest.approx(2.9)
        assert relayout_calls == []
        assert changed == []
    finally:
        view._outline_tasks.clear()
        view.close()


def test_loading_another_pdf_flushes_the_last_wheel_frame(
    qapp, sample_pdf
):
    view = PdfView()

    class _Pool:
        def start(self, _task):
            pass

    view._outline_pool = _Pool()
    view.resize(760, 520)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        qapp.processEvents()
        qapp.processEvents()
        pos = QPointF(view.viewport().rect().center())
        changed = []
        view.zoom_changed.connect(changed.append)

        qapp.sendEvent(
            view.viewport(), _wheel_event(view, pos, angle_y=120)
        )
        assert view.zoom_factor() == pytest.approx(1.0)
        assert view._pending_wheel_zoom == pytest.approx(1.1)
        assert view._wheel_zoom_timer.isActive()

        assert view.load(sample_pdf) is True

        assert view.zoom_factor() == pytest.approx(1.1)
        assert changed == pytest.approx([1.1])
        assert view._pending_wheel_zoom is None
        assert view._wheel_zoom_timer.isActive() is False

        view._apply_pending_wheel_zoom()
        assert view.zoom_factor() == pytest.approx(1.1)
        assert changed == pytest.approx([1.1])
    finally:
        view._outline_tasks.clear()
        view.close()


def test_wheel_without_ctrl_keeps_zoom_and_scrolls_normally(qapp, sample_pdf):
    view = PdfView()

    class _Pool:
        def start(self, _task):
            pass

    view._outline_pool = _Pool()
    view.resize(760, 520)
    view.show()
    try:
        assert view.load(sample_pdf) is True
        qapp.processEvents()
        qapp.processEvents()
        view.verticalScrollBar().setValue(
            view.verticalScrollBar().maximum() // 2
        )
        before_scroll = view.verticalScrollBar().value()
        before_zoom = view.zoom_factor()
        pos = QPointF(view.viewport().rect().center())
        changed = []
        view.zoom_changed.connect(changed.append)

        event = _wheel_event(
            view,
            pos,
            angle_y=-120,
            modifiers=Qt.KeyboardModifier.NoModifier,
        )
        qapp.sendEvent(view.viewport(), event)

        assert view.zoom_factor() == pytest.approx(before_zoom)
        assert view.verticalScrollBar().value() > before_scroll
        assert changed == []
    finally:
        view._outline_tasks.clear()
        view.close()
