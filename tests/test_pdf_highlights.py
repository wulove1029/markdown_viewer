"""Tests for PDF text-highlight persistence and the in-canvas selection path."""

import time

import pytest

from app.pdf_highlights import PdfHighlight, PdfHighlightStore, Rect


# --------------------------- persistence ---------------------------
def test_sidecar_path_is_next_to_pdf(tmp_path):
    pdf = tmp_path / "book.pdf"
    assert PdfHighlightStore.sidecar_path(pdf) == tmp_path / "book.pdf.highlights.json"


def test_does_not_collide_with_notes_sidecar(tmp_path):
    # notes use .notes.json; highlights must use a distinct suffix.
    assert PdfHighlightStore.sidecar_path(tmp_path / "a.pdf").name == "a.pdf.highlights.json"


def test_save_and_load_roundtrip(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    highlights = [
        PdfHighlight.new(
            page=2,
            rects=[Rect(72, 110.5, 240, 14), Rect(72, 126, 180, 14)],
            text="selected passage",
            color="#a5d6a7",
            note="recall this",
            tags=["重要"],
        ),
        PdfHighlight.new(page=0, rects=[Rect(10, 10, 20, 8)], text="cover"),
    ]
    PdfHighlightStore.save(pdf, highlights)

    loaded = PdfHighlightStore.load(pdf)
    assert [h.page for h in loaded] == [0, 2]  # sorted by page
    assert loaded[0].text == "cover"
    passage = loaded[1]
    assert passage.color == "#a5d6a7"
    assert passage.note == "recall this"
    assert passage.tags == ["重要"]
    assert len(passage.rects) == 2
    assert passage.rects[0].w == 240
    assert isinstance(passage.rects[0], Rect)


def test_load_missing_returns_empty(tmp_path):
    assert PdfHighlightStore.load(tmp_path / "nope.pdf") == []


def test_corrupt_sidecar_is_backed_up(tmp_path):
    pdf = tmp_path / "book.pdf"
    sidecar = PdfHighlightStore.sidecar_path(pdf)
    sidecar.write_text("{ not valid json", encoding="utf-8")
    assert PdfHighlightStore.load(pdf) == []
    assert sidecar.with_suffix(sidecar.suffix + ".bak").exists()


def test_from_dict_tolerates_missing_keys():
    hl = PdfHighlight.from_dict({"page": 5})
    assert hl.page == 5 and hl.rects == [] and hl.color == "#ffd54f" and hl.id


# ----------------------- in-canvas selection -----------------------
pymupdf = pytest.importorskip("pymupdf")


def _make_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "alpha beta gamma delta", fontsize=20)
    page.insert_text((72, 200), "second line of words", fontsize=20)
    doc.save(str(path))
    doc.close()


def _wait_ready(view, qapp):
    from PyQt6.QtPdf import QPdfDocument

    for _ in range(300):
        if view._doc.status() == QPdfDocument.Status.Ready and view._page_tops:
            return True
        qapp.processEvents()
        time.sleep(0.005)
    return False


def test_drag_selection_emits_highlight(qapp, tmp_path):
    from PyQt6.QtCore import QEvent, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    from app.pdf_view import PdfView

    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)

    view = PdfView()
    view.resize(800, 1100)
    view.show()
    qapp.processEvents()
    view.load(pdf)
    assert _wait_ready(view, qapp), "PDF never reached Ready"

    assert view.page_count() == 1
    assert view._scale > 0 and view.viewport().width() > 0

    # Map two known PDF points on the first text line into viewport pixels.
    def to_viewport(pdf_x, pdf_y):
        s = view._scale
        cx = view._page_lefts[0] + pdf_x * s - view.horizontalScrollBar().value()
        cy = view._page_tops[0] + pdf_y * s - view.verticalScrollBar().value()
        return QPointF(cx, cy)

    start = to_viewport(74, 92)
    end = to_viewport(250, 92)

    captured = []
    view.highlight_requested.connect(lambda payload: captured.append(payload))
    view.set_pen_mode(True)

    def mouse(kind, pos, button, buttons):
        return QMouseEvent(kind, pos, pos, button, buttons, Qt.KeyboardModifier.NoModifier)

    view.mousePressEvent(
        mouse(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    )
    view.mouseMoveEvent(
        mouse(QEvent.Type.MouseMove, end, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    )
    # selection should now hold the dragged text
    assert view.has_selection()
    assert "alpha" in view._selection.text().lower()

    view.mouseReleaseEvent(
        mouse(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    )

    # pen mode -> release emits a highlight payload with page 0 + geometry + text
    assert len(captured) == 1
    payload = captured[0]
    assert payload["page"] == 0
    assert payload["rects"] and len(payload["rects"][0]) == 4
    assert "alpha" in payload["text"].lower()
    assert payload["color"] == view.pen_color()
