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

    class _Win:
        _current_kind = "pdf"
        _current_file = other_pdf
        _pdf_view = _FakePdfViewForWindowGuard(generation=2)
        _pdf_embedded_annotations = None
        refreshed = False

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

    win = type("W", (), {"_pdf_view": _View()})()
    entry = EmbeddedAnnotation(page=3, kind="Highlight", rect=(10.0, 20.0, 100.0, 15.0))
    window_mod.MainWindow._pdf_embedded_annotation_activated(win, entry)
    assert calls == [("reveal", 3, 10.0, 20.0, 100.0, 15.0)]


def test_window_activation_falls_back_to_jump_when_rect_is_empty():
    calls = []

    class _View:
        def reveal(self, *args):
            calls.append(("reveal",) + args)

        def jump_to_page(self, page):
            calls.append(("jump", page))

    win = type("W", (), {"_pdf_view": _View()})()
    entry = EmbeddedAnnotation(page=5, kind="Text", rect=(0.0, 0.0, 0.0, 0.0))
    window_mod.MainWindow._pdf_embedded_annotation_activated(win, entry)
    assert calls == [("jump", 5)]


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
