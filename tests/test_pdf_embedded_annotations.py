"""Tests for reading Adobe-style annotations embedded inside a PDF.

Covers the pure data layer (``app.pdf_embedded_annotations``), the background
task's generation/staleness guard in ``PdfView``, and the read-only sidebar
panel's display + click-to-jump wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_embedded_annotations import (
    EmbeddedAnnotation,
    extract_embedded_annotations,
)
from app.pdf_embedded_annotations_panel import PdfEmbeddedAnnotationsPanel
from app.pdf_view import PdfView
import app.window as window_mod


# --------------------------------------------------------------------------
# Fixture PDFs
# --------------------------------------------------------------------------

def _make_annotated_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Hello World Highlight Test")

    text_annot = page.add_text_annot((400, 50), "A sticky note")
    text_annot.set_info(title="Alice")
    text_annot.update()

    highlight = page.add_highlight_annot(pymupdf.Rect(50, 45, 250, 60))
    highlight.set_info(title="Bob")
    highlight.set_colors(stroke=(1, 1, 0))
    highlight.update()

    free_text = page.add_freetext_annot(
        pymupdf.Rect(50, 100, 250, 130), "A free text comment", fontsize=12
    )
    free_text.set_info(title="Carol")
    free_text.update()

    doc.save(str(path))
    doc.close()


def _make_empty_content_highlight_pdf(path: Path) -> None:
    """A Highlight annotation with no note text, over real page text."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Underlying highlighted text")
    highlight = page.add_highlight_annot(pymupdf.Rect(48, 40, 260, 62))
    highlight.update()
    doc.save(str(path))
    doc.close()


def _make_encrypted_pdf(path: Path, password: str = "secret123") -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Encrypted content")
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=password,
        owner_pw="owner-" + password,
        permissions=int(pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT),
    )
    doc.close()


# --------------------------------------------------------------------------
# Data-layer extraction
# --------------------------------------------------------------------------

def test_extracts_text_highlight_freetext_with_author_and_page(tmp_path):
    path = tmp_path / "annotated.pdf"
    _make_annotated_pdf(path)

    entries = extract_embedded_annotations(path)
    by_kind = {e.kind: e for e in entries}

    assert set(by_kind) == {"Text", "Highlight", "FreeText"}

    text = by_kind["Text"]
    assert text.page == 0
    assert text.author == "Alice"
    assert text.content == "A sticky note"

    highlight = by_kind["Highlight"]
    assert highlight.page == 0
    assert highlight.author == "Bob"
    assert highlight.color == "#ffff00"

    free_text = by_kind["FreeText"]
    assert free_text.page == 0
    assert free_text.author == "Carol"
    assert free_text.content == "A free text comment"


def test_highlight_with_no_note_pulls_underlying_text(tmp_path):
    path = tmp_path / "empty_highlight.pdf"
    _make_empty_content_highlight_pdf(path)

    entries = extract_embedded_annotations(path)
    assert len(entries) == 1
    assert entries[0].kind == "Highlight"
    assert "Underlying highlighted text" in entries[0].content


def test_popup_is_not_double_listed(tmp_path):
    """A Text annotation's auto-created Popup must never appear as its own entry."""
    path = tmp_path / "popup.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    annot = page.add_text_annot((50, 50), "Parented note content")
    annot.set_info(title="Dave")
    assert annot.has_popup
    annot.update()
    doc.save(str(path))
    doc.close()

    entries = extract_embedded_annotations(path)
    matches = [e for e in entries if e.content == "Parented note content"]
    assert len(matches) == 1
    assert matches[0].kind == "Text"
    assert matches[0].author == "Dave"
    assert all(e.kind != "Popup" for e in entries)


def test_multi_page_annotations_report_correct_page_numbers(tmp_path):
    path = tmp_path / "multipage.pdf"
    doc = pymupdf.open()
    doc.new_page()
    page2 = doc.new_page()
    a = page2.add_text_annot((10, 10), "on page two")
    a.update()
    doc.save(str(path))
    doc.close()

    entries = extract_embedded_annotations(path)
    assert len(entries) == 1
    assert entries[0].page == 1


def test_encrypted_pdf_without_password_is_empty_not_a_crash(tmp_path):
    path = tmp_path / "enc.pdf"
    _make_encrypted_pdf(path)
    assert extract_embedded_annotations(path) == []
    assert extract_embedded_annotations(path, password="wrong") == []


def test_encrypted_pdf_with_correct_password_extracts(tmp_path):
    path = tmp_path / "enc2.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    a = page.add_text_annot((10, 10), "secret note")
    a.update()
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="pw123",
        owner_pw="owner-pw123",
        permissions=int(pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT),
    )
    doc.close()

    entries = extract_embedded_annotations(path, password="pw123")
    assert len(entries) == 1
    assert entries[0].content == "secret note"


def test_corrupted_file_is_empty_not_a_crash(tmp_path):
    bad = tmp_path / "corrupt.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a real pdf body")
    assert extract_embedded_annotations(bad) == []


def test_non_pdf_file_is_empty_not_a_crash(tmp_path):
    not_pdf = tmp_path / "notes.txt"
    not_pdf.write_text("just some text", encoding="utf-8")
    assert extract_embedded_annotations(not_pdf) == []


def test_missing_file_is_empty_not_a_crash(tmp_path):
    assert extract_embedded_annotations(tmp_path / "does_not_exist.pdf") == []


def test_none_path_is_empty():
    assert extract_embedded_annotations(None) == []


def test_pymupdf_unavailable_degrades_to_empty(tmp_path, monkeypatch):
    import app.pdf_embedded_annotations as mod

    monkeypatch.setattr(mod, "_pymupdf_module", None)
    path = tmp_path / "whatever.pdf"
    path.write_bytes(b"%PDF-1.4")
    assert mod.extract_embedded_annotations(path) == []


# --------------------------------------------------------------------------
# Background task: generation / staleness guard on PdfView
# --------------------------------------------------------------------------

@pytest.fixture
def annotated_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    _make_annotated_pdf(path)
    return path


@pytest.fixture
def other_pdf(tmp_path):
    path = tmp_path / "other.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    a = page.add_text_annot((10, 10), "other doc note")
    a.update()
    doc.save(str(path))
    doc.close()
    return path


def test_embedded_annotations_ready_emits_for_current_document(qapp, annotated_pdf):
    view = PdfView()
    received = []
    view.embedded_annotations_ready.connect(
        lambda gen, path, entries: received.append((gen, Path(path), entries))
    )
    try:
        assert view.load(annotated_pdf) is True
        assert view.request_embedded_annotations() is True
        view._embedded_annotations_pool.waitForDone(5000)
        for _ in range(200):
            qapp.processEvents()
            if received:
                break
        assert len(received) == 1
        gen, path, entries = received[0]
        assert gen == view.load_generation()
        assert path == annotated_pdf
        assert {e.kind for e in entries} == {"Text", "Highlight", "FreeText"}
    finally:
        view.close()


def test_stale_embedded_annotations_result_is_discarded_after_switching_docs(
    qapp, annotated_pdf, other_pdf
):
    """Simulate a slow background read finishing after the user has already
    switched documents: the emitted signal must belong to the *old*
    generation/path and the view/window layer must be able to reject it.
    """
    view = PdfView()
    received = []
    view.embedded_annotations_ready.connect(
        lambda gen, path, entries: received.append((gen, Path(path), entries))
    )
    try:
        assert view.load(annotated_pdf) is True
        first_generation = view.load_generation()

        # Manually finish a task for the first generation *after* switching
        # documents, exactly like a slow worker thread arriving late.
        assert view.load(other_pdf) is True
        assert first_generation != view.load_generation()

        view._on_embedded_annotations_finished(
            first_generation,
            annotated_pdf,
            [EmbeddedAnnotation(page=0, kind="Text", content="stale")],
        )
        assert received == []  # stale result for a since-replaced document

        # A result for the *current* generation/path still gets through.
        assert view.request_embedded_annotations() is True
        view._embedded_annotations_pool.waitForDone(5000)
        for _ in range(200):
            qapp.processEvents()
            if received:
                break
        assert len(received) == 1
        gen, path, entries = received[0]
        assert gen == view.load_generation()
        assert path == other_pdf
        assert entries[0].content == "other doc note"
    finally:
        view.close()


def test_request_embedded_annotations_is_at_most_one_per_generation(qapp, annotated_pdf):
    view = PdfView()
    started = []

    class _Pool:
        def start(self, task):
            started.append(task)

    view._embedded_annotations_pool = _Pool()
    try:
        assert view.load(annotated_pdf) is True
        assert view.request_embedded_annotations() is True
        assert view.request_embedded_annotations() is False
        assert len(started) == 1
    finally:
        view._embedded_annotations_tasks.clear()
        view.close()


# --------------------------------------------------------------------------
# Window-level staleness guard (mirrors _on_pdf_outline_ready's checks)
# --------------------------------------------------------------------------

class _FakePdfViewForWindowGuard:
    def __init__(self, generation):
        self._generation = generation

    def load_generation(self):
        return self._generation


def test_window_discards_stale_embedded_annotations_result(annotated_pdf, other_pdf):
    """``_on_pdf_embedded_annotations_ready`` must ignore a result whose
    generation or path no longer matches the currently-open document, exactly
    like ``_on_pdf_outline_ready`` already does for outlines.
    """

    class _StatusBar:
        def showMessage(self, *_args, **_kwargs):
            pass

    class _Win:
        _current_kind = "pdf"
        _current_file = other_pdf
        _pdf_view = _FakePdfViewForWindowGuard(generation=2)
        _pdf_embedded_annotations = None
        refreshed = False

        def statusBar(self):
            return _StatusBar()

        def _refresh_pdf_embedded_annotations_panel(self):
            self._Win_class_marker = True
            type(self).refreshed = True

    win = _Win()
    window_mod.MainWindow._on_pdf_embedded_annotations_ready(
        win, 1, annotated_pdf, [EmbeddedAnnotation(page=0, kind="Text", content="x")]
    )
    assert win._pdf_embedded_annotations is None
    assert _Win.refreshed is False

    window_mod.MainWindow._on_pdf_embedded_annotations_ready(
        win, 2, other_pdf, [EmbeddedAnnotation(page=0, kind="Text", content="fresh")]
    )
    assert win._pdf_embedded_annotations[0].content == "fresh"
    assert _Win.refreshed is True


# --------------------------------------------------------------------------
# Panel display + click-to-jump
# --------------------------------------------------------------------------

def test_panel_shows_empty_state_message(qapp):
    panel = PdfEmbeddedAnnotationsPanel()
    panel.set_annotations([])
    assert panel._list.count() == 1
    assert panel._list.item(0).text() == "此 PDF 沒有內嵌註解"


def test_panel_lists_entries_with_page_kind_and_content(qapp):
    panel = PdfEmbeddedAnnotationsPanel()
    entries = [
        EmbeddedAnnotation(page=0, kind="Text", author="Alice", content="hello"),
        EmbeddedAnnotation(page=2, kind="Highlight", author="Bob", content="marked text"),
    ]
    panel.set_annotations(entries)
    assert panel._list.count() == 2
    first_text = panel._list.item(0).text()
    assert "p.1" in first_text
    assert "hello" in first_text
    assert "Alice" in first_text
    second_text = panel._list.item(1).text()
    assert "p.3" in second_text
    assert "marked text" in second_text


def test_clicking_a_panel_item_invokes_activated_callback(qapp):
    activated = []
    panel = PdfEmbeddedAnnotationsPanel({"activated": activated.append})
    entry = EmbeddedAnnotation(page=4, kind="FreeText", content="jump target")
    panel.set_annotations([entry])
    panel._on_clicked(panel._list.item(0))
    assert activated == [entry]


def test_window_activation_jumps_to_page_with_rect_when_available():
    calls = []

    class _View:
        def reveal(self, page, x, y, w, h):
            calls.append(("reveal", page, x, y, w, h))

        def jump_to_page(self, page):
            calls.append(("jump", page))

        def flash_embedded_annotation(self, xref):
            calls.append(("flash", xref))

    win = type("W", (), {"_pdf_view": _View()})()
    entry = EmbeddedAnnotation(
        page=3, kind="Highlight", rect=(10.0, 20.0, 100.0, 15.0), xref=42
    )
    window_mod.MainWindow._pdf_embedded_annotation_activated(win, entry)
    assert calls == [("reveal", 3, 10.0, 20.0, 100.0, 15.0), ("flash", 42)]


def test_window_activation_falls_back_to_jump_when_rect_is_empty():
    calls = []

    class _View:
        def reveal(self, *args):
            calls.append(("reveal",) + args)

        def jump_to_page(self, page):
            calls.append(("jump", page))

        def flash_embedded_annotation(self, xref):
            calls.append(("flash", xref))

    win = type("W", (), {"_pdf_view": _View()})()
    entry = EmbeddedAnnotation(page=5, kind="Text", rect=(0.0, 0.0, 0.0, 0.0), xref=7)
    window_mod.MainWindow._pdf_embedded_annotation_activated(win, entry)
    assert calls == [("jump", 5), ("flash", 7)]


# --------------------------------------------------------------------------
# Review follow-ups: non-blocking close, swatch instead of text colour
# --------------------------------------------------------------------------

def test_closing_view_does_not_wait_for_in_flight_scan(qapp, annotated_pdf, monkeypatch):
    """A slow background scan must not stall closing/destroying the view."""
    import time
    import app.pdf_view as pdf_view_mod

    def slow_extract(path, password=""):
        time.sleep(1.5)
        return []

    monkeypatch.setattr(pdf_view_mod, "extract_embedded_annotations", slow_extract)
    view = PdfView()
    assert view.load(annotated_pdf) is True
    assert view.request_embedded_annotations() is True
    started = time.perf_counter()
    view.close()
    view.deleteLater()
    qapp.processEvents()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"close blocked for {elapsed:.2f}s"
    view._embedded_annotations_pool.waitForDone(5000)


def test_panel_shows_colour_as_swatch_not_text_foreground(qapp):
    from app.theme import DARK

    panel = PdfEmbeddedAnnotationsPanel()
    panel.apply_theme(DARK)
    panel.set_annotations(
        [EmbeddedAnnotation(page=0, kind="Highlight", content="x", color="#ffff00")]
    )
    item = panel._list.item(0)
    assert not item.icon().isNull()
    # Foreground was not overridden with the annotation colour.
    assert item.foreground().color().name() != "#ffff00"


# --------------------------------------------------------------------------
# Real Acrobat files: replies (/IRT), rich text, shapes, and on-page painting
# --------------------------------------------------------------------------

from PySide6.QtCore import QPoint, QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402

from app import pdf_annotation_overlay as overlay  # noqa: E402
from app.pdf_embedded_annotations import (  # noqa: E402
    build_annotation_threads,
    kind_label,
    summary_text,
    tooltip_text,
)
from app.pdf_embedded_annotations_panel import parent_label, reply_label  # noqa: E402

# The user's real Acrobat export. Never copied into the repo: the tests that
# need it skip when it is not on this machine.
REAL_ACROBAT_PDF = Path(r"C:\Users\USER01\Desktop\dm00293821.pdf")
requires_real_pdf = pytest.mark.skipif(
    not REAL_ACROBAT_PDF.exists(),
    reason="the real Acrobat sample PDF is not present on this machine",
)


def _make_threaded_pdf(path: Path) -> None:
    """A highlight with a Text *reply* attached through /IRT, as Acrobat writes."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Underlying highlighted text")
    highlight = page.add_highlight_annot(pymupdf.Rect(48, 40, 260, 62))
    highlight.set_info(title="USER01", subject="螢光標示")
    highlight.set_colors(stroke=(1.0, 0.384, 0.0))
    highlight.set_opacity(0.4)
    highlight.update()
    reply = page.add_text_annot((48, 40), "測試用")
    reply.set_info(title="USER01", subject="註解")
    reply.update()
    doc.xref_set_key(reply.xref, "IRT", f"{highlight.xref} 0 R")
    doc.save(str(path))
    doc.close()


def _make_shapes_pdf(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.add_rect_annot(pymupdf.Rect(40, 40, 140, 90)).update()
    page.add_circle_annot(pymupdf.Rect(160, 40, 260, 90)).update()
    page.add_line_annot((40, 120), (240, 160)).update()
    page.add_polygon_annot([(40, 200), (140, 200), (90, 260)]).update()
    page.add_ink_annot([[(40, 300), (80, 320), (120, 300)]]).update()
    doc.save(str(path))
    doc.close()


@pytest.fixture
def threaded_pdf(tmp_path):
    path = tmp_path / "threaded.pdf"
    _make_threaded_pdf(path)
    return path


def test_reply_reports_its_parent_xref_and_never_stands_alone(threaded_pdf):
    entries = extract_embedded_annotations(threaded_pdf)
    parent = next(e for e in entries if e.kind == "Highlight")
    reply = next(e for e in entries if e.kind == "Text")
    assert parent.xref > 0 and reply.xref > 0
    assert parent.in_reply_to is None
    assert reply.in_reply_to == parent.xref
    assert reply.is_reply and not parent.is_reply


def test_threads_group_replies_under_their_parent(threaded_pdf):
    entries = extract_embedded_annotations(threaded_pdf)
    threads = build_annotation_threads(entries)
    assert len(threads) == 1
    parent, replies = threads[0]
    assert parent.kind == "Highlight"
    assert [r.content for r in replies] == ["測試用"]


def test_a_reply_whose_parent_is_missing_is_promoted_to_top_level(tmp_path):
    """A dangling /IRT must not make an annotation disappear from the list."""
    doc = pymupdf.open()
    page = doc.new_page()
    annot = page.add_text_annot((50, 50), "orphan reply")
    annot.update()
    doc.xref_set_key(annot.xref, "IRT", "9999 0 R")
    path = tmp_path / "orphan.pdf"
    doc.save(str(path))
    doc.close()
    entries = extract_embedded_annotations(path)
    assert len(entries) == 1
    assert entries[0].in_reply_to is None
    assert build_annotation_threads(entries)[0][0].content == "orphan reply"


def test_highlight_reports_quads_colour_and_opacity(threaded_pdf):
    entry = next(
        e for e in extract_embedded_annotations(threaded_pdf) if e.kind == "Highlight"
    )
    assert entry.quads and len(entry.quads[0]) == 4
    assert entry.color == "#ff6200"
    assert 0.3 < entry.opacity < 0.5
    assert entry.subject == "螢光標示"


def test_marked_text_is_separate_from_note_text(threaded_pdf):
    entry = next(
        e for e in extract_embedded_annotations(threaded_pdf) if e.kind == "Highlight"
    )
    assert "highlighted text" in entry.marked_text
    # No note of its own, so the marked passage is not misreported as one.
    assert entry.note_text == ""


def test_shape_annotations_expose_vertices_and_ink_paths(tmp_path):
    path = tmp_path / "shapes.pdf"
    _make_shapes_pdf(path)
    by_kind = {e.kind: e for e in extract_embedded_annotations(path)}
    assert {"Square", "Circle", "Line", "Polygon", "Ink"} <= set(by_kind)
    assert len(by_kind["Line"].vertices) >= 2
    assert len(by_kind["Polygon"].vertices) >= 3
    assert by_kind["Ink"].ink and len(by_kind["Ink"].ink[0]) >= 2
    assert by_kind["Square"].rect[2] > 0


def test_rich_text_is_used_when_contents_is_empty(tmp_path):
    """Acrobat sometimes leaves /Contents empty and keeps the text in /RC."""
    doc = pymupdf.open()
    page = doc.new_page()
    annot = page.add_text_annot((50, 50), "")
    annot.update()
    doc.xref_set_key(
        annot.xref,
        "RC",
        "(<body><p><span>rich &amp; only</span></p></body>)",
    )
    path = tmp_path / "rc.pdf"
    doc.save(str(path))
    doc.close()
    entry = extract_embedded_annotations(path)[0]
    assert entry.content == "rich & only"


def test_popup_contents_is_used_as_a_last_resort(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    annot = page.add_text_annot((50, 50), "")
    annot.update()
    popup_xref = doc.get_new_xref()
    doc.update_object(
        popup_xref,
        f"<</Type/Annot/Subtype/Popup/Parent {annot.xref} 0 R"
        "/Rect[0 0 10 10]/Contents(from the popup)>>",
    )
    doc.xref_set_key(annot.xref, "Popup", f"{popup_xref} 0 R")
    path = tmp_path / "popup.pdf"
    doc.save(str(path))
    doc.close()
    entry = extract_embedded_annotations(path)[0]
    assert entry.content == "from the popup"


def test_subject_is_the_display_label_when_there_is_no_note():
    entry = EmbeddedAnnotation(page=0, kind="Highlight", subject="螢光標示")
    assert kind_label(entry) == "螢光標示"
    assert kind_label(EmbeddedAnnotation(page=0, kind="Highlight")) == "螢光標記"


def test_tooltip_lists_marked_text_and_replies():
    parent = EmbeddedAnnotation(
        page=0, kind="Highlight", xref=1, author="USER01",
        marked_text="marked words", content="marked words", subject="螢光標示",
    )
    reply = EmbeddedAnnotation(
        page=0, kind="Text", xref=2, in_reply_to=1, author="USER01", content="測試用"
    )
    text = tooltip_text(parent, [reply])
    assert "第 1 頁" in text and "螢光標示" in text
    assert "marked words" in text
    assert "↳ USER01：測試用" in text


# --------------------------------------------------------------------------
# On-page overlay: replies stay off the page, icons stay a fixed size
# --------------------------------------------------------------------------

def _identity_mapper(scale=1.0):
    def to_screen(x, y, w, h):
        return QRectF(x * scale, y * scale, w * scale, h * scale)
    return to_screen


def _entries_for_overlay():
    parent = EmbeddedAnnotation(
        page=0, kind="Highlight", xref=1, color="#ff6200", opacity=0.4,
        rect=(20.0, 20.0, 100.0, 12.0), quads=[(20.0, 20.0, 100.0, 12.0)],
    )
    reply = EmbeddedAnnotation(
        page=0, kind="Text", xref=2, in_reply_to=1, color="#9643fc",
        rect=(20.0, 20.0, 24.0, 24.0), content="測試用",
    )
    return [parent, reply]


def test_replies_are_not_drawn_on_the_page():
    entries = _entries_for_overlay()
    visible = overlay.visible_annotations(entries, 0)
    assert [e.xref for e in visible] == [1]


def test_sticky_icon_keeps_a_fixed_pixel_size_at_any_zoom():
    note = EmbeddedAnnotation(page=0, kind="Text", rect=(20.0, 20.0, 24.0, 24.0))
    for scale in (0.5, 1.0, 4.0):
        box = overlay.icon_rect(note, _identity_mapper(scale))
        assert box.width() == overlay.ICON_PX
        assert box.height() == overlay.ICON_PX


def test_painting_tints_the_highlight_and_leaves_the_reply_area_clean(qapp):
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    drawn = overlay.paint_embedded_annotations(
        painter, _entries_for_overlay(), 0, _identity_mapper(1.0), 1.0
    )
    painter.end()
    assert drawn == 1  # the reply contributed nothing
    inside = image.pixelColor(60, 25)
    assert inside.red() > inside.blue()  # orange tint over white
    # Below the highlight is where PDFium used to stamp the 24pt reply icon.
    assert image.pixelColor(30, 40).name() == "#ffffff"


def test_annotation_hit_test_finds_the_highlight_not_the_reply():
    entries = _entries_for_overlay()
    hit = overlay.annotation_at(entries, 0, QPoint(60, 25), _identity_mapper(1.0))
    assert hit is not None and hit.xref == 1
    assert overlay.annotation_at(entries, 0, QPoint(5, 5), _identity_mapper(1.0)) is None


def test_view_hides_reply_icons_from_the_page(qapp, threaded_pdf):
    view = PdfView()
    view.resize(600, 800)
    assert view.load(threaded_pdf)
    view.set_embedded_annotations(extract_embedded_annotations(threaded_pdf))
    assert len(view.embedded_annotations()) == 2
    assert len(overlay.visible_annotations(view.embedded_annotations(), 0)) == 1
    view.deleteLater()


# --------------------------------------------------------------------------
# Panel: threaded display + count badge
# --------------------------------------------------------------------------

def test_panel_indents_replies_under_their_parent(qapp, threaded_pdf):
    panel = PdfEmbeddedAnnotationsPanel()
    entries = extract_embedded_annotations(threaded_pdf)
    panel.set_annotations(entries)
    labels = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert len(labels) == 2
    assert labels[0].startswith("p.1")
    assert "「" in labels[0]  # the marked passage is quoted for the parent
    assert labels[1].lstrip().startswith("↳")
    assert "測試用" in labels[1]
    assert panel.count() == 2


def test_parent_label_shows_type_and_marked_text_when_there_is_no_note():
    entry = EmbeddedAnnotation(
        page=0, kind="Highlight", subject="螢光標示",
        marked_text="marked words", content="marked words", author="USER01",
    )
    label = parent_label(entry)
    assert "螢光標示" in label and "marked words" in label and "USER01" in label
    assert "（無文字內容）" not in label


def test_reply_label_falls_back_to_a_placeholder_when_empty():
    assert "（無文字內容）" in reply_label(
        EmbeddedAnnotation(page=0, kind="Text", xref=2, in_reply_to=1)
    )
    assert summary_text(EmbeddedAnnotation(page=0, kind="Text", content=" a  b ")) == "a b"


def test_left_panel_tab_titles_carry_the_annotation_count(qapp):
    from app.left_panel import LeftPanel

    panel = LeftPanel(lambda _p: None, lambda _a: None, {})
    panel.set_embedded_annotation_count(2)
    titles = [panel._tabs.tabText(i) for i in range(panel._tabs.count())]
    assert "標註 (2)" in titles
    panel.set_embedded_annotation_count(0)
    assert "標註" in [panel._tabs.tabText(i) for i in range(panel._tabs.count())]
    panel.deleteLater()


# --------------------------------------------------------------------------
# The real Acrobat file (skipped when absent; never copied into the repo)
# --------------------------------------------------------------------------

@requires_real_pdf
def test_real_acrobat_file_extracts_a_highlight_with_a_text_reply():
    entries = extract_embedded_annotations(REAL_ACROBAT_PDF)
    assert len(entries) == 2
    highlight, reply = entries
    assert highlight.kind == "Highlight" and highlight.page == 0
    assert highlight.color == "#ff6200"
    assert round(highlight.opacity, 1) == 0.4
    assert highlight.subject == "螢光標示"
    # Acrobat's panel shows the type name because the highlight has no note of
    # its own — the only text is the passage it marks.
    assert highlight.note_text == ""
    assert highlight.marked_text == "BlueNRG-2 \u201cover-the-air\u201d (OTA)"
    assert reply.kind == "Text" and reply.content == "測試用"
    assert reply.in_reply_to == highlight.xref


@requires_real_pdf
def test_real_acrobat_file_threads_and_hides_the_reply_icon():
    entries = extract_embedded_annotations(REAL_ACROBAT_PDF)
    threads = build_annotation_threads(entries)
    assert len(threads) == 1
    parent, replies = threads[0]
    assert parent.kind == "Highlight" and [r.content for r in replies] == ["測試用"]
    # The 24pt purple Comment icon PDFium used to stamp over the text is a
    # reply, so nothing of it is drawn on the page.
    assert [e.kind for e in overlay.visible_annotations(entries, 0)] == ["Highlight"]
