"""Independent relocation acceptance, including actual Windows cross-drive IO."""

from pathlib import Path
import tempfile

import pytest
from markdown_it import MarkdownIt

from app import file_ops
from app.document_relocation import rebase_markdown_links


def test_unmatched_backticks_in_separate_blocks_do_not_hide_real_links(tmp_path):
    destination = tmp_path / "archive"
    destination.mkdir()
    source = "Paragraph `\n\n[real](assets/a.png)\n\nAnother `\n"
    assert '<a href="assets/a.png">' in MarkdownIt("commonmark").render(source)

    converted = rebase_markdown_links(source, tmp_path / "note.md", destination / "note.md")

    assert converted == source.replace("(assets/a.png)", "(../assets/a.png)")


def test_failed_signature_after_publication_reports_retained_destination(tmp_path, monkeypatch):
    old = tmp_path / "old.md"
    new = tmp_path / "new.md"
    old.write_bytes(b"original")
    signature = file_ops._signature

    def fail_new_signature(path):
        if path == new and new.exists():
            raise PermissionError("signature unavailable")
        return signature(path)

    monkeypatch.setattr(file_ops, "_signature", fail_new_signature)
    with pytest.raises(OSError) as error:
        file_ops.rename_document(old, new)

    assert old.read_bytes() == b"original"
    # A retained publication must be named; callers cannot assume rollback
    # removed it and safely retry to the same destination.
    assert not new.exists() or str(new) in str(error.value)


def test_external_source_appearing_during_rollback_is_not_overwritten(tmp_path, monkeypatch):
    old = tmp_path / "old.md"
    old.write_bytes(b"original document")
    notes = old.with_name(old.name + ".notes.json")
    notes.write_bytes(b"original notes")
    archive = tmp_path / "archive"
    archive.mkdir()
    original_rename = file_ops._rename_no_replace

    def fail_sidecar_and_recreate_source(source, target):
        if target == archive / notes.name:
            old.write_bytes(b"external replacement")
            raise PermissionError("sidecar publication blocked")
        original_rename(source, target)

    monkeypatch.setattr(file_ops, "_rename_no_replace", fail_sidecar_and_recreate_source)
    with pytest.raises(OSError) as error:
        file_ops.move_document(old, archive)

    assert old.read_bytes() == b"external replacement"
    assert notes.read_bytes() == b"original notes"
    retained = list(tmp_path.glob(".markdown-relocate-originals-*"))
    assert len(retained) == 1
    assert str(retained[0]) in str(error.value)
    assert (retained[0] / "0").read_bytes() == b"original document"
    assert not (archive / old.name).exists()


def test_external_destination_change_is_preserved_during_rollback(tmp_path, monkeypatch):
    old = tmp_path / "old.md"
    old.write_bytes(b"original document")
    notes = old.with_name(old.name + ".notes.json")
    notes.write_bytes(b"original notes")
    archive = tmp_path / "archive"
    archive.mkdir()
    moved = archive / old.name
    original_rename = file_ops._rename_no_replace

    def fail_sidecar_after_external_destination_edit(source, target):
        if target == archive / notes.name:
            moved.write_bytes(b"changed by external application")
            raise PermissionError("sidecar publication blocked")
        original_rename(source, target)

    monkeypatch.setattr(file_ops, "_rename_no_replace", fail_sidecar_after_external_destination_edit)
    with pytest.raises(OSError) as error:
        file_ops.move_document(old, archive)

    assert old.read_bytes() == b"original document"
    assert notes.read_bytes() == b"original notes"
    assert moved.read_bytes() == b"changed by external application"
    assert str(moved) in str(error.value)


def test_real_cross_drive_resource_move_refuses_without_changing_sources(tmp_path):
    with tempfile.TemporaryDirectory(prefix="mdv-cross-drive-review-") as folder:
        destination = Path(folder)
        if destination.drive.casefold() == tmp_path.drive.casefold():
            pytest.skip("The test host has no second drive for this fixture")
        old = tmp_path / "note.md"
        asset = tmp_path / "assets" / "attachment.txt"
        asset.parent.mkdir()
        asset.write_bytes(b"attachment bytes")
        old.write_bytes(b"[attachment](assets/attachment.txt)\r\n")
        notes = old.with_name(old.name + ".notes.json")
        notes.write_bytes(b'{"annotation":"retained"}')

        # The application renderer refuses file: Markdown links. Refuse the
        # cross-drive rebase instead of publishing links it cannot display.
        with pytest.raises(OSError):
            file_ops.move_document(old, destination)

        assert old.read_bytes() == b"[attachment](assets/attachment.txt)\r\n"
        assert notes.read_bytes() == b'{"annotation":"retained"}'
        assert list(destination.iterdir()) == []
        assert asset.read_bytes() == b"attachment bytes"


def test_real_cross_drive_move_without_relative_resources_preserves_bytes(tmp_path):
    with tempfile.TemporaryDirectory(prefix="mdv-cross-drive-review-") as folder:
        destination = Path(folder)
        if destination.drive.casefold() == tmp_path.drive.casefold():
            pytest.skip("The test host has no second drive for this fixture")
        old = tmp_path / "note.md"
        old.write_bytes(b"# unchanged\r\n[remote](https://example.com/a)\r\n")
        original = old.read_bytes()
        notes = old.with_name(old.name + ".notes.json")
        notes.write_bytes(b'{"annotation":"retained"}')

        mapping = file_ops.move_document(old, destination)

        assert mapping == {str(old): str(destination / old.name)}
        assert (destination / old.name).read_bytes() == original
        assert (destination / notes.name).read_bytes() == b'{"annotation":"retained"}'
        assert not old.exists()
        assert not notes.exists()


@pytest.mark.parametrize("protected", [
    "$[x](assets/a.png)$\n\n",
    "$$\n[x](assets/a.png)\n$$\n\n",
    '---\ntitle: "[x](assets/a.png)"\n---\n\n',
])
def test_math_and_frontmatter_are_preserved_or_move_is_refused(tmp_path, protected):
    source = tmp_path / "note.md"
    destination = tmp_path / "archive"
    destination.mkdir()
    text = protected + "[real](assets/real.png)"
    source.write_text(text, encoding="utf-8")

    try:
        file_ops.move_document(source, destination)
    except OSError:
        assert source.read_text(encoding="utf-8") == text
        assert list(destination.iterdir()) == []
    else:
        assert (destination / source.name).read_text(encoding="utf-8") == protected + "[real](../assets/real.png)"
