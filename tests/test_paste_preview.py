"""Clipboard review preserves selection, full payload, and file ownership."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QObject, QTimer, QUrl
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication

from app import paste_preview
from app.editor import EditorView
from app.paste_preview import PasteChoice, TablePastePreviewDialog, parse_clipboard_table


@pytest.fixture(autouse=True)
def release_native_clipboard(qapp):
    yield
    # PySide6 6.11 offscreen teardown can destroy retained QMimeData twice.
    qapp.clipboard().clear()


def _mime(text):
    mime = QMimeData()
    mime.setText(text)
    return mime


def _select(editor, start, end):
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


def test_tsv_quotes_newlines_and_markdown_characters_are_preserved():
    from markdown_it import MarkdownIt

    source = 'Name\tValue\r\n"two\nlines"\t"a|b <script> **bold** &copy;"\r\n'
    table = parse_clipboard_table(source)
    assert table is not None
    assert table.rows[1] == ("two\nlines", "a|b <script> **bold** &copy;")
    rendered = MarkdownIt("commonmark").enable("table").render(table.markdown())
    assert "two<br>lines" in rendered
    assert "<script>" not in rendered and "<strong>" not in rendered
    assert "a|b &lt;script&gt; **bold** &amp;copy;" in rendered
    assert table.source == source


def test_ordinary_ragged_and_single_row_text_are_not_tables():
    for text in ("普通文字\n第二行", "a\tb", "a\tb\nc", 'a\tb\n"unclosed\tx'):
        assert parse_clipboard_table(text) is None


def test_preview_limits_display_but_inserts_complete_table(qapp):
    from markdown_it import MarkdownIt

    source = "\n".join("\t".join(f"row{r}-col{c}" for c in range(25)) for r in range(75))
    table = parse_clipboard_table(source)
    dialog = TablePastePreviewDialog(table)
    assert dialog.preview.rowCount() == 50
    assert dialog.preview.columnCount() == 20
    dialog.insert_button.click()
    choice = dialog.choice()
    assert choice.mode == "table"
    assert "row74-col24" in MarkdownIt("commonmark").enable("table").render(choice.text)
    assert choice.text.count("\n") == 75
    dialog.deleteLater()


def test_source_native_paste_reviews_at_selection_and_undoes_once(qapp):
    editor = EditorView()
    editor.set_content("前😀選取後")
    _select(editor, 3, 5)
    original_selection = editor.textCursor().selectedText()
    QApplication.clipboard().setMimeData(_mime("名稱\t數量\n蘋果\t3"))
    seen = []

    def accept_dialog():
        dialog = next(w for w in QApplication.topLevelWidgets()
                      if isinstance(w, TablePastePreviewDialog) and w.isVisible())
        seen.append(dialog)
        dialog.insert_button.click()

    QTimer.singleShot(0, accept_dialog)
    editor.paste()
    assert seen and original_selection == "選取"
    assert editor.toPlainText() == "前😀\n\n| 名稱 | 數量 |\n| --- | --- |\n| 蘋果 | 3 |\n\n後"
    editor.undo()
    assert editor.toPlainText() == "前😀選取後"
    editor.deleteLater()


def test_source_plain_choice_and_cancel_preserve_original_text(qapp, monkeypatch):
    editor = EditorView()
    editor.set_content("before selected after")
    _select(editor, 7, 15)
    source = "a\tb\r\nc\td"
    monkeypatch.setattr(paste_preview, "choose_table_paste", lambda *_: PasteChoice("cancel"))
    editor.insertFromMimeData(_mime(source))
    assert editor.toPlainText() == "before selected after"
    assert editor.textCursor().selectedText() == "selected"
    monkeypatch.setattr(
        paste_preview, "choose_table_paste", lambda *_: PasteChoice("plain", source),
    )
    editor.insertFromMimeData(_mime(source))
    assert editor.toPlainText() == "before a\tb\nc\td after"
    editor.undo()
    assert editor.toPlainText() == "before selected after"
    editor.deleteLater()


def test_source_document_change_during_review_rejects_stale_paste(qapp, monkeypatch):
    editor = EditorView()
    editor.set_content("old")
    messages = []
    editor.image_status.connect(messages.append)

    def changed(*_args):
        editor.set_content("reloaded")
        return PasteChoice("table", "unwanted table")

    monkeypatch.setattr(paste_preview, "choose_table_paste", changed)
    editor.insertFromMimeData(_mime("a\tb\nc\td"))
    assert editor.toPlainText() == "reloaded"
    assert messages == ["文件已變更，請重新貼上。"]
    editor.deleteLater()


@pytest.mark.parametrize("change", ["destroyed", "clipboard", "save_as", "resource"])
def test_source_resource_review_rejects_invalidated_editor_mime_or_file(
    qapp, tmp_path, monkeypatch, change,
):
    from shiboken6 import delete, isValid

    note = tmp_path / "notes" / "note.md"
    note.parent.mkdir()
    note.write_text("selected", encoding="utf8")
    resource = tmp_path / "document.pdf"
    resource.write_bytes(b"original")
    editor = EditorView()
    editor.set_document_path(note)
    editor.set_content("selected")
    editor.selectAll()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(resource))])
    QApplication.clipboard().setMimeData(mime)

    def changed(*_args):
        if change == "destroyed":
            delete(editor)
        elif change == "clipboard":
            QApplication.clipboard().setMimeData(_mime("replaced"))
            assert not isValid(mime)
        elif change == "save_as":
            editor.set_document_path(tmp_path / "other.md")
        else:
            resource.write_bytes(b"changed during preview")
        return True

    monkeypatch.setattr(paste_preview, "confirm_resource_paste", changed)
    editor.insertFromMimeData(mime)
    assert not (note.parent / "assets").exists()
    assert not (tmp_path / "assets").exists()
    if isValid(editor):
        assert editor.toPlainText() == "selected"
        editor.deleteLater()


def test_ime_and_plain_text_mode_do_not_open_table_review(qapp, monkeypatch):
    editor = EditorView()
    calls = []
    monkeypatch.setattr(paste_preview, "choose_table_paste", lambda *_: calls.append(True))
    editor.inputMethodEvent(QInputMethodEvent("候選", []))
    assert editor.is_composing()
    editor.insertFromMimeData(_mime("a\tb\nc\td"))
    assert not calls
    editor.inputMethodEvent(QInputMethodEvent("", []))
    editor.set_plain_text_mode(True)
    editor.insertFromMimeData(_mime("a\tb\nc\td"))
    assert not calls
    editor.deleteLater()


def test_attachment_preview_cancel_creates_nothing_and_accept_replaces_selection(
    qapp, tmp_path, monkeypatch,
):
    document = tmp_path / "notes" / "note.md"
    document.parent.mkdir()
    document.write_text("selected", encoding="utf8")
    resource = tmp_path / "guide.pdf"
    resource.write_bytes(b"preserved attachment")
    editor = EditorView()
    editor.set_document_path(document)
    editor.set_content("selected")
    editor.selectAll()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(resource))])
    calls = []

    def review(paths, target, parent):
        calls.append(([Path(path) for path in paths], Path(target), parent))
        return False

    monkeypatch.setattr(paste_preview, "confirm_resource_paste", review)
    editor.insertFromMimeData(mime)
    assert calls == [([resource], document, editor)]
    assert editor.toPlainText() == "selected"
    assert not (document.parent / "assets").exists()
    monkeypatch.setattr(paste_preview, "confirm_resource_paste", lambda *_: True)
    editor.insertFromMimeData(mime)
    assert editor.toPlainText() == "[guide.pdf](assets/guide.pdf)"
    assert (document.parent / "assets" / "guide.pdf").read_bytes() == resource.read_bytes()
    editor.undo()
    assert editor.toPlainText() == "selected"
    editor.deleteLater()


@pytest.mark.parametrize("outcome", ["table", "plain", "cancel", "ordinary", "switch", "close"])
def test_mainwindow_office_route_resolves_review_or_cancels_stale_context(
    qapp, monkeypatch, outcome,
):
    from app import edit_backend
    from app.window import MainWindow

    resolutions, messages = [], []

    def resolve(token, generation, mode, text, callback):
        resolutions.append((token, generation, mode, text))
        callback(True)

    view = SimpleNamespace(resolve_paste_preview=resolve)
    owner = SimpleNamespace(
        _wysiwyg_view=view, _active_path="note.md", _edit_mode=True,
        _active_edit_backend=edit_backend.WYSIWYG_BACKEND,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: messages.append(args)),
    )

    def choose(text, parent):
        assert text == "a\tb\nc\td" and parent is owner
        if outcome == "switch":
            owner._active_path = "other.md"
        if outcome == "close":
            owner._wysiwyg_view = None
            owner._edit_mode = False
        if outcome == "ordinary":
            return None
        mode = outcome if outcome in {"table", "plain", "cancel"} else "table"
        return PasteChoice(mode, "chosen")

    monkeypatch.setattr(paste_preview, "choose_table_paste", choose)
    MainWindow._on_wysiwyg_paste_preview(owner, "a\tb\nc\td", 17, 3)
    expected_mode = "cancel" if outcome in {"switch", "close"} else outcome
    expected_text = "" if expected_mode == "cancel" and outcome != "cancel" else "chosen"
    if outcome == "ordinary":
        expected_mode, expected_text = "plain", "a\tb\nc\td"
    assert resolutions == [(17, 3, expected_mode, expected_text)]
    assert not messages


@pytest.mark.parametrize("decision", [
    "accept", "cancel", "clipboard", "changed", "closed", "stale", "failed",
    "success_then_switch", "destroyed_after_dispatch",
])
def test_mainwindow_attachment_preview_validates_clipboard_and_copies_only_after_acceptance(
    qapp, tmp_path, monkeypatch, decision,
):
    from app import edit_backend
    from app.window import MainWindow

    note = tmp_path / "notes" / "note.md"
    note.parent.mkdir()
    note.write_text("before selected after", encoding="utf8")
    source = tmp_path / "document.pdf"
    source.write_bytes(b"original attachment")

    def clipboard(path):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        QApplication.clipboard().setMimeData(mime)

    clipboard(source)
    resolved, recent, messages = [], [], []

    def resolve(token, generation, mode, text="", callback=None):
        resolved.append((mode, text))
        if mode == "markdown" and decision == "destroyed_after_dispatch":
            from shiboken6 import delete

            delete(view)
            return
        if mode == "markdown" and decision == "success_then_switch":
            owner._active_path = "switched.md"
        if callback:
            callback(decision != "failed")

    view = QObject()
    view._generation = 7
    view.resolve_paste_preview = resolve
    view.validate_paste_preview = lambda token, generation, callback: callback(decision != "stale")
    owner = SimpleNamespace(
        _wysiwyg_view=view, _current_file=note, _active_path=str(note), _edit_mode=True,
        _active_edit_backend=edit_backend.WYSIWYG_BACKEND, _resources_available=lambda: True,
        _record_recent_resource=recent.append,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: messages.append(args)),
    )

    def preview(paths, document_path, parent):
        assert paths == [source] and document_path == note and parent is owner
        assert not (note.parent / "assets").exists()
        if decision == "clipboard":
            other = tmp_path / "other" / source.name
            other.parent.mkdir()
            other.write_bytes(b"different file with same name")
            clipboard(other)
        if decision == "changed":
            source.write_bytes(b"changed since preview")
        if decision == "closed":
            owner._wysiwyg_view = None
        return decision != "cancel"

    monkeypatch.setattr(paste_preview, "confirm_resource_paste", preview)
    MainWindow._on_wysiwyg_resource_paste(owner, json.dumps([source.name]), 19, 7)
    target = note.parent / "assets" / source.name
    if decision in {"accept", "failed", "success_then_switch", "destroyed_after_dispatch"}:
        assert target.read_bytes() == source.read_bytes()
        assert resolved == [("markdown", "[document.pdf](assets/document.pdf)")]
        assert recent == (["[document.pdf](assets/document.pdf)"] if decision == "accept" else [])
    else:
        assert not target.exists()
        assert recent == []
    assert note.read_text(encoding="utf8") == "before selected after"


def test_clipboard_file_names_must_match_native_urls(qapp, tmp_path):
    file = tmp_path / "real.pdf"
    file.write_bytes(b"pdf")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(file))])
    with pytest.raises(ValueError, match="來源已變更"):
        paste_preview.resource_clipboard_snapshot(mime, ["different.pdf"])
