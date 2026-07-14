"""Tests for the PDF page-notes data model and sidecar persistence."""

import os

import pytest

from app.pdf_notes import PdfNote, PdfNoteStore

_FILE_ATTRIBUTE_HIDDEN = 0x2
_windows_only = pytest.mark.skipif(
    os.name != "nt", reason="hidden attribute is Windows-only"
)


def test_sidecar_path_is_next_to_pdf(tmp_path):
    pdf = tmp_path / "book.pdf"
    assert PdfNoteStore.sidecar_path(pdf) == tmp_path / "book.pdf.notes.json"


def test_save_and_load_roundtrip(tmp_path):
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    notes = [
        PdfNote.new(page=2, note="intro thoughts", tags=["idea"]),
        PdfNote.new(page=0, note="cover"),
    ]
    PdfNoteStore.save(pdf, notes)

    loaded = PdfNoteStore.load(pdf)
    # sorted by page
    assert [n.page for n in loaded] == [0, 2]
    assert loaded[0].note == "cover"
    assert loaded[1].tags == ["idea"]


def test_load_missing_returns_empty(tmp_path):
    assert PdfNoteStore.load(tmp_path / "nope.pdf") == []


def test_corrupt_sidecar_is_backed_up(tmp_path):
    pdf = tmp_path / "book.pdf"
    sidecar = PdfNoteStore.sidecar_path(pdf)
    sidecar.write_text("{ not valid json", encoding="utf-8")
    assert PdfNoteStore.load(pdf) == []
    # corrupt file preserved as .bak rather than silently lost
    assert sidecar.with_suffix(sidecar.suffix + ".bak").exists()


def test_markdown_and_pdf_sidecars_do_not_collide(tmp_path):
    # a.md -> a.md.notes.json ; a.pdf -> a.pdf.notes.json
    assert PdfNoteStore.sidecar_path(tmp_path / "a.pdf").name == "a.pdf.notes.json"


def test_empty_save_creates_no_sidecar(tmp_path):
    pdf = tmp_path / "book.pdf"
    PdfNoteStore.save(pdf, [], doc_tags=[])
    assert not PdfNoteStore.sidecar_path(pdf).exists()


def test_empty_save_removes_existing_sidecar(tmp_path):
    pdf = tmp_path / "book.pdf"
    PdfNoteStore.save(pdf, [PdfNote.new(page=1, note="hi")])
    assert PdfNoteStore.sidecar_path(pdf).exists()
    # Clearing both notes and doc-tags removes the now-empty sidecar.
    PdfNoteStore.save(pdf, [], doc_tags=[])
    assert not PdfNoteStore.sidecar_path(pdf).exists()


def test_notes_only_save_preserves_doc_tags_and_keeps_file(tmp_path):
    pdf = tmp_path / "book.pdf"
    PdfNoteStore.save_doc_tags(pdf, ["kept"])
    # Saving notes with default doc_tags must NOT delete the sidecar: the
    # existing doc-tags are preserved, so the file stays non-empty.
    PdfNoteStore.save(pdf, [PdfNote.new(page=0, note="c")])
    assert PdfNoteStore.sidecar_path(pdf).exists()
    assert PdfNoteStore.load_doc_tags(pdf) == ["kept"]


@_windows_only
def test_saved_sidecar_is_hidden(tmp_path):
    pdf = tmp_path / "book.pdf"
    PdfNoteStore.save(pdf, [PdfNote.new(page=1, note="hi")])
    side = PdfNoteStore.sidecar_path(pdf)
    assert os.stat(side).st_file_attributes & _FILE_ATTRIBUTE_HIDDEN
