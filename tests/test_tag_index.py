from app.annotations import Annotation, DocumentAnnotations
from app.tag_index import TagIndex


def _doc(doc_tags, annot_tags):
    anns = [Annotation.new(exact="x", tags=annot_tags)] if annot_tags else []
    return DocumentAnnotations(doc_tags=doc_tags, annotations=anns)


def test_update_and_query(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    idx.update(tmp_path / "a.md", _doc(["PD"], ["重要"]))
    idx.update(tmp_path / "b.md", _doc(["PD", "待讀"], []))
    assert "PD" in idx.all_tags()
    assert "重要" in idx.all_tags()
    pd_files = idx.files_with_tag("PD")
    assert len(pd_files) == 2


def test_empty_doc_removes_entry(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    md = tmp_path / "a.md"
    idx.update(md, _doc(["PD"], []))
    idx.update(md, _doc([], []))
    assert idx.all_tags() == []


def test_persists_across_instances(tmp_path):
    p = tmp_path / "idx.json"
    TagIndex(path=p).update(tmp_path / "a.md", _doc(["PD"], []))
    assert "PD" in TagIndex(path=p).all_tags()


def test_prune_removes_missing_files(tmp_path):
    idx = TagIndex(path=tmp_path / "idx.json")
    idx.update(tmp_path / "ghost.md", _doc(["PD"], []))
    idx.prune()
    assert idx.all_tags() == []
