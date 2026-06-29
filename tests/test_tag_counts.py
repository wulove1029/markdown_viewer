"""Tests for tag index counts and front-matter tag integration."""

from dataclasses import dataclass, field

from app.tag_index import TagIndex


@dataclass
class _Annot:
    tags: list = field(default_factory=list)


@dataclass
class _Doc:
    doc_tags: list = field(default_factory=list)
    annotations: list = field(default_factory=list)


def test_front_tags_counted(tmp_path):
    idx = TagIndex(tmp_path / "idx.json")
    idx.update(tmp_path / "a.md", _Doc(), front_tags=["research", "ai"])
    idx.update(tmp_path / "b.md", _Doc(doc_tags=["research"]))
    assert set(idx.all_tags()) == {"research", "ai"}
    counts = dict(idx.tag_counts())
    assert counts["research"] == 2  # a.md (front) + b.md (doc)
    assert counts["ai"] == 1
    assert {p.split("\\")[-1].split("/")[-1] for p in idx.files_with_tag("research")} == {
        "a.md",
        "b.md",
    }


def test_entry_removed_when_no_tags(tmp_path):
    idx = TagIndex(tmp_path / "idx.json")
    idx.update(tmp_path / "a.md", _Doc(doc_tags=["x"]))
    assert idx.all_tags() == ["x"]
    idx.update(tmp_path / "a.md", _Doc(), front_tags=[])
    assert idx.all_tags() == []


def test_counts_sorted_by_frequency(tmp_path):
    idx = TagIndex(tmp_path / "idx.json")
    idx.update(tmp_path / "a.md", _Doc(doc_tags=["common"]))
    idx.update(tmp_path / "b.md", _Doc(doc_tags=["common", "rare"]))
    counts = idx.tag_counts()
    assert counts[0][0] == "common"  # most frequent first
