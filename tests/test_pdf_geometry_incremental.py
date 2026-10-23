"""Mixed-size geometry, navigation, and resize-anchor correctness."""

import random
import time

import pytest
from PySide6.QtCore import QPoint, QSizeF

from app.pdf_view import PdfView


@pytest.fixture
def view(qapp):
    widget = PdfView()
    widget.resize(900, 700)
    widget.show()
    qapp.processEvents()
    widget._page_sizes = [QSizeF(420 + (i % 3) * 90, 600 + (i % 5) * 50)
                          for i in range(3000)]
    widget._geometry_scale_width = 420
    widget._relayout()
    widget._finish_resize_layout()
    yield widget
    widget._resize_layout_timer.stop()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def _geometry(view):
    return (
        list(view._page_tops), list(view._page_lefts), list(view._page_pix),
        view._content_w, view._content_h, view._scale,
    )


def _assert_geometry_equal(actual, expected):
    names = ("page_tops", "page_lefts", "page_pix", "content_w", "content_h", "scale")
    for index in (5, 3, 4, 0, 1, 2):
        found, wanted = actual[index], expected[index]
        if found == wanted:
            continue
        if isinstance(found, list) and len(found) == len(wanted):
            page = next(i for i, pair in enumerate(zip(found, wanted)) if pair[0] != pair[1])
            pytest.fail(
                f"{names[index]}[{page}]: full={found[page]!r}, incremental={wanted[page]!r}"
            )
        pytest.fail(f"{names[index]}: full={found!r}, incremental={wanted!r}")


def test_incremental_mixed_paper_geometry_matches_complete_layout(view):
    rng = random.Random(22102026)
    for _ in range(10):
        changed = {rng.randrange(3000) for _ in range(40)}
        for page in changed:
            view._page_sizes[page] = QSizeF(rng.randrange(300, 1200), rng.randrange(200, 1600))
        view._geometry_dirty.update(changed)
        view._apply_geometry_updates()
        incremental = _geometry(view)
        view._relayout()
        _assert_geometry_equal(_geometry(view), incremental)


def test_widest_provisional_page_can_shrink_without_stale_horizontal_range(view):
    view._page_sizes = [QSizeF(1000, 800)] + [QSizeF(600, 800) for _ in range(2999)]
    view._relayout()
    view._page_sizes[0] = QSizeF(420, 700)
    view._geometry_dirty.add(0)
    view._apply_geometry_updates()
    assert max(view._layout_width_counts) == 600
    incremental = _geometry(view)
    view._relayout()
    _assert_geometry_equal(_geometry(view), incremental)


def test_geometry_update_during_deferred_resize_recomputes_all_page_pixels(view):
    view.resize(view.width() + 120, view.height())
    assert view.viewport().width() != view._layout_viewport_width
    view._page_sizes[1800] = QSizeF(960, 320)
    view._geometry_dirty.add(1800)
    view._apply_geometry_updates()
    incremental = _geometry(view)
    view._relayout()
    _assert_geometry_equal(_geometry(view), incremental)


def test_unmodified_geometry_batch_does_not_invalidate_render_layout(view):
    epoch = view._layout_epoch
    view._apply_geometry_updates()
    assert view._layout_epoch == epoch


def test_binary_navigation_matches_reference_for_boundaries_and_page_gaps(view):
    samples = [0, view._content_h - view.viewport().height()]
    for page in (0, 1, 100, 1500, 2998, 2999):
        samples.extend([view._page_tops[page] - 1, view._page_tops[page],
                        view._page_tops[page] + view._page_pix[page][1]])
    for value in samples:
        view.verticalScrollBar().setValue(value)
        top = view.verticalScrollBar().value()
        bottom = top + view.viewport().height()
        expected = [page for page, position in enumerate(view._page_tops)
                    if position + view._page_pix[page][1] > top and position < bottom]
        assert view._visible_pages() == expected
        center = top + view.viewport().height() // 2
        expected_page = next(
            (page for page, position in enumerate(view._page_tops)
             if position <= center < position + view._page_pix[page][1] + view.PAGE_SPACING),
            None,
        )
        if expected_page is None:
            expected_page = min(range(3000), key=lambda page: abs(view._page_tops[page] - top))
        assert view.current_page() == expected_page
        for y in (0, 100, view.viewport().height() - 1):
            point = QPoint(view.viewport().width() // 2, y)
            found, _ = view._pos_to_page(point)
            expected_hit = next(
                (page for page, position in enumerate(view._page_tops)
                 if position <= top + y <= position + view._page_pix[page][1]), None,
            )
            assert found == expected_hit


def test_large_resize_is_coalesced_and_preserves_page_coordinate(view, qapp, monkeypatch):
    view.jump_to_page(1500)
    view.verticalScrollBar().setValue(view._page_tops[1500] + 180)
    before = (view.verticalScrollBar().value() - view._page_tops[1500]) / view._scale
    calls = []
    relayout = view._relayout

    def recorded():
        calls.append(True)
        relayout()

    monkeypatch.setattr(view, "_relayout", recorded)
    for width in (910, 920, 930, 940, 950):
        view.resize(width, 700)
    assert not calls
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        qapp.processEvents()
    assert len(calls) == 1
    after = (view.verticalScrollBar().value() - view._page_tops[1500]) / view._scale
    assert after == pytest.approx(before, abs=1 / view._scale)
