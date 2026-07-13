import json

from app import doc_tags
from app.annotations import AnnotationStore, Annotation, DocumentAnnotations
from app.pdf_notes import PdfNoteStore


def test_markdown_round_trip(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# hello", encoding="utf-8")

    assert doc_tags.read_doc_tags(md) == []
    doc_tags.write_doc_tags(md, ["PD", "待讀"])
    assert doc_tags.read_doc_tags(md) == ["PD", "待讀"]

    # persisted through AnnotationStore sidecar
    assert AnnotationStore.load(md).doc_tags == ["PD", "待讀"]


def test_markdown_write_preserves_annotations(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# hello", encoding="utf-8")
    doc = DocumentAnnotations(
        doc_tags=["old"], annotations=[Annotation.new(exact="quote", tags=["重要"])]
    )
    AnnotationStore.save(md, doc)

    doc_tags.write_doc_tags(md, ["new"])
    reloaded = AnnotationStore.load(md)
    assert reloaded.doc_tags == ["new"]
    # annotations untouched
    assert len(reloaded.annotations) == 1
    assert reloaded.annotations[0].exact == "quote"


def test_pdf_round_trip(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    assert doc_tags.read_doc_tags(pdf) == []
    doc_tags.write_doc_tags(pdf, ["PD", "待讀"])
    assert doc_tags.read_doc_tags(pdf) == ["PD", "待讀"]

    # persisted through PdfNoteStore sidecar
    assert PdfNoteStore.load_doc_tags(pdf) == ["PD", "待讀"]


def test_dedupe_and_strip_empties(tmp_path):
    md = tmp_path / "note.md"
    md.write_text("# hi", encoding="utf-8")
    doc_tags.write_doc_tags(md, ["a", " a ", "", "  ", "b", "a"])
    # order preserved, deduped, empties/whitespace stripped
    assert doc_tags.read_doc_tags(md) == ["a", "b"]


def test_unknown_type_is_noop(tmp_path):
    other = tmp_path / "data.txt"
    other.write_text("plain", encoding="utf-8")

    assert doc_tags.read_doc_tags(other) == []
    # write must not raise and must not create a sidecar
    doc_tags.write_doc_tags(other, ["x", "y"])
    assert doc_tags.read_doc_tags(other) == []
    assert not (tmp_path / "data.txt.notes.json").exists()


def test_markdown_extension_variant(tmp_path):
    md = tmp_path / "note.markdown"
    md.write_text("# hi", encoding="utf-8")
    doc_tags.write_doc_tags(md, ["z"])
    assert doc_tags.read_doc_tags(md) == ["z"]
