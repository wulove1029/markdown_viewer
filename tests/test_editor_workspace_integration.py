"""Integration requirements for independent per-tab editor workspaces.

This file intentionally exercises the real ``MainWindow`` / ``EditorView``
coordination while replacing renderer- and sidebar-only dependencies with the
same lightweight fakes used by the main window integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtTest import QTest

from app import session_state
from app import window as window_mod
from app.recovery import RecoveryStore
from tests.test_window_integration import (
    _FakePanel,
    _FakePdfView,
    _FakeRenderer,
    _FakeTagIndex,
)


@pytest.fixture(autouse=True)
def _isolated_workspace_dependencies(tmp_path, monkeypatch):
    """Keep the test focused on editor state, without WebEngine or user data."""

    monkeypatch.setattr(window_mod, "RendererView", _FakeRenderer)
    monkeypatch.setattr(window_mod, "PdfView", _FakePdfView)
    monkeypatch.setattr(window_mod, "LeftPanel", _FakePanel)
    monkeypatch.setattr(window_mod, "TagIndex", _FakeTagIndex)
    monkeypatch.setattr(
        window_mod.MainWindow, "_refresh_tags_panel", lambda self: None
    )
    monkeypatch.setattr(
        window_mod.MainWindow,
        "_refresh_link_index",
        lambda self, force=False: None,
    )
    monkeypatch.setattr(window_mod.QMessageBox, "warning", lambda *args: None)
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.Discard,
    )

    settings_path = tmp_path / "workspace-settings.ini"

    def isolated_settings(*_args, **_kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(window_mod, "QSettings", isolated_settings)
    monkeypatch.setattr(session_state, "QSettings", isolated_settings)
    monkeypatch.setattr(
        window_mod,
        "RecoveryStore",
        lambda: RecoveryStore(tmp_path / "recovery"),
    )

    # Suppress the delayed update check and watcher work while making the
    # zero-delay editor-scroll restoration deterministic in this test file.
    def selective_single_shot(*args):
        delay = int(args[0])
        if delay == 0:
            callback = args[-1]
            callback()

    monkeypatch.setattr(
        window_mod.QTimer,
        "singleShot",
        staticmethod(selective_single_shot),
    )


@pytest.fixture
def make_workspace_window(qapp):
    windows = []

    def _make():
        window = window_mod.MainWindow()
        windows.append(window)
        return window

    yield _make

    for window in reversed(windows):
        for state in window._tab_state.values():
            document = state.get("editor_document")
            if isinstance(document, QTextDocument):
                document.setModified(False)
        window._editor.document().setModified(False)
        window.close()
    qapp.processEvents()


def _write_long_note(path: Path, prefix: str, lines: int = 260) -> str:
    text = "\n".join(f"{prefix} line {index:03}" for index in range(lines))
    path.write_text(text, encoding="utf-8")
    return text


def _enter_markdown_editor(window, path: Path) -> None:
    window.open_path(str(path))
    if not window._edit_mode:
        window._toggle_edit_mode()
    assert window._current_kind == "markdown"
    assert window._edit_mode is True


def _set_cursor(editor, position: int) -> None:
    cursor = editor.textCursor()
    cursor.setPosition(position)
    editor.setTextCursor(cursor)


def _append_one_undo_step(editor, marker: str) -> None:
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    cursor.insertText(marker)
    cursor.endEditBlock()
    editor.setTextCursor(cursor)


def test_tab_switch_preserves_dirty_documents_undo_cursor_and_scroll_without_prompt(
    make_workspace_window, tmp_path, monkeypatch, qapp
):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first_original = _write_long_note(first, "FIRST")
    second_original = _write_long_note(second, "SECOND")
    questions = []
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: (
            questions.append(args),
            window_mod.QMessageBox.StandardButton.Discard,
        )[1],
    )

    window = make_workspace_window()
    window.resize(980, 560)
    window.show()
    _enter_markdown_editor(window, first)
    qapp.processEvents()

    first_document = window._editor.document()
    _append_one_undo_step(window._editor, "\nFIRST-DIRTY")
    first_cursor = first_original.index("FIRST line 040") + 5
    _set_cursor(window._editor, first_cursor)
    first_bar = window._editor.verticalScrollBar()
    assert first_bar.maximum() > 20
    first_scroll = first_bar.maximum()
    first_bar.setValue(first_scroll)

    _enter_markdown_editor(window, second)
    qapp.processEvents()
    second_document = window._editor.document()
    assert second_document is not first_document
    _append_one_undo_step(window._editor, "\nSECOND-DIRTY")
    second_cursor = second_original.index("SECOND line 090") + 7
    _set_cursor(window._editor, second_cursor)
    second_bar = window._editor.verticalScrollBar()
    assert second_bar.maximum() > 20
    second_scroll = second_bar.maximum() // 2
    second_bar.setValue(second_scroll)

    window.open_path(str(first))
    qapp.processEvents()
    assert questions == []
    assert window._editor.document() is first_document
    assert window._editor.toPlainText() == first_original + "\nFIRST-DIRTY"
    assert window._editor.textCursor().position() == first_cursor
    assert window._editor.verticalScrollBar().value() == first_scroll
    assert first_document.isUndoAvailable()
    first_document.undo()
    assert window._editor.toPlainText() == first_original
    assert second_document.toPlainText() == second_original + "\nSECOND-DIRTY"
    assert second_document.isUndoAvailable()

    window.open_path(str(second))
    qapp.processEvents()
    assert questions == []
    assert window._editor.document() is second_document
    assert window._editor.toPlainText() == second_original + "\nSECOND-DIRTY"
    assert window._editor.textCursor().position() == second_cursor
    assert window._editor.verticalScrollBar().value() == second_scroll
    second_document.undo()
    assert window._editor.toPlainText() == second_original


def test_markdown_auto_pair_and_tab_differ_from_plain_text_native_input(
    make_workspace_window, tmp_path, qapp
):
    markdown = tmp_path / "note.md"
    plain = tmp_path / "note.txt"
    markdown.write_text("item", encoding="utf-8")
    plain.write_text("item", encoding="utf-8")
    window = make_workspace_window()
    window.show()

    _enter_markdown_editor(window, markdown)
    editor = window._editor
    editor.setFocus()
    _set_cursor(editor, 0)
    QTest.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "    item"
    _set_cursor(editor, len(editor.toPlainText()))
    QTest.keyClicks(editor, "(")
    assert editor.toPlainText() == "    item()"
    assert editor.textCursor().position() == len("    item(")
    editor.setPlainText("item")
    _set_cursor(editor, 0)
    QTest.keyClick(editor, Qt.Key.Key_Backtab)
    assert editor.toPlainText() == "item"

    window.open_path(str(plain))
    qapp.processEvents()
    assert window._current_kind == "text"
    assert window._edit_mode is True
    editor = window._editor
    editor.setFocus()
    _set_cursor(editor, 0)
    QTest.keyClick(editor, Qt.Key.Key_Tab)
    assert editor.toPlainText() == "\titem"
    _set_cursor(editor, len(editor.toPlainText()))
    QTest.keyClicks(editor, "(")
    assert editor.toPlainText() == "\titem("


def test_editor_status_tracks_cursor_count_kind_encoding_and_newline(
    make_workspace_window, tmp_path, qapp
):
    markdown = tmp_path / "status.md"
    plain = tmp_path / "status.txt"
    # Write bytes so Windows newline translation cannot turn this LF fixture
    # into CRLF before the status strip reads it.
    markdown.write_bytes("你好 world\nsecond".encode("utf-8"))
    plain.write_bytes(b"alpha\r\nbeta\r\n")
    window = make_workspace_window()
    window.show()

    _enter_markdown_editor(window, markdown)
    assert window._editor_status.full_status_text == (
        "第 1 行，第 1 欄｜4 字｜Markdown｜UTF-8｜LF"
    )
    _set_cursor(window._editor, len(window._editor.toPlainText()))
    assert "第 2 行，第 7 欄" in window._editor_status.full_status_text
    QTest.keyClicks(window._editor, " more")
    QTest.qWait(220)
    qapp.processEvents()
    assert window._editor_status.full_status_text == (
        "第 2 行，第 12 欄｜5 字｜Markdown｜UTF-8｜LF"
    )

    window.open_path(str(plain))
    qapp.processEvents()
    assert window._editor_status.full_status_text == (
        "第 1 行，第 1 欄｜2 字｜純文字｜UTF-8｜CRLF"
    )

    emoji = tmp_path / "emoji.txt"
    emoji.write_bytes("😀x".encode("utf-8"))
    window.open_path(str(emoji))
    cursor = window._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window._editor.setTextCursor(cursor)
    assert "第 1 行，第 3 欄" in window._editor_status.full_status_text


def test_encoding_fallback_refreshes_permanent_editor_status(
    make_workspace_window, tmp_path
):
    note = tmp_path / "big5.md"
    note.write_bytes("原始內容".encode("cp950"))
    window = make_workspace_window()
    _enter_markdown_editor(window, note)
    assert "CP950" in window._editor_status.full_status_text
    cursor = window._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText("😀")
    window._editor.setTextCursor(cursor)

    assert window._save_edits() is True

    assert note.read_text(encoding="utf-8") == "原始內容😀"
    assert "UTF-8" in window._editor_status.full_status_text


def test_closing_inactive_dirty_tab_prompts_for_that_tab_and_honors_cancel(
    make_workspace_window, tmp_path, monkeypatch
):
    dirty = tmp_path / "dirty.md"
    active = tmp_path / "active.md"
    dirty.write_text("dirty source", encoding="utf-8")
    active.write_text("active source", encoding="utf-8")
    window = make_workspace_window()

    _enter_markdown_editor(window, dirty)
    _append_one_undo_step(window._editor, " changed")
    dirty_document = window._editor.document()
    window.open_path(str(active))
    assert window._active_path == str(active)
    assert dirty_document.isModified()

    answers = [
        window_mod.QMessageBox.StandardButton.Cancel,
        window_mod.QMessageBox.StandardButton.Discard,
    ]
    prompts = []

    def question(*args, **kwargs):
        prompts.append(args)
        return answers.pop(0)

    monkeypatch.setattr(window_mod.QMessageBox, "question", question)

    assert window._close_tab_by_path(str(dirty)) is False
    assert window._index_of_path(str(dirty)) >= 0
    assert window._active_path == str(active)
    assert window._current_file == active
    assert "dirty.md" in prompts[0][2]

    assert window._close_tab_by_path(str(dirty)) is True
    assert window._index_of_path(str(dirty)) == -1
    assert window._active_path == str(active)
    assert window._current_file == active
    assert "dirty.md" in prompts[1][2]
