"""Writing outline source fidelity, folding, and one-step heading edits."""

import time

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QTextCursor

from app.editor import EditorView
from app.writing_outline import (
    WritingOutlineController,
    WritingOutlineDialog,
    extract_writing_headings,
)


@pytest.fixture
def editor(qapp):
    widget = EditorView()
    widget.resize(720, 500)
    widget.show()
    yield widget
    assert QThreadPool.globalInstance().waitForDone(5000)
    qapp.processEvents()
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_heading_maps_exclude_code_frontmatter_quote_and_html():
    text = (
        "---\ntitle: test\n# yaml\n---\n# 主章😀\nbody\n"
        "````md\n# code\n```\n## still code\n````\n"
        "> # quote\n\n<div>\n# html\n</div>\n\n"
        "子章𠀀\nsecond line\n---\nbody\n# 尾章"
    )
    headings = extract_writing_headings(text)
    assert [(heading.level, heading.title) for heading in headings] == [
        (1, "主章😀"), (2, "子章𠀀 second line"), (1, "尾章"),
    ]
    assert headings[0].section_end_line == headings[-1].line
    assert headings[1].setext
    assert text.splitlines()[headings[1].content_end_line - 1] == "---"


def test_nested_folding_keeps_source_undo_and_search_navigation(editor):
    original = "# 主章\n本文😀\n## 子章\n子內容\n# 尾章\n尾內容"
    editor.set_content(original)
    controller = WritingOutlineController(editor)
    controller.refresh_now()
    try:
        controller.toggle_fold(1)
        controller.toggle_fold(0)
        assert not editor.document().findBlockByNumber(2).isVisible()
        assert not editor.document().findBlockByNumber(3).isVisible()
        assert editor.document().findBlockByNumber(4).isVisible()
        controller.toggle_fold(0)
        assert editor.document().findBlockByNumber(2).isVisible()
        assert not editor.document().findBlockByNumber(3).isVisible()
        assert editor.toPlainText() == original
        assert not editor.document().isModified()
        assert not editor.document().isUndoAvailable()
        # Finding or navigating to hidden text must reveal its containing section.
        editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(3)))
        assert editor.document().findBlockByNumber(3).isVisible()
        controller.jump_to_heading(2)
        assert editor.textCursor().blockNumber() == 4
        assert not editor.grab().isNull()
    finally:
        controller.close()


@pytest.mark.parametrize(
    ("original", "expected"),
    [("# 主章😀\nbody", "## 主章😀\nbody"),
     ("主章😀\n第二行\n===\nbody", "## 主章😀 第二行\nbody")],
)
def test_heading_level_change_is_one_unicode_safe_undo(editor, original, expected):
    editor.set_content(original)
    controller = WritingOutlineController(editor)
    controller.refresh_now()
    try:
        assert controller.change_heading_level(0, 1)
        assert editor.toPlainText() == expected
        editor.undo()
        assert editor.toPlainText() == original
        editor.redo()
        assert editor.toPlainText() == expected
    finally:
        controller.close()


def test_document_switch_restores_old_folds_and_background_result_is_current(editor, qapp):
    old = editor.create_buffer_document("# old\nold body")
    editor.use_buffer_document(old, plain_text_mode=False, document_path=None)
    controller = WritingOutlineController(editor)
    controller.refresh_now()
    controller.toggle_fold(0)
    new = editor.create_buffer_document("# new\nnew body")
    editor.use_buffer_document(new, plain_text_mode=False, document_path=None)
    assert old.findBlockByNumber(1).isVisible()
    controller._start_refresh()
    deadline = time.monotonic() + 5
    while (not controller.headings or controller._running) and time.monotonic() < deadline:
        qapp.processEvents()
    assert [heading.title for heading in controller.headings] == ["new"]
    controller.close()


def test_modeless_outline_smoke(editor, qapp):
    editor.set_content("# 主章\nbody\n## 子章")
    dialog = WritingOutlineDialog(editor)
    dialog.controller.refresh_now()
    dialog.show()
    qapp.processEvents()
    assert dialog.tree.topLevelItemCount() == 1
    assert dialog.tree.topLevelItem(0).childCount() == 1
    assert not dialog.grab().isNull()
    dialog.close()
