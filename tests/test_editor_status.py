"""Tests for the independent editor status strip."""

import pytest

from app.editor_status import EditorStatus, count_writing_units
from app.theme import DARK, LIGHT


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("", 0),
        ("你好 world", 3),
        ("Hello, world! 2026", 3),
        ("note-taking isn't two", 3),
        ("**粗體** and `code`", 4),
        ("測試 OpenAI 筆記 123", 6),
        ("𠀀 extension", 2),
        ("😊 ，。---", 0),
    ),
)
def test_count_writing_units_has_defined_mixed_language_semantics(text, expected):
    assert count_writing_units(text) == expected


def test_ascii_count_preserves_unicode_path_word_boundaries():
    from itertools import product

    # A trailing emoji contributes no units and selects the Unicode path.
    # Cover connectors at edges, doubled connectors, separators, and controls.
    alphabet = "aZ0'-_ \n\x00"
    for length in range(5):
        for characters in product(alphabet, repeat=length):
            text = "".join(characters)
            assert count_writing_units(text) == count_writing_units(text + "😀"), repr(text)
    for codepoint in range(128):
        text = "word" + chr(codepoint) + "123"
        assert count_writing_units(text) == count_writing_units(text + "😀")


def test_long_connector_chain_count_has_bounded_auxiliary_memory():
    import tracemalloc

    text = "a-" * 500_000 + "a"
    tracemalloc.start()
    try:
        assert count_writing_units(text) == 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_set_document_state_displays_complete_markdown_status(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.show()
        status.set_document_state(
            line=12,
            column=8,
            text="你好 world",
            document_kind="markdown",
            encoding="utf-8",
            newline="\n",
        )
        qapp.processEvents()

        expected = "第 12 行，第 8 欄｜3 字｜Markdown｜UTF-8｜LF"
        assert status.full_status_text == expected
        assert status.label.text() == expected
        assert status.writing_unit_count == 3
        assert status.toolTip() == status.label.toolTip() == expected
        assert expected in status.accessibleName()
        assert status.accessibleName() == status.label.accessibleName()
    finally:
        status.close()


def test_markdown_status_counts_visible_labels_not_hidden_resource_paths(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.set_document_state(
            line=1,
            column=1,
            text=(
                "![說明](assets/coil%20geometry.png) "
                "[OpenAI](https://openai.com/docs)"
            ),
            document_kind="markdown",
            encoding="utf-8",
            newline="\n",
        )

        assert status.writing_unit_count == 3
    finally:
        status.close()


def test_text_crlf_and_encoding_are_normalized(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.show()
        status.set_document_state(
            line=0,
            column=-4,
            text="plain text",
            document_kind="txt",
            encoding="utf_16_le",
            newline="CRLF",
        )
        qapp.processEvents()

        assert status.full_status_text == (
            "第 1 行，第 1 欄｜2 字｜純文字｜UTF-16-LE｜CRLF"
        )
    finally:
        status.close()


def test_cursor_only_update_keeps_document_metadata_and_count(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(760, status.height())
        status.set_document_state(
            line=1,
            column=1,
            text="你好 note",
            document_kind="text",
            encoding="big5",
            newline="\r\n",
        )
        status.set_cursor_position(line=7, column=3)
        status.show()
        qapp.processEvents()

        assert status.full_status_text == (
            "第 7 行，第 3 欄｜3 字｜純文字｜BIG5｜CRLF"
        )
    finally:
        status.close()


def test_narrow_status_compacts_without_losing_full_tooltip(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(155, status.height())
        status.show()
        status.set_document_state(
            line=1234,
            column=88,
            text="你好 mixed English words 2026",
            document_kind="markdown",
            encoding="utf-8-sig",
            newline="\r\n",
        )
        qapp.processEvents()

        assert status.label.text() != status.full_status_text
        assert status.label.toolTip() == status.full_status_text
        assert (
            status.label.fontMetrics().horizontalAdvance(status.label.text())
            <= status.label.contentsRect().width()
        )
        assert status.label.geometry().right() <= status.rect().right()
    finally:
        status.close()


def test_light_and_dark_themes_render_and_retain_state(qapp):
    status = EditorStatus(LIGHT)
    try:
        status.resize(620, status.height())
        status.set_document_state(
            line=2,
            column=5,
            text="深淺 dark theme",
            document_kind="md",
            encoding="utf-8",
            newline="LF",
        )
        status.show()
        qapp.processEvents()
        light_style = status.styleSheet()
        assert LIGHT.surface_alt in light_style
        assert LIGHT.text_muted in light_style
        assert status.grab().isNull() is False

        full_text = status.full_status_text
        status.apply_theme(DARK)
        qapp.processEvents()
        dark_style = status.styleSheet()
        assert dark_style != light_style
        assert DARK.surface_alt in dark_style
        assert DARK.text_muted in dark_style
        assert status.full_status_text == full_text
        assert status.grab().isNull() is False
    finally:
        status.close()


def test_unknown_document_kind_is_rejected(qapp):
    status = EditorStatus(LIGHT)
    try:
        with pytest.raises(ValueError, match="Unsupported document kind"):
            status.set_document_state(
                line=1,
                column=1,
                text="",
                document_kind="pdf",
                encoding="utf-8",
                newline="LF",
            )
    finally:
        status.close()


def test_counter_tracks_utf16_edits_undo_and_document_replacement(qapp):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app.editor_status import DocumentWritingCounter, _visible_markdown_text

    editor = QPlainTextEdit()
    original = "你好😀 world\n[文件](long/path.md)\n尾段"
    editor.setPlainText(original)
    counter = DocumentWritingCounter.for_document(editor.document(), markdown=True)

    def check():
        assert counter.writing_units == count_writing_units(
            _visible_markdown_text(editor.toPlainText())
        )

    check()
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    cursor.insertText("新😀\n另一段")
    check()
    editor.undo()
    check()
    assert editor.toPlainText() == original
    editor.redo()
    check()
    editor.setPlainText("replacement\n![圖](assets/image.png)")
    check()
    editor.clear()
    check()
    editor.close()


def test_counter_local_edit_does_not_recount_unchanged_document(qapp):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app.editor_status import DocumentWritingCounter

    editor = QPlainTextEdit()
    editor.setPlainText("[說明](assets/test.md) 中英 mixed 😀\n" * 3000)
    counter = DocumentWritingCounter.for_document(editor.document(), markdown=True)
    assert not counter.uses_full_fallback
    before = counter.writing_units
    recounted = counter.blocks_recounted
    cursor = QTextCursor(editor.document().findBlockByNumber(1500))
    cursor.insertText("新增 ")
    assert counter.writing_units == before + 2
    assert counter.blocks_recounted - recounted <= 2
    assert not counter.uses_full_fallback
    editor.close()


@pytest.mark.parametrize(
    "text",
    [
        "[visible\nlabel](hidden/path.md)",
        "![label](assets/\nverylongfilename.png)",
        "[[hidden\npath|visible text]]",
        '<span\nclass="hidden words">visible</span>',
        "[unfinished\n\nthen label](hidden/path.md)",
        "[x] task\n[full](path)\nplain",
        "```md\n[code](literal/words.md)\n```\n[visible](hidden/file.md)",
        "````md\n```\n[code](literal/words.md)\n````",
        "~~~md\n```\n[code](literal/words.md)\n~~~",
    ],
)
def test_incremental_count_preserves_multiline_markup_and_fences(qapp, text):
    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app.editor_status import DocumentWritingCounter, _visible_markdown_text

    editor = QPlainTextEdit()
    editor.setPlainText(text)
    counter = DocumentWritingCounter.for_document(editor.document(), markdown=True)
    assert counter.writing_units == count_writing_units(_visible_markdown_text(text))
    cursor = QTextCursor(editor.document())
    cursor.insertText("```\n")
    assert counter.writing_units == count_writing_units(
        _visible_markdown_text(editor.toPlainText())
    )
    editor.undo()
    assert counter.writing_units == count_writing_units(_visible_markdown_text(text))
    editor.close()


def test_status_document_api_avoids_plain_text_snapshot(qapp, monkeypatch):
    from PySide6.QtWidgets import QPlainTextEdit

    editor = QPlainTextEdit()
    editor.setPlainText("你好 world")
    status = EditorStatus()
    # Restore the patched native method while its owning editor is alive.
    with monkeypatch.context() as patch:
        patch.setattr(editor.document(), "toPlainText", lambda: pytest.fail("full snapshot"))
        status.set_document_state(
            line=1, column=1, document=editor.document(), document_kind="markdown",
            encoding="utf-8", newline="\n",
        )
        assert status.writing_unit_count == 3
    editor.close()
    status.close()


@pytest.mark.parametrize("markdown", [False, True])
def test_counter_random_edit_sequences_match_complete_recount(qapp, markdown):
    import random

    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app.editor_status import DocumentWritingCounter, _visible_markdown_text
    from app.text_positions import py_to_qt_position

    rng = random.Random(22102026)
    editor = QPlainTextEdit()
    editor.setPlainText(
        "起點😀 [label](hidden/file.md)\n```md\n[code](literal/file)\n```\n"
        "[[target|名稱]]\n<span>text</span>\n尾段"
    )
    counter = DocumentWritingCounter.for_document(editor.document(), markdown=markdown)
    fragments = ["新增😀", "\n", "[", "](", ")", "```\n", "~~~", "<tag>", "", "𠀀"]
    for index in range(250):
        text = editor.toPlainText()
        start = rng.randrange(len(text) + 1)
        end = min(len(text), start + rng.randrange(9))
        cursor = QTextCursor(editor.document())
        cursor.setPosition(py_to_qt_position(text, start))
        cursor.setPosition(py_to_qt_position(text, end), QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(rng.choice(fragments))
        if index % 7 == 0:
            editor.undo()
        if index % 14 == 0:
            editor.redo()
        text = editor.toPlainText()
        visible = _visible_markdown_text(text) if markdown else text
        assert counter.writing_units == count_writing_units(visible), index
    editor.close()


def test_background_statistics_restart_on_edit_and_ignore_inactive_document(qapp):
    import time

    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app.editor_status import DocumentWritingCounter

    editor = QPlainTextEdit()
    editor.setPlainText("長文 word\n" * 20000)
    status = EditorStatus()
    kwargs = dict(line=1, column=1, document_kind="markdown", encoding="utf-8", newline="\n")
    status.set_document_state(document=editor.document(), **kwargs)
    counter = DocumentWritingCounter.for_document(editor.document(), markdown=True)
    assert not counter.ready
    assert "統計中" in status.full_status_text
    cursor = QTextCursor(editor.document())
    cursor.insertText("新增 ")
    deadline = time.monotonic() + 5
    while not counter.ready and time.monotonic() < deadline:
        qapp.processEvents()
    assert counter.ready
    assert status.writing_unit_count == 60002
    assert "統計中" not in status.full_status_text

    # A large paste starts another background pass. Completing it after a tab
    # switch must not overwrite the new document's visible count.
    cursor.insertText("paste\n" * 2000)
    assert not counter.ready
    other = QPlainTextEdit()
    other.setPlainText("短文")
    status.set_document_state(document=other.document(), **kwargs)
    while not counter.ready and time.monotonic() < deadline:
        qapp.processEvents()
    assert counter.ready
    assert status.writing_unit_count == 2
    editor.close()
    other.close()
    status.close()


def _finish_selection_count(qapp, status):
    import time

    deadline = time.monotonic() + 5
    while status._selection_pending and time.monotonic() < deadline:
        qapp.processEvents()
    assert not status._selection_pending


def test_selection_units_preserve_mixed_language_contract_and_clear_without_placeholder(qapp):
    from PySide6.QtGui import QTextCursor, QTextDocument

    document = QTextDocument("你好😀 note-taking isn't 2026\n[標籤](path/file.md)")
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    status = EditorStatus()
    status.set_selection(cursor)
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == count_writing_units(cursor.selectedText())
    assert f"選取 {status.selection_writing_unit_count} 字" in status.full_status_text
    assert "依來源文字" in status.toolTip()
    assert "選取" in status.label.accessibleName()

    # Selected emoji/punctuation is a real zero-unit selection, not no selection.
    cursor.setPosition(2)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    status.set_selection(cursor)
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == 0
    assert "選取 0 字" in status.full_status_text
    cursor.clearSelection()
    status.set_selection(cursor)
    assert status.selection_writing_unit_count is None
    assert "選取" not in status.full_status_text
    status.deleteLater()


def test_large_selection_is_chunked_exact_and_cancels_stale_revision(qapp, monkeypatch):
    from PySide6.QtGui import QTextCursor, QTextDocument

    from app import editor_status

    # Cross chunk boundaries inside long words, connectors and UTF-16 pairs.
    text = ("a" * 4093 + "𠀀😀b-c isn't\n" + "中文 " * 1500) * 80
    expected = count_writing_units(text)
    document = QTextDocument(text)
    monkeypatch.setattr(document, "toPlainText", lambda: pytest.fail("full snapshot"))
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    sizes = []
    original = editor_status.count_writing_units

    def counted(fragment):
        sizes.append(len(fragment))
        return original(fragment)

    monkeypatch.setattr(editor_status, "count_writing_units", counted)
    status = EditorStatus()
    status.set_selection(cursor)
    assert sizes == []  # Selection changes never synchronously copy/count the range.
    status._count_selection_slice()
    assert status._selection_pending
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == expected
    assert max(sizes) <= 4098
    assert len(sizes) > 100

    status.clear_selection()
    status.set_selection(cursor)
    edit = QTextCursor(document)
    edit.insertText("changed ")
    status._count_selection_slice()
    assert status.selection_writing_unit_count is None
    assert "選取" not in status.full_status_text
    status.deleteLater()


def test_selection_replaced_or_document_destroyed_cannot_publish_old_count(qapp):
    from PySide6.QtGui import QTextCursor, QTextDocument
    from shiboken6 import delete

    document = QTextDocument("long selection " * 20000)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    status = EditorStatus()
    status.set_selection(cursor)
    status._count_selection_slice()
    cursor.setPosition(0)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    status.set_selection(cursor)
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == 1
    cursor.select(QTextCursor.SelectionType.Document)
    status.set_selection(cursor)
    delete(document)
    status._count_selection_slice()
    assert status.selection_writing_unit_count is None
    assert "選取" not in status.full_status_text
    status.deleteLater()


def test_window_selection_route_handles_anchor_change_and_never_reports_hidden_office(qapp):
    from types import SimpleNamespace

    from PySide6.QtGui import QTextCursor
    from PySide6.QtWidgets import QPlainTextEdit

    from app import edit_backend
    from app.window import MainWindow

    editor = QPlainTextEdit()
    editor.setPlainText("one two 三")
    editor.is_composing = lambda: False
    status = EditorStatus()
    owner = SimpleNamespace(
        _editor=editor, _editor_status=status, _edit_mode=True,
        _current_kind="markdown", _active_edit_backend=edit_backend.SPLIT_BACKEND,
    )
    editor.selectionChanged.connect(lambda: MainWindow._update_editor_selection_status(owner))
    editor.textChanged.connect(lambda: MainWindow._update_editor_selection_status(owner))
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == 2
    # Position stays at 7: cursorPositionChanged alone cannot see this change.
    cursor.setPosition(4)
    cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    _finish_selection_count(qapp, status)
    assert status.selection_writing_unit_count == 1
    owner._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    MainWindow._update_editor_selection_status(owner)
    assert status.selection_writing_unit_count is None
    owner._active_edit_backend = edit_backend.SPLIT_BACKEND
    editor.is_composing = lambda: True
    MainWindow._update_editor_selection_status(owner)
    assert "選取" not in status.full_status_text
    editor.deleteLater()
    status.deleteLater()
