import json

import pytest

from app import file_ops
from app.annotations import DocumentAnnotations
from app.tag_index import TagIndex


def test_create_note_writes_utf8_and_numbers_duplicates(tmp_path):
    first = file_ops.create_note(tmp_path, "靈感")
    assert first == tmp_path / "靈感.md"
    assert first.read_text(encoding="utf-8") == "# 靈感\n"

    second = file_ops.create_note(tmp_path, "靈感")
    third = file_ops.create_note(tmp_path, "靈感.md")
    assert second == tmp_path / "靈感 2.md"
    assert third == tmp_path / "靈感 3.md"


def test_create_note_rejects_invalid_names(tmp_path):
    with pytest.raises(OSError):
        file_ops.create_note(tmp_path, "bad|name")
    with pytest.raises(OSError):
        file_ops.create_note(tmp_path, "   ")


def test_create_folder(tmp_path):
    created = file_ops.create_folder(tmp_path, "inbox")
    assert created.is_dir()
    with pytest.raises(OSError):
        file_ops.create_folder(tmp_path, "inbox")


def test_rename_document_moves_sidecars_and_tag_index(tmp_path):
    doc = tmp_path / "old.md"
    doc.write_text("# old", encoding="utf-8")
    sidecar = tmp_path / "old.md.notes.json"
    sidecar.write_text(json.dumps({"doc_tags": ["keep"]}), encoding="utf-8")

    index = TagIndex(tmp_path / "tags.json")
    index.update(doc, DocumentAnnotations(doc_tags=["keep"]))

    new = tmp_path / "new.md"
    mapping = file_ops.rename_document(doc, new)
    assert mapping == {str(doc): str(new)}
    assert not doc.exists()
    assert new.read_text(encoding="utf-8") == "# old"
    assert not sidecar.exists()
    assert (tmp_path / "new.md.notes.json").exists()

    index.migrate_paths(mapping)
    assert index.files_with_tag("keep") == [str(new.resolve())]

    # The migrated key must survive a reload from disk.
    reloaded = TagIndex(tmp_path / "tags.json")
    assert reloaded.files_with_tag("keep") == [str(new.resolve())]


def test_rename_document_refuses_existing_target(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")
    with pytest.raises(OSError):
        file_ops.rename_document(a, b)


def test_move_document_into_subfolder(tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    (tmp_path / "note.md.highlights.json").write_text("{}", encoding="utf-8")
    dest = tmp_path / "archive"
    dest.mkdir()

    mapping = file_ops.move_document(doc, dest)
    assert mapping == {str(doc): str(dest / "note.md")}
    assert (dest / "note.md").exists()
    assert (dest / "note.md.highlights.json").exists()
    assert not doc.exists()

    # Moving into the same folder is a no-op.
    assert file_ops.move_document(dest / "note.md", dest) == {}


def test_rename_folder_maps_every_file(tmp_path):
    folder = tmp_path / "olddir"
    (folder / "deep").mkdir(parents=True)
    (folder / "a.md").write_text("a", encoding="utf-8")
    (folder / "deep" / "b.md").write_text("b", encoding="utf-8")

    mapping = file_ops.rename_folder(folder, "newdir")
    newdir = tmp_path / "newdir"
    assert mapping == {
        str(folder / "a.md"): str(newdir / "a.md"),
        str(folder / "deep" / "b.md"): str(newdir / "deep" / "b.md"),
    }
    assert (newdir / "deep" / "b.md").exists()
    assert not folder.exists()


def test_delete_document_permanent_removes_sidecars(tmp_path, monkeypatch):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    notes = tmp_path / "note.md.notes.json"
    notes.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(file_ops, "_send2trash", None)
    trashed = file_ops.delete_document(doc)
    assert trashed is False
    assert not doc.exists()
    assert not notes.exists()


def test_delete_document_uses_trash_when_available(tmp_path, monkeypatch):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    calls = []
    monkeypatch.setattr(file_ops, "_send2trash", lambda p: calls.append(p))

    trashed = file_ops.delete_document(doc)
    assert trashed is True
    assert calls == [str(doc)]
