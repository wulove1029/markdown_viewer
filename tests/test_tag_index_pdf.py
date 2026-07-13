"""PDF paths with doc_tags participate in the type-neutral TagIndex."""

from app.annotations import DocumentAnnotations
from app.tag_index import TagIndex


def _doc(doc_tags):
    return DocumentAnnotations(doc_tags=list(doc_tags), annotations=[])


def test_pdf_and_md_mixed_filtering(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    md = tmp_path / "note.md"
    pdf = tmp_path / "paper.pdf"

    idx.update(md, _doc(["共享"]))
    idx.update(pdf, _doc(["共享", "只PDF"]))

    assert "共享" in idx.all_tags()
    assert "只PDF" in idx.all_tags()

    shared = idx.files_with_tag("共享")
    assert len(shared) == 2
    assert str(md.resolve()) in shared
    assert str(pdf.resolve()) in shared

    pdf_only = idx.files_with_tag("只PDF")
    assert pdf_only == [str(pdf.resolve())]


def test_pdf_tags_for_accessor(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    pdf = tmp_path / "paper.pdf"
    idx.update(pdf, _doc(["a", "b"]))
    assert idx.tags_for(pdf) == {"a", "b"}
    assert idx.tags_for(tmp_path / "unknown.pdf") == set()


def test_pdf_entry_persists_across_instances(tmp_path):
    p = tmp_path / "idx.json"
    pdf = tmp_path / "paper.pdf"
    TagIndex(path=p).update(pdf, _doc(["persisted"]))
    assert "persisted" in TagIndex(path=p).all_tags()


def test_clearing_pdf_doc_tags_removes_entry(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    pdf = tmp_path / "paper.pdf"
    idx.update(pdf, _doc(["temp"]))
    idx.update(pdf, _doc([]))
    assert idx.all_tags() == []
    assert idx.files_with_tag("temp") == []
