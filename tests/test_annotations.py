import json
from pathlib import Path

from app.annotations import Annotation, AnnotationStore, DocumentAnnotations


def test_sidecar_path_appends_notes_json():
    assert AnnotationStore.sidecar_path("a/b/foo.md") == Path("a/b/foo.md.notes.json")


def test_save_then_load_roundtrip(tmp_path):
    md = tmp_path / "doc.md"
    md.write_text("# hi", encoding="utf-8")
    ann = Annotation.new(exact="hello", prefix="say ", suffix=" world",
                         textPosition=4, color="#ffd54f", note="n", tags=["重要"])
    doc = DocumentAnnotations(doc_tags=["待讀"], annotations=[ann])
    AnnotationStore.save(md, doc)

    loaded = AnnotationStore.load(md)
    assert loaded.doc_tags == ["待讀"]
    assert len(loaded.annotations) == 1
    a = loaded.annotations[0]
    assert a.id == ann.id
    assert a.exact == "hello"
    assert a.tags == ["重要"]


def test_load_missing_returns_empty(tmp_path):
    doc = AnnotationStore.load(tmp_path / "nope.md")
    assert doc.doc_tags == [] and doc.annotations == []


def test_corrupt_sidecar_is_backed_up(tmp_path):
    md = tmp_path / "doc.md"
    side = AnnotationStore.sidecar_path(md)
    side.write_text("{not json", encoding="utf-8")
    doc = AnnotationStore.load(md)
    assert doc.annotations == []
    assert side.with_suffix(side.suffix + ".bak").exists()


def test_save_is_atomic_and_utf8(tmp_path):
    md = tmp_path / "doc.md"
    AnnotationStore.save(md, DocumentAnnotations(doc_tags=["中文"]))
    raw = json.loads(AnnotationStore.sidecar_path(md).read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert raw["doc_tags"] == ["中文"]
