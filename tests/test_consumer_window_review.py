"""Independent acceptance checks for upgraded search and relocation data flow."""

from PySide6.QtGui import QTextCursor

from app import edit_backend, file_browser, window as window_mod
from app.document_libraries import DocumentLibraryStore
from tests.test_editor_data_safety import (
    _DelayedSnapshotWysiwygView,
    _isolated_editor_dependencies,
    make_data_safety_window,
)
from tests.test_editor_workspace_integration import _enter_markdown_editor
from tests.test_recovery_startup_integration import choose_recovery
from tests.test_relocation_workspace import relocate, scenario


def save_snapshot(store, path, text, signature=None):
    store.save(path, text, encoding="utf-8", newline="\n", cursor=0, anchor=0,
               scroll=0, source_signature=signature)


def test_move_never_overwrites_a_different_orphan_draft_at_destination(
    make_data_safety_window, tmp_path,
):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    save_snapshot(window._recovery_store, new, "unrelated recoverable destination draft")
    _enter_markdown_editor(window, old)
    window._editor.insertPlainText("source unsaved changes\n")

    relocate(window, old, new)

    assert window._recovery_store.load(new).draft == "unrelated recoverable destination draft"
    assert old.exists() and not new.exists()
    assert window._active_path == str(old)


def test_inactive_dirty_move_preserves_active_document_and_target_buffer(
    make_data_safety_window, tmp_path,
):
    old, new = scenario(tmp_path)
    other = tmp_path / "active.md"
    other.write_text("# Other\n", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    moved_document = window._editor.document()
    window._editor.moveCursor(QTextCursor.MoveOperation.End)
    window._editor.insertPlainText("pending moved text")
    _enter_markdown_editor(window, other)
    active_document = window._editor.document()
    window._editor.insertPlainText("active changes\n")
    active_text = active_document.toPlainText()

    relocate(window, old, new)

    assert window._active_path == str(other)
    assert window._editor.document() is active_document
    assert active_document.toPlainText() == active_text
    assert active_document.isModified()
    assert window._tab_state[str(new)]["editor_document"] is moved_document
    assert moved_document.isModified()
    assert "../source/assets/note.txt" in moved_document.toPlainText()
    assert moved_document.toPlainText().endswith("pending moved text")
    assert window._recovery_store.load(new).draft == moved_document.toPlainText()
    assert window._recovery_store.load(old) is None


def test_deferred_draft_can_be_reviewed_after_relocation(
    make_data_safety_window, tmp_path, monkeypatch,
):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    save_snapshot(window._recovery_store, old, "[draft](assets/note.txt)", window._file_signature(old))
    dialogs = choose_recovery(monkeypatch, ["later", "restore"])
    window.open_path(str(old))
    assert window._tab_state[str(old)].get("pending_recovery")

    relocate(window, old, new)
    assert window._tab_state[str(new)].get("pending_recovery")
    window._review_recovery_path(str(new))

    assert len(dialogs) == 2
    assert window._editor.toPlainText() == "[draft](../source/assets/note.txt)"
    assert window._editor.document().isModified()
    assert not window._tab_state[str(new)].get("pending_recovery")
    assert window._recovery_store.load(old) is None
    assert window._recovery_store.load(new).signature_pair == window._file_signature(new)
    assert "[draft]" not in new.read_text(encoding="utf-8")


def test_office_search_waits_for_last_keystroke_then_selects_requested_txt_line(
    make_data_safety_window, tmp_path, monkeypatch,
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    old, _new = scenario(tmp_path)
    target = tmp_path / "result.txt"
    target.write_text("needle first\nother\nneedle second\n", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SOURCE_BACKEND
    _enter_markdown_editor(window, old)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("last unsent Office keystroke")

    window._open_global_search_result(str(target), "needle", 3)
    assert window._active_path == str(old)
    view.deliver_snapshot()
    assert window._active_path == str(old)
    view.deliver_acknowledgement()

    assert window._active_path == str(target)
    assert window._editor.textCursor().blockNumber() == 2
    assert window._editor.textCursor().selectedText() == "needle"
    saved_state = window._tab_state[str(old)]
    assert saved_state["editor_document"].toPlainText() == "last unsent Office keystroke"
    assert saved_state["editor_document"].isModified()
    assert "last unsent" not in old.read_text(encoding="utf-8")


def test_preview_search_requests_selected_source_block_even_if_query_is_unchanged(
    make_data_safety_window, tmp_path,
):
    source = tmp_path / "duplicate.markdown"
    source.write_text("needle first\n\nneedle second\n", encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(source))
    requested = []
    window._renderer.reveal_source_line_after_load = requested.append

    window._open_global_search_result(str(source), "needle", 1)
    window._open_global_search_result(str(source), "needle", 3)

    assert requested == [1, 3]
    assert window._active_path == str(source)


def test_real_file_browser_relocation_hook_waits_for_office_snapshot(
    make_data_safety_window, tmp_path, monkeypatch,
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    store = DocumentLibraryStore(tmp_path / "browser-libraries.json")
    monkeypatch.setattr(file_browser, "DocumentLibraryStore", lambda: store)
    monkeypatch.setattr(file_browser, "load_excluded_folders", lambda: [])
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    window._toggle_edit_backend()
    office = window._wysiwyg_view
    office.edit_without_push("[latest](assets/note.txt)")
    browser = file_browser.FileBrowserView(lambda _path: None)
    browser.on_document_relocation = window._request_document_relocation
    browser.on_paths_migrated = window._on_browser_paths_migrated

    browser._relocate_document(old, new, "Move")
    assert old.exists() and not new.exists()
    office.deliver_snapshot()
    assert old.exists() and not new.exists()
    office.deliver_acknowledgement()

    assert not old.exists() and new.exists()
    assert window._active_path == str(new)
    assert window._tab_state[str(new)]["editor_document"].toPlainText() == "[latest](../source/assets/note.txt)"
    browser.close()
