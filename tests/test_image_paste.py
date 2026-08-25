"""Pure-logic tests for saving/importing pasted or dropped images."""

from PySide6.QtGui import QImage

from app.image_paste import (
    import_image_file,
    is_image_file,
    markdown_image_link,
    save_clipboard_image,
)


def _make_image() -> QImage:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFF00FF)
    return image


def test_save_clipboard_image_returns_none_for_null_image(qapp, tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")

    assert save_clipboard_image(QImage(), doc) is None
    assert not (tmp_path / "assets").exists()


def test_save_clipboard_image_returns_none_when_save_fails(qapp, tmp_path, monkeypatch):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    monkeypatch.setattr(QImage, "save", lambda self, *a, **k: False)

    assert save_clipboard_image(_make_image(), doc) is None


def test_save_clipboard_image_creates_assets_folder_and_png(qapp, tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")

    rel = save_clipboard_image(_make_image(), doc)

    assert rel.startswith("assets/image-")
    assert rel.endswith(".png")
    saved = tmp_path / rel
    assert saved.is_file()


def test_save_clipboard_image_numbers_collisions(qapp, tmp_path, monkeypatch):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()

    class _FixedDatetime:
        @staticmethod
        def now():
            import datetime as _dt

            return _dt.datetime(2026, 1, 2, 3, 4, 5)

    import app.image_paste as image_paste

    monkeypatch.setattr(image_paste, "datetime", _FixedDatetime)

    first = save_clipboard_image(_make_image(), doc)
    second = save_clipboard_image(_make_image(), doc)
    third = save_clipboard_image(_make_image(), doc)

    assert first == "assets/image-20260102-030405.png"
    assert second == "assets/image-20260102-030405-1.png"
    assert third == "assets/image-20260102-030405-2.png"


def test_import_image_file_inside_document_folder_is_not_copied(tmp_path):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    sub = tmp_path / "pics"
    sub.mkdir()
    src = sub / "photo.png"
    src.write_bytes(b"fake-png-bytes")

    rel = import_image_file(src, doc)

    assert rel == "pics/photo.png"
    # Not copied into assets/.
    assert not (tmp_path / "assets").exists()


def test_import_image_file_outside_document_folder_is_copied(tmp_path):
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    doc = doc_dir / "note.md"
    doc.write_text("# note", encoding="utf-8")

    outside_dir = tmp_path / "downloads"
    outside_dir.mkdir()
    src = outside_dir / "screenshot.png"
    src.write_bytes(b"fake-png-bytes")

    rel = import_image_file(src, doc)

    assert rel == "assets/screenshot.png"
    copied = doc_dir / "assets" / "screenshot.png"
    assert copied.is_file()
    assert copied.read_bytes() == b"fake-png-bytes"


def test_import_image_file_collision_numbers_copy(tmp_path):
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    doc = doc_dir / "note.md"
    doc.write_text("# note", encoding="utf-8")
    (doc_dir / "assets").mkdir()
    (doc_dir / "assets" / "screenshot.png").write_bytes(b"existing")

    outside_dir = tmp_path / "downloads"
    outside_dir.mkdir()
    src = outside_dir / "screenshot.png"
    src.write_bytes(b"new-bytes")

    rel = import_image_file(src, doc)

    assert rel == "assets/screenshot-1.png"


def test_is_image_file():
    assert is_image_file("a/b/photo.PNG")
    assert is_image_file("photo.jpeg")
    assert not is_image_file("document.pdf")
    assert not is_image_file("readme.md")


def test_markdown_image_link_plain_path():
    assert markdown_image_link("assets/image-1.png") == "![](assets/image-1.png)"


def test_markdown_image_link_with_alt():
    assert (
        markdown_image_link("assets/image-1.png", alt="cat")
        == "![cat](assets/image-1.png)"
    )


def test_markdown_image_link_wraps_paths_with_spaces():
    assert (
        markdown_image_link("assets/my photo.png")
        == "![](assets/my%20photo.png)"
    )
