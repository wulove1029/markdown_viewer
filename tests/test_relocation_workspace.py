"""Real temporary files plus isolated windows for relocation integration."""
from pathlib import Path

from PySide6.QtGui import QTextCursor

from app import edit_backend, file_ops, window as window_mod
from tests.test_editor_data_safety import (
    _isolated_editor_dependencies,
    make_data_safety_window,
    _DelayedSnapshotWysiwygView,
)
from tests.test_editor_workspace_integration import _enter_markdown_editor


def scenario(tmp_path):
    folder = tmp_path / "source"
    folder.mkdir()
    (folder / "assets").mkdir()
    (folder / "assets" / "note.txt").write_text("attachment", encoding="utf-8")
    old = folder / "note.md"
    old.write_text("# Note\n\n[file](assets/note.txt)\n", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    return old, destination / old.name


def relocate(window, old, new):
    def commit():
        mapping = file_ops.rename_document(old, new)
        window._on_browser_paths_migrated(mapping)
    window._request_document_relocation(old, new, commit)


def test_dirty_source_move_rebases_live_text_and_next_save(make_data_safety_window, tmp_path):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    document = window._editor.document()
    window._editor.moveCursor(QTextCursor.MoveOperation.End)
    window._editor.insertPlainText("unsaved tail")
    relocate(window, old, new)
    state = window._tab_state[str(new)]
    assert state["editor_document"] is document
    assert document.isModified()
    assert "../source/assets/note.txt" in document.toPlainText()
    assert document.toPlainText().endswith("unsaved tail")
    assert "unsaved tail" not in new.read_text(encoding="utf-8")
    assert not old.exists()
    assert window._recovery_store.load(new).draft == document.toPlainText()
    assert window._recovery_store.load(old) is None
    assert window._save_tab_buffer(str(new))
    assert new.read_text(encoding="utf-8").endswith("unsaved tail")
    assert (new.parent / "../source/assets/note.txt").read_text(encoding="utf-8") == "attachment"


def test_office_move_waits_for_latest_snapshot_before_touching_disk(
    make_data_safety_window, tmp_path, monkeypatch,
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, old)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("[latest](assets/note.txt)\nlast keystroke")
    relocate(window, old, new)
    assert old.exists() and not new.exists()
    view.deliver_snapshot()
    assert old.exists() and not new.exists()
    view.deliver_acknowledgement()
    assert not old.exists() and new.exists()
    state = window._tab_state[str(new)]
    assert state["editor_document"].toPlainText() == "[latest](../source/assets/note.txt)\nlast keystroke"
    assert state["editor_document"].isModified()
    assert Path(view.document_path) == new


def test_unsupported_unsaved_links_refuse_before_move(make_data_safety_window, tmp_path):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    window._editor.insertPlainText("[[unresolved note]]\n")
    before = window._editor.toPlainText()
    relocate(window, old, new)
    assert old.exists() and not new.exists()
    assert window._editor.toPlainText() == before
    assert window._active_path == str(old)


def test_unopened_recovery_snapshot_migrates_without_saving_draft_to_source(
    make_data_safety_window, tmp_path,
):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    window._recovery_store.save(old, "[draft](assets/note.txt)", encoding="utf-8", newline="\n",
                                cursor=0, anchor=0, scroll=0)
    relocate(window, old, new)
    assert window._recovery_store.load(old) is None
    assert window._recovery_store.load(new).draft == "[draft](../source/assets/note.txt)"
    assert "[draft]" not in new.read_text(encoding="utf-8")


def test_existing_external_conflict_is_not_reset_by_move(make_data_safety_window, tmp_path):
    old, new = scenario(tmp_path)
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    window._editor.insertPlainText("local draft\n")
    signature = window._tab_state[str(old)]["source_signature"]
    old.write_text("external version of a different length", encoding="utf-8")
    relocate(window, old, new)
    state = window._tab_state[str(new)]
    assert state["source_signature"] == signature
    assert state["source_signature"] != window._file_signature(new)
    assert state["editor_document"].toPlainText().startswith("local draft")
    assert new.read_text(encoding="utf-8") == "external version of a different length"
