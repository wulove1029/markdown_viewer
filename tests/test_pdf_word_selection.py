"""Double-click selects a word, triple-click selects a line, in the PDF view.

Click points come from PyMuPDF's own word rectangles rather than being guessed
from font metrics, so a layout change in a future PyMuPDF cannot silently make
the tests click on empty space and still "pass".
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtPdf import QPdfDocument

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_view import PdfView, _is_word_char

LINE_ONE = "Hello wonderful world"
LINE_TWO = "Second line of text"
LINE_CJK = "這是一段中文測試內容"


@pytest.fixture(scope="module")
def sample_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "words.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=420, height=320)
    page.insert_text((50, 80), LINE_ONE, fontsize=18)
    page.insert_text((50, 120), LINE_TWO, fontsize=18)
    page.insert_text((50, 170), LINE_CJK, fontname="china-t", fontsize=18)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture(scope="module")
def word_rects(sample_pdf):
    with pymupdf.open(str(sample_pdf)) as doc:
        words = doc[0].get_text("words")
    return {w[4]: (w[0], w[1], w[2], w[3]) for w in words}


@pytest.fixture
def view(qapp, sample_pdf):
    view = PdfView()
    view.resize(900, 700)
    view.load(str(sample_pdf))
    deadline = time.time() + 15
    while view._doc.status() != QPdfDocument.Status.Ready and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert view._doc.status() == QPdfDocument.Status.Ready, "PDF never loaded"
    qapp.processEvents()
    return view


def center_of(word_rects, word) -> QPointF:
    x0, y0, x1, y1 = word_rects[word]
    return QPointF((x0 + x1) / 2, (y0 + y1) / 2)


# ── word-character classification ───────────────────────────────────────

@pytest.mark.parametrize("ch", ["a", "Z", "7", "_", "中", "あ"])
def test_word_characters(ch):
    assert _is_word_char(ch)


@pytest.mark.parametrize("ch", [" ", ".", ",", "\r", "\n", "-", "。"])
def test_non_word_characters(ch):
    assert not _is_word_char(ch)


# ── page text ───────────────────────────────────────────────────────────

def test_page_text_matches_the_document(view):
    text = view._page_text(0)
    assert LINE_ONE in text
    assert LINE_TWO in text


def test_page_text_is_cached(view):
    first = view._page_text(0)
    assert 0 in view._page_texts
    assert view._page_text(0) is first


# ── word selection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["Hello", "wonderful", "world", "Second", "text"])
def test_double_click_selects_the_whole_word(view, word_rects, word):
    assert view.select_word_at(0, center_of(word_rects, word))
    assert view.selected_text() == word


def test_double_click_selects_a_cjk_run_not_one_glyph(view, word_rects):
    cjk = next(
        w for w in word_rects if any("㐀" <= c <= "鿿" for c in w)
    )
    assert view.select_word_at(0, center_of(word_rects, cjk))
    selected = view.selected_text()
    assert len(selected) > 1, "a CJK run should not select a single character"
    assert selected in LINE_CJK


def test_click_just_past_a_word_snaps_to_a_neighbour(view, word_rects):
    """Being forgiving near a word is fine; reaching distant text is not."""
    x0, y0, x1, y1 = word_rects["Hello"]
    view.select_word_at(0, QPointF(x1 + 2, (y0 + y1) / 2))
    assert view.selected_text() in ("", "Hello", "wonderful")


def test_click_in_the_empty_margin_selects_nothing(view, word_rects):
    _, y0, _, y1 = word_rects["Hello"]
    assert view.select_word_at(0, QPointF(390, (y0 + y1) / 2)) is False
    assert view.selected_text() == ""


def test_click_in_blank_space_selects_nothing(view):
    assert view.select_word_at(0, QPointF(200, 260)) is False
    assert view.selected_text() == ""


# ── line selection ──────────────────────────────────────────────────────

def test_triple_click_selects_the_whole_line(view, word_rects):
    assert view.select_line_at(0, center_of(word_rects, "wonderful"))
    assert view.selected_text() == LINE_ONE


def test_triple_click_does_not_cross_the_line_break(view, word_rects):
    assert view.select_line_at(0, center_of(word_rects, "line"))
    assert view.selected_text() == LINE_TWO


# ── event plumbing ──────────────────────────────────────────────────────

def _viewport_pos(view, page, pdf_pt: QPointF) -> QPoint:
    scale = view._scale or 1.0
    x = pdf_pt.x() * scale + view._page_lefts[page] - view.horizontalScrollBar().value()
    y = pdf_pt.y() * scale + view._page_tops[page] - view.verticalScrollBar().value()
    return QPoint(round(x), round(y))


def _send(qapp, view, kind, pos: QPoint):
    event = QMouseEvent(
        kind,
        QPointF(pos),
        view.viewport().mapToGlobal(QPointF(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(view.viewport(), event)


def test_real_double_click_event_selects_the_word(qapp, view, word_rects):
    pos = _viewport_pos(view, 0, center_of(word_rects, "wonderful"))
    emitted = []
    view.selection_changed.connect(emitted.append)

    _send(qapp, view, QMouseEvent.Type.MouseButtonPress, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonDblClick, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, pos)
    qapp.processEvents()

    assert view.selected_text() == "wonderful"
    assert emitted[-1] is True, "the toolbar needs to hear about the selection"


def test_press_after_a_double_click_extends_to_the_line(qapp, view, word_rects):
    pos = _viewport_pos(view, 0, center_of(word_rects, "wonderful"))
    _send(qapp, view, QMouseEvent.Type.MouseButtonPress, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonDblClick, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, pos)
    qapp.processEvents()

    _send(qapp, view, QMouseEvent.Type.MouseButtonPress, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, pos)
    qapp.processEvents()

    assert view.selected_text() == LINE_ONE


def test_a_later_press_starts_a_fresh_drag_not_a_triple_click(qapp, view, word_rects):
    """The triple-click window must expire, or normal clicks stop working."""
    pos = _viewport_pos(view, 0, center_of(word_rects, "wonderful"))
    _send(qapp, view, QMouseEvent.Type.MouseButtonDblClick, pos)
    qapp.processEvents()
    assert view.selected_text() == "wonderful"

    view._last_dbl_ms = view._click_clock.elapsed() - 100_000  # long ago
    assert view._is_triple_click(pos) is False


def test_double_click_leaves_no_drag_anchor(qapp, view, word_rects):
    """A stray mouse-move must not collapse the word selection."""
    pos = _viewport_pos(view, 0, center_of(word_rects, "wonderful"))
    _send(qapp, view, QMouseEvent.Type.MouseButtonPress, pos)
    _send(qapp, view, QMouseEvent.Type.MouseButtonDblClick, pos)
    qapp.processEvents()

    assert view._dragging is False
    assert view._sel_start is None

    _send(qapp, view, QMouseEvent.Type.MouseMove, QPoint(pos.x() + 30, pos.y()))
    qapp.processEvents()
    assert view.selected_text() == "wonderful"


def test_drag_selection_still_works(qapp, view, word_rects):
    hx0, hy0, _, hy1 = word_rects["Hello"]
    _, wy0, wx1, wy1 = word_rects["world"]
    start = _viewport_pos(view, 0, QPointF(hx0 + 1, (hy0 + hy1) / 2))
    end = _viewport_pos(view, 0, QPointF(wx1 - 1, (wy0 + wy1) / 2))

    _send(qapp, view, QMouseEvent.Type.MouseButtonPress, start)
    _send(qapp, view, QMouseEvent.Type.MouseMove, end)
    _send(qapp, view, QMouseEvent.Type.MouseButtonRelease, end)
    qapp.processEvents()

    assert "wonderful" in view.selected_text()
