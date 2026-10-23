"""Independent regression checks for the 2026-10-22 workspace upgrade."""

from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from app import edit_backend, session_state
from app import window as window_mod
from app.recovery_dialog import RecoveryDialog
from tests.test_editor_data_safety import _DelayedSnapshotWysiwygView, _FakeWysiwygView
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


def test_first_save_during_tab_close_releases_relocated_state(
    make_window, tmp_path, monkeypatch  # noqa: F811
):
    window = make_window()
    window._new_draft("# draft\nbody")
    original = str(window._current_file)
    target = tmp_path / "saved.md"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    assert window._on_tab_close(0)
    assert target.read_text(encoding="utf-8") == "# draft\nbody"
    assert window._tab_bar.count() == 0
    assert original not in window._tab_state
    assert str(target) not in window._tab_state


def test_first_save_never_overwrites_existing_target(
    make_window, tmp_path, monkeypatch  # noqa: F811
):
    window = make_window()
    window._new_draft("# new draft")
    original = str(window._current_file)
    target = tmp_path / "occupied.md"
    target.write_text("original disk content", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    assert not window._save_edits()
    assert target.read_text(encoding="utf-8") == "original disk content"
    assert str(window._current_file) == original
    assert window._editor.document().isModified()
    assert window._recovery_store.load(original).draft == "# new draft"


def test_cancel_first_save_during_close_keeps_draft(
    make_window, monkeypatch  # noqa: F811
):
    window = make_window()
    window._new_draft("# still here")
    original = str(window._current_file)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: ("", ""))
    assert not window._on_tab_close(0)
    assert window._tab_bar.count() == 1
    assert str(window._current_file) == original
    assert window._editor.toPlainText() == "# still here"
    assert window._editor.document().isModified()


def test_pending_source_target_clears_on_user_tab_switch(
    make_window, md_files  # noqa: F811
):
    window = make_window()
    first, second = md_files
    window._open_file(str(first))
    window._open_file(str(second))
    window._renderer._render_generation = 4
    window._renderer._loaded_markdown_generation = 3
    window._open_document_target(str(first), "chapter-b")
    assert window._pending_source_target is not None
    window._tab_bar.setCurrentIndex(window._index_of_path(str(second)))
    assert window._current_file == second
    assert window._pending_source_target is None


def test_cancel_source_target_mode_exit_clears_pending(
    make_window, md_files, monkeypatch  # noqa: F811
):
    window = make_window()
    first, _ = md_files
    window._open_file(str(first))
    window._toggle_edit_mode()
    window._editor.insertPlainText("unsaved")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Cancel
    )
    window._open_document_target(str(first), "chapter-b")
    assert window._edit_mode
    assert window._editor.document().isModified()
    assert window._pending_source_target is None


def test_reading_next_chapter_retains_active_anchor(
    make_window, md_files  # noqa: F811
):
    window = make_window()
    window._open_file(str(md_files[0]))
    entries = [(1, "One", "one"), (1, "Two", "two"), (1, "Three", "three")]
    window._panel.toc.entries = lambda: entries
    tools = window._reading_tools
    assert window._panel.toc.entries() == entries
    assert tools.window is window
    tools.dock.show()
    tools.refresh()
    assert tools.toc._anchors == ["one", "two", "three"]
    tools.toc.set_active_anchor("two")
    called = []
    window._scroll_to_anchor = called.append
    tools.step(1)
    assert tools.toc._anchors == ["one", "two", "three"]
    assert called == ["three"]


def test_writing_outline_cannot_edit_hidden_office_shadow(
    make_window, md_files, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(window_mod, "WysiwygView", _FakeWysiwygView)
    window = make_window()
    window._open_file(str(md_files[0]))
    window._toggle_edit_mode()
    window._show_writing_outline()
    dialog = window._writing_outline
    dialog.controller.refresh_now()
    assert window._open_office_editor()
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    original = window._wysiwyg_view.loaded[-1]
    try:
        dialog.controller.change_heading_level(0, 1)
        assert window._editor.toPlainText() == original
    finally:
        dialog.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_pinned_tab_survives_close_others_and_session_roundtrip(
    make_window, md_files  # noqa: F811
):
    window = make_window()
    first, second = md_files
    window._open_file(str(first))
    window._toggle_tab_pin(str(first))
    window._open_file(str(second))
    window._close_other_tabs(str(second))
    assert window._tab_bar.count() == 2
    session_state.save_open_tabs(window)
    restored = make_window()
    session_state.restore_last_session(restored)
    assert restored._tab_state[str(first)]["pinned"]
    assert not restored._on_tab_close(restored._index_of_path(str(first)))


def test_draft_checkpoint_reopen_restores_latest_unicode_without_overwrite(
    make_window, monkeypatch  # noqa: F811
):
    window = make_window()
    window._new_draft("# initial")
    original = window._current_file
    window._editor.insertPlainText("更新😀\n")
    expected = window._editor.toPlainText()
    window._save_active_recovery_snapshot()
    session_state.save_open_tabs(window)
    monkeypatch.setattr(RecoveryDialog, "exec", lambda dialog: dialog._choose_restore())
    restored = make_window()
    session_state.restore_last_session(restored)
    assert restored._current_file == original
    assert restored._editor.toPlainText() == expected
    assert restored._editor.document().isModified()
    assert original.read_text(encoding="utf-8") == "# initial"


def test_background_draft_first_save_close_preserves_active_editor(
    make_window, md_files, tmp_path, monkeypatch  # noqa: F811
):
    window = make_window()
    window._new_draft("# draft background")
    old_key = str(window._current_file)
    window._open_file(str(md_files[0]))
    window._toggle_edit_mode()
    active_document = window._editor.document()
    target = tmp_path / "background.md"
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    assert window._close_tab_by_path(old_key)
    assert str(target) not in window._tab_state
    assert window._editor.document() is active_document
    assert window._current_file == md_files[0]
    assert target.read_text(encoding="utf-8") == "# draft background"


def test_startup_relocation_preserves_pin_and_both_recovery_copies(
    make_window, md_files, monkeypatch  # noqa: F811
):
    import app.relocation_journal as journal

    source, target = md_files
    window = make_window()
    window._open_file(str(source))
    window._toggle_tab_pin(str(source))
    session_state.save_open_tabs(window)
    snapshot_args = {"encoding": "utf-8", "newline": "\n", "cursor": 0, "anchor": 0, "scroll": 0}
    window._recovery_store.save(source, "old pending draft", **snapshot_args)
    window._recovery_store.save(target, "new pending draft", **snapshot_args)
    monkeypatch.setattr(
        journal,
        "recover_pending_relocations",
        lambda _roots: [SimpleNamespace(status="committed", source=source, target=target)],
    )
    restored = make_window()
    settings = session_state.QSettings("markdown-viewer", "MarkdownViewer")
    paths, mapping = session_state._recover_relocations(restored, settings, [str(source)], "")
    assert mapping == {str(source): str(target)}
    assert paths == [str(target)]
    assert restored._recovery_store.load(source).draft == "old pending draft"
    assert restored._recovery_store.load(target).draft == "new pending draft"
    restored._add_tab(target, "markdown")
    assert restored._tab_state[str(target)]["pinned"]


def test_office_draft_first_save_waits_for_live_snapshot(
    make_window, tmp_path, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    window = make_window()
    window._new_draft("# old shadow")
    assert window._open_office_editor()
    office = window._wysiwyg_view
    latest = "# Office 最新😀\n\n尚未推送來源緩衝區"
    office.edit_without_push(latest)
    target = tmp_path / "office-draft.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    assert window._save_edits()
    assert not target.exists()
    office.deliver_snapshot()
    assert not target.exists()
    office.deliver_acknowledgement()
    assert target.read_text(encoding="utf-8") == latest
    assert window._current_file == target
    assert office.document_path == target
    assert not window._editor.document().isModified()
    # Avoid starting a fresh async close handshake during fixture disposal.
    window._wysiwyg_close_snapshot_approved = True
