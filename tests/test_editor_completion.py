"""Focused EditorView wiki-link completion and image paste/drop behavior tests."""

import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage, QTextCursor
from PySide6.QtTest import QTest

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


def test_completion_replaces_query_with_combining_mark_without_eating_bracket(
    make_editor,
):
    editor = make_editor()
    editor.set_content("[[e\u0301")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    editor._insert_wikilink_completion("Target")

    assert editor.toPlainText() == "[[Target]]"


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


def test_paste_url_over_selection_creates_markdown_link(make_editor):
    editor = make_editor()
    editor.set_content("OpenAI")
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    mime = QMimeData()
    mime.setText("https://openai.com")

    editor.insertFromMimeData(mime)

    assert editor.toPlainText() == "[OpenAI](https://openai.com)"
    assert editor.textCursor().position() == len(editor.toPlainText())


def test_paste_url_over_selection_after_emoji_uses_qt_utf16_positions(make_editor):
    editor = make_editor()
    editor.set_content("😀 OpenAI")
    cursor = editor.textCursor()
    cursor.setPosition(3)  # emoji is two UTF-16 code units, then one space
    cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    mime = QMimeData()
    mime.setText("https://openai.com")

    editor.insertFromMimeData(mime)

    assert editor.toPlainText() == "😀 [OpenAI](https://openai.com)"


def _show_editor(editor, qapp, text=""):
    editor.resize(640, 420)
    editor.set_content(text)
    editor.show()
    editor.setFocus()
    qapp.processEvents()


def test_slash_command_filters_accepts_and_undoes_as_one_edit(
    make_editor, qapp
):
    editor = make_editor()
    _show_editor(editor, qapp)

    QTest.keyClicks(editor, "/table")
    qapp.processEvents()

    assert editor._slash_popup.isVisible()
    assert editor._slash_popup.current_action() == "table"
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText().startswith("| 標題1 | 標題2 |")
    assert "/table" not in editor.toPlainText()
    assert editor._slash_popup.isHidden()

    editor.document().undo()
    assert editor.toPlainText() == "/table"


def test_slash_escape_closes_but_keeps_query_and_stays_dismissed(
    make_editor, qapp
):
    editor = make_editor()
    _show_editor(editor, qapp)
    QTest.keyClicks(editor, "/tab")
    assert editor._slash_popup.isVisible()

    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert editor._slash_popup.isHidden()
    assert editor.toPlainText() == "/tab"

    QTest.keyClicks(editor, "l")
    assert editor.toPlainText() == "/tabl"
    assert editor._slash_popup.isHidden()


def test_slash_navigation_and_tab_accept_current_command(make_editor, qapp):
    editor = make_editor()
    _show_editor(editor, qapp)
    QTest.keyClicks(editor, "/")
    first = editor._slash_popup.current_action()
    QTest.keyClick(editor, Qt.Key.Key_Down)
    second = editor._slash_popup.current_action()
    assert second != first

    QTest.keyClick(editor, Qt.Key.Key_Tab)
    assert editor._slash_popup.isHidden()
    assert editor.toPlainText() != "/"


def test_slash_and_smart_enter_after_emoji_keep_offsets_correct(make_editor, qapp):
    editor = make_editor()
    _show_editor(editor, qapp, "😀\n")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClicks(editor, "/table")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText().startswith("😀\n| 標題1 | 標題2 |")

    editor.set_content("😀\n- item")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "😀\n- item\n- "


def test_format_selection_after_emoji_does_not_crash_or_misplace(make_editor):
    from app.format_actions import apply_format_action

    editor = make_editor()
    editor.set_content("😀 hello")
    cursor = editor.textCursor()
    cursor.setPosition(3)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)

    assert apply_format_action(editor, "bold") is True
    assert editor.toPlainText() == "😀 **hello**"
    assert editor.textCursor().selectedText() == "hello"


def test_wikilink_completion_wins_over_slash_popup(make_editor, qapp):
    editor = make_editor()
    editor.set_wikilink_candidates(["Roadmap", "Release"])
    _show_editor(editor, qapp)
    QTest.keyClicks(editor, "[[ro")
    qapp.processEvents()

    assert editor._completer.popup().isVisible()
    assert editor._slash_popup.isHidden()


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("- item", "- item\n- "),
        ("4. item", "4. item\n5. "),
        ("- [x] done", "- [x] done\n- [ ] "),
    ),
)
def test_enter_continues_markdown_lists(make_editor, qapp, text, expected):
    editor = make_editor()
    _show_editor(editor, qapp, text)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    QTest.keyClick(editor, Qt.Key.Key_Return)

    assert editor.toPlainText() == expected
    editor.document().undo()
    assert editor.toPlainText() == text


def test_shift_enter_and_plain_text_do_not_continue_markdown_list(
    make_editor, qapp
):
    editor = make_editor()
    _show_editor(editor, qapp, "- item")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(
        editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )
    assert editor.toPlainText() == "- item\n"

    editor.set_content("- item")
    editor.set_plain_text_mode(True)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key.Key_Return)
    QTest.keyClicks(editor, "/")
    assert editor.toPlainText() == "- item\n/"
    assert editor._slash_popup.isHidden()


def test_fenced_blocks_disable_local_slash_and_smart_enter(make_editor, qapp):
    editor = make_editor()
    _show_editor(editor, qapp, "````python\n- code /ta")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    qapp.processEvents()

    assert cursor.block().userState() > 0
    assert editor._slash_popup.isHidden()
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText().endswith("- code /ta\n")


def test_hot_cursor_paths_do_not_materialize_full_document(
    make_editor, qapp, monkeypatch
):
    editor = make_editor()
    _show_editor(editor, qapp, ("ordinary text\n" * 10_000) + "/ta")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    qapp.processEvents()

    def fail_full_text(_self):
        raise AssertionError("cursor hot path copied the full document")

    monkeypatch.setattr(EditorView, "toPlainText", fail_full_text)
    editor._emit_format_context()
    editor._refresh_slash_commands()
    assert editor._slash_popup.isVisible()
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    qapp.processEvents()
    assert editor.document().toPlainText().endswith("/t")


def test_selection_format_bar_applies_action_without_losing_selection(
    make_editor, qapp
):
    editor = make_editor()
    _show_editor(editor, qapp, "hello")
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    editor._selection_toolbar_from_mouse = True
    editor._selection_format_bar.show_for_selection(editor, 0, 5)
    assert editor._selection_format_bar.isVisible()
    assert editor._selection_format_bar.sizeHint().width() <= 260

    QTest.mouseClick(
        editor._selection_format_bar.button("bold"),
        Qt.MouseButton.LeftButton,
    )

    assert editor.toPlainText() == "**hello**"
    assert editor.textCursor().selectedText() == "hello"
    assert editor._selection_format_bar.isHidden()


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
