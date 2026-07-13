"""Data-plane tests for deleting tags and merging created-but-unassigned tags.

These stay GUI-free: they exercise the same building blocks the window's
``_delete_tag`` / ``_refresh_tags_panel`` use (app.doc_tags, TagIndex,
TagColorStore, and the pure ``merged_tag_rows`` helper) without a QApplication.
"""

from pathlib import Path

from app import doc_tags
from app.annotations import DocumentAnnotations
from app.tag_colors import TagColorStore
from app.tag_index import TagIndex
from app.window import merged_tag_rows


def _reindex(idx: TagIndex, path: Path) -> None:
    """Mirror MainWindow._index_doc_tags: push doc-level tags into the index."""
    doc = DocumentAnnotations(doc_tags=list(doc_tags.read_doc_tags(path)))
    idx.update(path, doc, front_tags=[], body_tags=[])


def test_delete_tag_data_plane(tmp_path):
    a = tmp_path / "a.md"
    a.write_text("# a", encoding="utf-8")
    b = tmp_path / "b.md"
    b.write_text("# b", encoding="utf-8")
    doc_tags.write_doc_tags(a, ["共用", "其他"])
    doc_tags.write_doc_tags(b, ["共用"])

    idx = TagIndex(path=tmp_path / "idx.json")
    _reindex(idx, a)
    _reindex(idx, b)
    colors = TagColorStore(path=tmp_path / "colors.json")
    colors.set_color("共用", "#0091FF")

    # sanity: both files carry the shared tag
    assert len(idx.files_with_tag("共用")) == 2

    # --- simulate MainWindow._delete_tag's data plane for "共用" ---
    affected = [Path(p) for p in idx.files_with_tag("共用")]
    for p in affected:
        new = [x for x in doc_tags.read_doc_tags(p) if x != "共用"]
        doc_tags.write_doc_tags(p, new)
    for p in affected:  # _on_doc_tags_changed re-indexes each touched file
        _reindex(idx, p)
    colors.remove("共用")

    # tag is gone from the index and the color store
    assert idx.files_with_tag("共用") == []
    assert "共用" not in colors.known_tags()
    # unrelated tag on file a survived (only the target assignment was stripped)
    assert doc_tags.read_doc_tags(a) == ["其他"]
    assert doc_tags.read_doc_tags(b) == []
    assert "其他" in idx.all_tags()


def test_merge_includes_known_tag_with_zero_count():
    # "新標籤" lives only in the color store (known_tags), on no file.
    counts = [("PD", 3), ("待讀", 1)]
    known = ["新標籤", "PD"]

    rows = merged_tag_rows(counts, known)
    as_dict = dict(rows)

    # created-but-unassigned tag shows up with count 0
    assert as_dict["新標籤"] == 0
    # an existing count is not clobbered to 0 by known_tags
    assert as_dict["PD"] == 3
    # ordering: descending count, then name -> zero-count tag sorts last
    assert rows[0] == ("PD", 3)
    assert rows[-1] == ("新標籤", 0)
