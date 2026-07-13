import json

from app.pdf_notes import PdfNote, PdfNoteStore


def _sidecar(pdf):
    return pdf.with_name(pdf.name + ".notes.json")


def test_save_doc_tags_persists_in_sidecar(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF dummy")

    PdfNoteStore.save_doc_tags(pdf, ["PD", "待讀"])

    raw = json.loads(_sidecar(pdf).read_text(encoding="utf-8"))
    assert raw["doc_tags"] == ["PD", "待讀"]
    assert raw["notes"] == []
    assert PdfNoteStore.load_doc_tags(pdf) == ["PD", "待讀"]


def test_doc_tags_and_notes_coexist(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF dummy")

    # save notes first, then doc_tags: neither should clobber the other
    PdfNoteStore.save(pdf, [PdfNote.new(page=1, note="hi")])
    PdfNoteStore.save_doc_tags(pdf, ["tagged"])

    assert PdfNoteStore.load_doc_tags(pdf) == ["tagged"]
    notes = PdfNoteStore.load(pdf)
    assert len(notes) == 1
    assert notes[0].note == "hi"

    # saving notes again with default doc_tags preserves existing doc_tags
    PdfNoteStore.save(pdf, PdfNoteStore.load(pdf))
    assert PdfNoteStore.load_doc_tags(pdf) == ["tagged"]


def test_backward_compat_sidecar_without_doc_tags_key(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF dummy")
    # legacy sidecar: no doc_tags key at all
    _sidecar(pdf).write_text(
        json.dumps({"schema": 1, "notes": []}), encoding="utf-8"
    )

    assert PdfNoteStore.load_doc_tags(pdf) == []


def test_load_doc_tags_missing_sidecar(tmp_path):
    pdf = tmp_path / "nope.pdf"
    assert PdfNoteStore.load_doc_tags(pdf) == []


def test_save_doc_tags_reloads_across_calls(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF dummy")
    PdfNoteStore.save_doc_tags(pdf, ["one"])
    PdfNoteStore.save_doc_tags(pdf, ["one", "two"])
    assert PdfNoteStore.load_doc_tags(pdf) == ["one", "two"]
