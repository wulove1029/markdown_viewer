import json
from pathlib import Path

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


def test_create_document_writes_empty_file_with_suffix(tmp_path):
    md = file_ops.create_document(tmp_path, "靈感")
    assert md == tmp_path / "靈感.md"
    assert md.read_bytes() == b""

    txt = file_ops.create_document(tmp_path, "備忘", ".txt")
    assert txt == tmp_path / "備忘.txt"
    assert txt.read_bytes() == b""

    # A matching typed extension is not doubled (case-insensitive).
    typed = file_ops.create_document(tmp_path, "list.TXT", ".txt")
    assert typed == tmp_path / "list.txt"


def test_create_document_refuses_existing_and_invalid_names(tmp_path):
    file_ops.create_document(tmp_path, "note", ".txt")
    with pytest.raises(OSError):
        file_ops.create_document(tmp_path, "note", ".txt")
    with pytest.raises(OSError):
        file_ops.create_document(tmp_path, "note.txt", ".txt")
    with pytest.raises(OSError):
        file_ops.create_document(tmp_path, "bad|name", ".txt")
    with pytest.raises(OSError):
        file_ops.create_document(tmp_path, "   ", ".md")
    # The .txt name never auto-numbers: the first create stays the only file.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["note.txt"]


def test_create_document_uses_exclusive_create_against_a_race(tmp_path, monkeypatch):
    """A file created *after* the preflight check must never be overwritten.

    ``create_document`` first does a friendly ``exists()`` check, then must
    still create the file exclusively so a competing writer that wins the
    race between that check and the real write is never silently clobbered.
    """
    target = tmp_path / "note.md"
    target.write_bytes(b"someone else's content")
    # Simulate the TOCTOU window: the preflight check reports "free" even
    # though the file already exists by the time the real write happens.
    monkeypatch.setattr(file_ops.Path, "exists", lambda self: False)

    with pytest.raises(OSError):
        file_ops.create_document(tmp_path, "note", ".md")

    assert target.read_bytes() == b"someone else's content"


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


def test_move_rebases_main_and_backup_without_moving_shared_assets(tmp_path):
    old = tmp_path / 'note.md'
    old.write_bytes(b'[current](assets/file.pdf)\r\n')
    old.with_name(old.name + '.bak').write_bytes(b'[previous](assets/file.pdf)\r\n')
    assets = tmp_path / 'assets'
    assets.mkdir()
    resource = assets / 'file.pdf'
    resource.write_bytes(b'shared attachment')
    (tmp_path / 'other.md').write_text('[other](assets/file.pdf)', encoding='utf-8')
    notes = old.with_name(old.name + '.notes.json')
    notes.write_bytes(b'{"doc_tags":["tag"]}')
    notes.with_name(notes.name + '.bak').write_bytes(b'{}')
    archive = tmp_path / 'archive'
    archive.mkdir()

    file_ops.move_document(old, archive)

    assert (archive / 'note.md').read_bytes() == b'[current](../assets/file.pdf)\r\n'
    assert (archive / 'note.md.bak').read_bytes() == b'[previous](../assets/file.pdf)\r\n'
    assert (archive / notes.name).read_bytes() == b'{"doc_tags":["tag"]}'
    assert (archive / (notes.name + '.bak')).read_bytes() == b'{}'
    assert resource.read_bytes() == b'shared attachment'
    assert (tmp_path / 'other.md').read_text(encoding='utf-8') == '[other](assets/file.pdf)'


@pytest.mark.parametrize('suffix', ['.notes.json', '.highlights.json', '.bak', '.notes.json.bak'])
def test_destination_sidecar_or_backup_collision_prevents_any_move(tmp_path, suffix):
    old, new = tmp_path / 'old.md', tmp_path / 'new.md'
    old.write_bytes(b'original document')
    collision = new.with_name(new.name + suffix)
    collision.write_bytes(b'unrelated existing data')
    with pytest.raises(OSError, match='已存在'):
        file_ops.rename_document(old, new)
    assert old.read_bytes() == b'original document'
    assert not new.exists()
    assert collision.read_bytes() == b'unrelated existing data'


def test_sidecar_publish_failure_rolls_back_original_bytes_and_all_paths(tmp_path, monkeypatch):
    old = tmp_path / 'note.md'
    old.write_bytes(b'[x](assets/a.png)\r\n')
    notes = old.with_name(old.name + '.notes.json')
    notes.write_bytes(b'{"annotations":[]}')
    archive = tmp_path / 'archive'
    archive.mkdir()
    new = archive / old.name
    original_rename = file_ops._rename_no_replace

    def fail_sidecar(source, target):
        if target == archive / notes.name:
            raise PermissionError('injected sidecar lock')
        original_rename(source, target)

    monkeypatch.setattr(file_ops, '_rename_no_replace', fail_sidecar)
    with pytest.raises(OSError, match='原始文件與註記已保留'):
        file_ops.move_document(old, archive)
    assert old.read_bytes() == b'[x](assets/a.png)\r\n'
    assert notes.read_bytes() == b'{"annotations":[]}'
    assert not new.exists()
    assert list(archive.iterdir()) == []
    assert not list(tmp_path.glob('.markdown-relocate-*'))


def test_disk_full_during_staging_leaves_sources_untouched(tmp_path, monkeypatch):
    old, new = tmp_path / 'old.md', tmp_path / 'new.md'
    old.write_bytes(b'original')
    monkeypatch.setattr(file_ops, '_stage_relocation_file', lambda *_args: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError, match='disk full'):
        file_ops.rename_document(old, new)
    assert old.read_bytes() == b'original'
    assert not new.exists()


def test_external_source_change_during_staging_is_preserved(tmp_path, monkeypatch):
    old, new = tmp_path / 'old.md', tmp_path / 'new.md'
    old.write_bytes(b'original')
    stage = file_ops._stage_relocation_file

    def external_edit(source, target, data):
        stage(source, target, data)
        source.write_bytes(b'new externally written content')

    monkeypatch.setattr(file_ops, '_stage_relocation_file', external_edit)
    with pytest.raises(OSError, match='已變更'):
        file_ops.rename_document(old, new)
    assert old.read_bytes() == b'new externally written content'
    assert not new.exists()


def test_destination_appearing_during_publish_is_not_overwritten(tmp_path, monkeypatch):
    old, new = tmp_path / 'old.md', tmp_path / 'new.md'
    old.write_bytes(b'original')
    original_rename = file_ops._rename_no_replace

    def raced_destination(source, target):
        if target == new:
            new.write_bytes(b'external destination')
        original_rename(source, target)

    monkeypatch.setattr(file_ops, '_rename_no_replace', raced_destination)
    with pytest.raises(OSError):
        file_ops.rename_document(old, new)
    assert old.read_bytes() == b'original'
    assert new.read_bytes() == b'external destination'


def test_rollback_failure_retains_original_in_reported_recovery_directory(tmp_path, monkeypatch):
    old, new = tmp_path / 'old.md', tmp_path / 'new.md'
    old.write_bytes(b'original')
    original_rename = file_ops._rename_no_replace

    def fail_publish_and_restore(source, target):
        if target in {old, new}:
            raise PermissionError('injected lock')
        original_rename(source, target)

    monkeypatch.setattr(file_ops, '_rename_no_replace', fail_publish_and_restore)
    with pytest.raises(OSError, match='備份資料夾') as error:
        file_ops.rename_document(old, new)
    retained = list(tmp_path.glob('.markdown-relocate-originals-*'))
    assert len(retained) == 1
    assert str(retained[0]) in str(error.value)
    assert (retained[0] / '0').read_bytes() == b'original'


def test_unsafe_markdown_main_or_backup_prevents_any_move(tmp_path):
    old = tmp_path / 'note.md'
    old.write_bytes(b'[x](assets/a.png)')
    backup = old.with_name(old.name + '.bak')
    backup.write_bytes(b'[[ambiguous]]')
    archive = tmp_path / 'archive'
    archive.mkdir()
    with pytest.raises(OSError, match='wiki-links'):
        file_ops.move_document(old, archive)
    assert old.read_bytes() == b'[x](assets/a.png)'
    assert backup.read_bytes() == b'[[ambiguous]]'
    assert list(archive.iterdir()) == []
