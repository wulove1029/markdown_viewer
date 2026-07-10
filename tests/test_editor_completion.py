"""Focused EditorView wiki-link completion behavior tests."""

from PySide6.QtGui import QTextCursor

from app.editor import EditorView


def test_completion_replaces_typed_query_and_places_cursor_after_closing_brackets(qapp):
    editor = EditorView()
    editor.set_content("See [[ro")
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    editor._insert_wikilink_completion("projects/Roadmap")

    assert editor.toPlainText() == "See [[projects/Roadmap]]"
    assert editor.textCursor().position() == len(editor.toPlainText())
