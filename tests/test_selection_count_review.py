"""Independent selection-count boundaries and real window switching checks."""

import time

import pytest
from PySide6.QtGui import QTextCursor, QTextDocument

from app import view_mode
from app.editor_status import EditorStatus, count_writing_units
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


def _finish(qapp, status):
    deadline = time.monotonic() + 5
    while status._selection_pending and time.monotonic() < deadline:
        qapp.processEvents()
    assert not status._selection_pending


@pytest.mark.parametrize("width", [4094, 4095, 4096, 4097])
@pytest.mark.parametrize("boundary", ["a-b", "a--b", "\U00020000", "\U0001f600"])
def test_source_selection_count_across_utf16_and_word_boundaries(qapp, width, boundary):
    selected = "a" * width + boundary + "\n[title](two/path.md)\n\u4e2d\u6587"
    document = QTextDocument("\U0001f600 " + selected + " excluded")
    cursor = QTextCursor(document)
    cursor.setPosition(3)
    cursor.setPosition(3 + len(selected.encode("utf-16-le")) // 2,
                       QTextCursor.MoveMode.KeepAnchor)
    status = EditorStatus()
    try:
        status.set_selection(cursor)
        assert status.selection_writing_unit_count is None
        _finish(qapp, status)
        assert status.selection_writing_unit_count == count_writing_units(selected)
        assert cursor.selectedText().replace("\u2029", "\n") == selected
        assert document.toPlainText() == "\U0001f600 " + selected + " excluded"
    finally:
        status.deleteLater()


def test_window_switch_drops_pending_selection_from_previous_document(
    qapp, make_window, md_files,  # noqa: F811
):
    window = make_window()
    window._open_file(str(md_files[0]))
    window._open_source_editor(view_mode.EDIT)
    window._editor.setPlainText("\u4e2d\u6587 " * 20000)
    previous = window._editor.document()
    cursor = window._editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    window._editor.setTextCursor(cursor)
    assert window._editor_status._selection_pending
    window._open_file(str(md_files[1]))
    window._open_source_editor(view_mode.EDIT)
    assert window._editor.document() is not previous
    qapp.processEvents()
    assert window._editor_status.selection_writing_unit_count is None
    cursor = window._editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
    window._editor.setTextCursor(cursor)
    _finish(qapp, window._editor_status)
    assert window._editor_status.selection_writing_unit_count == 1
    assert window._editor_status._selection_document is window._editor.document()
