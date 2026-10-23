"""Source input-method, focus writing, and native Undo contracts."""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from app.editor import EditorView


@pytest.fixture
def editor(qapp):
    widget = EditorView()
    widget.resize(720, 420)
    widget.show()
    widget.setFocus()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def _compose(widget, preedit="", commit="", start=0, length=0):
    event = QInputMethodEvent(preedit, [])
    event.setCommitString(commit, start, length)
    QApplication.sendEvent(widget, event)


def test_preedit_is_not_committed_text_and_hides_completion(editor):
    editor.set_content("/table")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    editor._refresh_slash_commands()
    assert editor._slash_popup.isVisible()
    _compose(editor, "ㄊㄞ")
    assert editor.is_composing()
    assert editor.toPlainText() == "/table"
    assert not editor._slash_popup.isVisible()
    editor._accept_slash_command("table")
    editor._apply_selection_format("bold")
    assert editor.toPlainText() == "/table"
    _compose(editor, commit="臺灣😀")
    assert not editor.is_composing()
    assert editor.toPlainText() == "/table臺灣😀"
    editor.undo()
    assert editor.toPlainText() == "/table"
    editor.redo()
    assert editor.toPlainText() == "/table臺灣😀"


@pytest.mark.parametrize("key", [Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Up])
def test_composing_keys_retain_native_qt_behavior_without_smart_commands(editor, key):
    native = QPlainTextEdit()
    native.show()
    try:
        for widget in (editor, native):
            widget.setPlainText("- 項目")
            widget.moveCursor(QTextCursor.MoveOperation.End)
            _compose(widget, "ㄒ")
            event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(widget, event)
        assert editor.toPlainText() == native.toPlainText()
        assert not editor._slash_popup.isVisible()
    finally:
        native.close()


def test_composition_captures_shortcut_override_and_cancellation_restores_input(editor):
    _compose(editor, "ㄓ")
    event = QKeyEvent(
        QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
    )
    event.setAccepted(False)
    QApplication.sendEvent(editor, event)
    assert event.isAccepted()
    _compose(editor)
    assert not editor.is_composing()
    QTest.keyClicks(editor, "(")
    assert editor.toPlainText() == "()"


def test_ime_replacement_and_selected_astral_text_round_trip_undo(editor):
    editor.set_content("起點😀舊文終點")
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    _compose(editor, "ㄒ")
    _compose(editor, commit="新稿𠀀")
    assert editor.toPlainText() == "起點新稿𠀀終點"
    editor.undo()
    assert editor.toPlainText() == "起點😀舊文終點"
    editor.redo()
    assert editor.toPlainText() == "起點新稿𠀀終點"
    editor.moveCursor(QTextCursor.MoveOperation.End)
    _compose(editor, commit="完成", start=-2, length=2)
    assert editor.toPlainText() == "起點新稿𠀀完成"


def test_focus_modes_do_not_change_text_undo_or_navigation(editor, monkeypatch):
    original = "\n".join(f"段落 {index}" for index in range(120))
    editor.set_content(original)
    centered = []
    monkeypatch.setattr(editor, "centerCursor", lambda: centered.append(True))
    editor.set_typewriter_scroll(True)
    editor.set_focus_paragraph(True)
    editor.moveCursor(QTextCursor.MoveOperation.End)
    assert not centered
    QTest.keyClicks(editor, "x")
    assert len(centered) == 1
    cursor = editor.textCursor()
    cursor.movePosition(
        QTextCursor.MoveOperation.PreviousCharacter, QTextCursor.MoveMode.KeepAnchor
    )
    editor.setTextCursor(cursor)
    assert len(centered) == 1
    assert not editor.grab().isNull()
    editor.set_focus_paragraph(False)
    editor.set_typewriter_scroll(False)
    editor.undo()
    assert editor.toPlainText() == original
    assert not editor.document().isModified()


def test_unsaved_draft_resource_guard_creates_no_files(editor, tmp_path):
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QImage

    editor.set_document_path(tmp_path / "recovery.md")
    editor.set_resource_import_enabled(False)
    messages = []
    editor.image_status.connect(messages.append)
    mime = QMimeData()
    mime.setImageData(QImage(4, 4, QImage.Format.Format_RGB32))
    editor.insertFromMimeData(mime)
    editor._insert_resource_files([str(tmp_path / "attachment.txt")])
    assert messages == ["請先儲存草稿，再加入圖片或附件"] * 2
    assert list(tmp_path.iterdir()) == []
    assert editor.toPlainText() == ""
