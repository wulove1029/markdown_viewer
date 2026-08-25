from pathlib import Path

import pytest

from app.attachments import import_attachment_file, markdown_attachment_link


def test_attachment_inside_note_tree_is_referenced_without_copy(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("", encoding="utf-8")
    source = tmp_path / "files" / "guide.pdf"
    source.parent.mkdir()
    source.write_bytes(b"pdf")

    assert import_attachment_file(source, note) == "files/guide.pdf"
    assert not (tmp_path / "assets").exists()


def test_external_attachment_is_copied_collision_safely(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    note = notes / "note.md"
    note.write_text("", encoding="utf-8")
    external = tmp_path / "external" / "data.csv"
    external.parent.mkdir()
    external.write_text("a,b", encoding="utf-8")
    assets = notes / "assets"
    assets.mkdir()
    (assets / "data.csv").write_text("old", encoding="utf-8")

    relative = import_attachment_file(external, note)

    assert relative == "assets/data-1.csv"
    assert (assets / "data-1.csv").read_text(encoding="utf-8") == "a,b"


def test_attachment_cannot_be_the_note_itself(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        import_attachment_file(note, note)


def test_markdown_attachment_link_handles_spaces_and_brackets():
    assert markdown_attachment_link("assets/my file.pdf") == (
        "[my file.pdf](assets/my%20file.pdf)"
    )
    assert markdown_attachment_link("files/a.pdf", "[Guide]") == (
        r"[\[Guide\]](files/a.pdf)"
    )
    assert markdown_attachment_link("assets/guide#v1 (final)).pdf") == (
        "[guide#v1 (final)).pdf](assets/guide%23v1%20%28final%29%29.pdf)"
    )
