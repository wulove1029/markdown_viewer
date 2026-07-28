"""Focused EditorView wiki-link completion and image paste/drop behavior tests."""

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage, QTextCursor

from app.editor import EditorView


@pytest.fixture
def make_editor(qapp):
    """Create EditorView instances and dispose of them after the test.

    Left uncleaned, unparented top-level QWidget instances (plus the
    QCompleter popup each EditorView owns) pile up in the shared, session-
    scoped QApplication and have been observed to cause intermittent
    unrelated failures later in the suite. Mirrors the ``make_window``
    fixture in test_window_integration.py.
    """
    editors = []

    def _make():
        editor = EditorView()
        editors.append(editor)
        return editor

    yield _make
    for editor in reversed(editors):
        editor.close()
        editor.setParent(None)
        editor.deleteLater()
    qapp.processEvents()


def test_completion_replaces_typed_query_and_places_cursor_after_closing_brackets(
    make_editor,
):
    editor = make_editor()
    editor.set_content("See [[ro")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    editor._insert_wikilink_completion("projects/Roadmap")

    assert editor.toPlainText() == "See [[projects/Roadmap]]"
    assert editor.textCursor().position() == len(editor.toPlainText())


def _image_mime() -> QMimeData:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0xFF00FF)
    mime = QMimeData()
    mime.setImageData(image)
    return mime


def test_paste_image_with_document_path_saves_asset_and_inserts_link(
    make_editor, tmp_path
):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    editor = make_editor()
    editor.set_document_path(doc)

    editor.insertFromMimeData(_image_mime())

    text = editor.toPlainText()
    assert text.startswith("![](assets/image-")
    assert text.endswith(".png)")
    saved = list((tmp_path / "assets").glob("*.png"))
    assert len(saved) == 1


def test_paste_image_without_document_path_does_nothing_but_notifies(make_editor):
    editor = make_editor()
    messages = []
    editor.image_status.connect(messages.append)

    editor.insertFromMimeData(_image_mime())

    assert editor.toPlainText() == ""
    assert messages


def test_can_insert_from_mime_data_true_for_image(make_editor):
    editor = make_editor()
    assert editor.canInsertFromMimeData(_image_mime()) is True


def test_paste_plain_text_behaves_as_before(make_editor):
    editor = make_editor()
    mime = QMimeData()
    mime.setText("hello world")

    editor.insertFromMimeData(mime)

    assert editor.toPlainText() == "hello world"


def test_drop_image_file_outside_document_folder_copies_and_inserts(
    make_editor, tmp_path
):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    editor = make_editor()
    editor.set_document_path(doc)

    outside_dir = tmp_path.parent / "outside_drop_src"
    outside_dir.mkdir(exist_ok=True)
    src = outside_dir / "photo.png"
    src.write_bytes(b"fake-png-bytes")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(src))])
    event = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.dropEvent(event)

    assert event.isAccepted()
    assert editor.toPlainText() == "![](assets/photo.png)"
    assert (tmp_path / "assets" / "photo.png").is_file()


def test_drop_non_image_file_falls_through_to_default_text_handling(
    make_editor, tmp_path
):
    # A non-image URL drop is not our concern: it must be handled exactly as
    # QPlainTextEdit always has (inserted as text), unchanged by our override.
    # This unmodified base behavior is what leaves room for MainWindow's own
    # dragEnterEvent/dropEvent to open .md/.pdf files dropped outside the
    # editor's own text area.
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    editor = make_editor()
    editor.set_document_path(doc)

    other = tmp_path / "other.md"
    other.write_text("# other", encoding="utf-8")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other))])
    event = QDropEvent(
        QPointF(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )

    editor.dropEvent(event)

    assert event.isAccepted()
    assert "other.md" in editor.toPlainText()


def test_paste_image_save_failure_does_not_insert_link_but_notifies(
    make_editor, tmp_path, monkeypatch
):
    doc = tmp_path / "note.md"
    doc.write_text("# note", encoding="utf-8")
    editor = make_editor()
    editor.set_document_path(doc)
    messages = []
    editor.image_status.connect(messages.append)

    monkeypatch.setattr(QImage, "save", lambda self, *a, **k: False)
    editor.insertFromMimeData(_image_mime())

    assert editor.toPlainText() == ""
    assert messages
