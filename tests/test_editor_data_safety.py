"""Regression tests for editor-buffer data safety and document ownership.

These cases cover failures that can lose an in-memory draft or leave the
shared editor owning every tab document it has ever displayed.  Renderer and
sidebar dependencies are replaced with the lightweight integration-test
fakes; the real ``MainWindow`` / ``EditorView`` buffer coordination remains in
use.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path
import weakref

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QTextDocument

from app import session_state, view_mode
from app import window as window_mod
from app.recovery import RecoveryStore
from tests.test_window_integration import (
    _FakePanel,
    _FakePdfView,
    _FakeRenderer,
    _FakeTagIndex,
)


@pytest.fixture(autouse=True)
def _isolated_editor_dependencies(tmp_path, monkeypatch):
    """Keep dialogs, settings, recovery, and WebEngine out of these tests."""

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

    settings_path = tmp_path / "data-safety-settings.ini"

    def isolated_settings(*_args, **_kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(window_mod, "QSettings", isolated_settings)
    monkeypatch.setattr(session_state, "QSettings", isolated_settings)
    monkeypatch.setattr(
        window_mod,
        "RecoveryStore",
        lambda: RecoveryStore(tmp_path / "recovery"),
    )

    # Run zero-delay cursor/scroll restoration immediately, but suppress the
    # delayed update checker and file-watcher callbacks in this test module.
    def selective_single_shot(*args):
        if int(args[0]) == 0:
            args[-1]()

    monkeypatch.setattr(
        window_mod.QTimer,
        "singleShot",
        staticmethod(selective_single_shot),
    )


@pytest.fixture
def make_data_safety_window(qapp):
    windows = []

    def _make():
        window = window_mod.MainWindow()
        windows.append(window)
        return window

    yield _make

    for window in reversed(windows):
        window._preview_editing = False
        for state in window._tab_state.values():
            document = state.get("editor_document")
            if isinstance(document, QTextDocument):
                document.setModified(False)
        window._editor.document().setModified(False)
        window.close()
    qapp.processEvents()


def _enter_markdown_editor(window, path: Path) -> None:
    window.open_path(str(path))
    if not window._edit_mode:
        window._toggle_edit_mode()
    assert window._current_kind == "markdown"
    assert window._edit_mode is True


def _replace_with_dirty_text(window, text: str) -> QTextDocument:
    document = window._editor.document()
    window._editor.setPlainText(text)
    document.setModified(True)
    assert window._editor.is_modified()
    return document


def test_deleted_dirty_source_keeps_draft_snapshot_and_can_be_recreated(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "deleted-dirty.md"
    note.write_text("disk version", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._fs_watcher.blockSignals(True)

    draft = "# preserved draft\n\nThis text exists only in memory."
    document = _replace_with_dirty_text(window, draft)
    window._save_active_recovery_snapshot()
    assert window._recovery_store.load(note).draft == draft

    note.unlink()
    window._on_browser_paths_deleted([note])

    key = str(note)
    assert window._index_of_path(key) >= 0
    assert window._active_path == key
    assert window._tab_state[key]["editor_document"] is document
    assert window._tab_state[key]["source_deleted"] is True
    assert document.isModified()
    assert document.toPlainText() == draft
    assert window._recovery_store.load(note).draft == draft

    # If another process recreates the deleted path first, it is a real
    # conflict and must not be overwritten merely because the tab remembers
    # the earlier user-driven deletion.
    note.write_text("someone recreated this path", encoding="utf-8")
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.No,
    )
    assert window._save_edits() is False
    assert note.read_text(encoding="utf-8") == "someone recreated this path"
    note.unlink()

    assert window._save_edits() is True
    assert note.read_text(encoding="utf-8") == draft
    assert document.isModified() is False
    assert "source_deleted" not in window._tab_state[key]
    assert window._recovery_store.load(note) is None


def test_dirty_external_reload_replaces_split_buffer_and_clears_snapshot(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "dirty-external.md"
    note.write_text("disk v1", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._toggle_split_mode()
    assert window._view_mode == view_mode.SPLIT
    window._fs_watcher.blockSignals(True)

    old_document = _replace_with_dirty_text(window, "unsaved local draft")
    window._save_active_recovery_snapshot()
    assert window._recovery_store.load(note) is not None
    note.write_text("disk v2 from another process", encoding="utf-8")
    window._loaded_signature = window._file_signature(note)
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.Yes,
    )

    window._prompt_external_change()

    state = window._tab_state[str(note)]
    new_document = state["editor_document"]
    assert new_document is window._editor.document()
    assert new_document is not old_document
    assert new_document.toPlainText() == "disk v2 from another process"
    assert new_document.isModified() is False
    assert state["view_mode"] == view_mode.SPLIT
    assert window._view_mode == view_mode.SPLIT
    assert window._recovery_store.load(note) is None


def test_clean_external_reload_updates_the_visible_markdown_editor(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "clean-external.md"
    note.write_text("visible editor v1", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._fs_watcher.blockSignals(True)
    old_document = window._editor.document()
    assert old_document.isModified() is False

    note.write_text("visible editor v2", encoding="utf-8")
    window._loaded_signature = window._file_signature(note)
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.Yes,
    )

    window._prompt_external_change()

    state = window._tab_state[str(note)]
    assert window._edit_mode is True
    assert window._view_mode == view_mode.EDIT
    assert window._editor.document() is state["editor_document"]
    assert window._editor.document() is not old_document
    assert window._editor.toPlainText() == "visible editor v2"
    assert window._editor.document().isModified() is False
    assert state["source_signature"] == window._file_signature(note)


def test_canceling_background_dirty_prompt_keeps_preview_edit_transaction_open(
    make_data_safety_window, tmp_path, monkeypatch
):
    dirty = tmp_path / "background-dirty.md"
    active = tmp_path / "active-preview.md"
    dirty.write_text("background disk", encoding="utf-8")
    active.write_text("active disk", encoding="utf-8")
    window = make_data_safety_window()

    _enter_markdown_editor(window, dirty)
    dirty_document = _replace_with_dirty_text(window, "background draft")
    window.open_path(str(active))
    assert window._view_mode == view_mode.PREVIEW
    assert dirty_document.isModified()
    assert window._recovery_store.load(dirty) is not None
    window._preview_editing = True

    answers = iter(
        [
            window_mod.QMessageBox.StandardButton.Yes,
            window_mod.QMessageBox.StandardButton.Cancel,
        ]
    )
    prompts = []

    def question(*args, **kwargs):
        prompts.append(args[1:3])
        return next(answers)

    monkeypatch.setattr(window_mod.QMessageBox, "question", question)

    assert window._confirm_close_all_edits() is False
    assert len(prompts) == 2
    assert window._preview_editing is True
    assert dirty_document.isModified()
    assert dirty_document.toPlainText() == "background draft"
    assert window._tab_state[str(dirty)]["editor_document"] is dirty_document
    assert window._recovery_store.load(dirty).draft == "background draft"

    # Keep fixture teardown non-interactive without discarding the state that
    # the assertions above are specifically checking.
    window._preview_editing = False
    dirty_document.setModified(False)


def test_repeated_closed_tabs_do_not_accumulate_child_documents(
    make_data_safety_window, tmp_path, qapp
):
    window = make_data_safety_window()
    released_documents = []

    for index in range(24):
        note = tmp_path / f"closed-{index:02}.md"
        note.write_text(f"document {index}", encoding="utf-8")
        _enter_markdown_editor(window, note)
        document = window._editor.document()
        assert document.parent() is None
        released_documents.append(weakref.ref(document))

        assert window._on_tab_close(window._tab_bar.currentIndex()) is True
        assert str(note) not in window._tab_state
        assert window._editor.document() is window._editor._parking_document
        assert window._editor.findChildren(QTextDocument) == []
        del document
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()

    gc.collect()
    assert all(
        reference() is None or not shiboken6.isValid(reference())
        for reference in released_documents
    )


def test_switch_to_preview_parks_editor_before_background_document_is_deleted(
    make_data_safety_window, tmp_path, qapp
):
    """A background tab must never delete the document still shown by editor.

    The parking assertion deliberately comes before closing the background
    tab.  A regression therefore fails safely instead of letting Qt process a
    deferred delete for the document still owned by ``QPlainTextEdit``, which
    can terminate the Python process with a native access violation.
    """

    edited = tmp_path / "edited.md"
    preview = tmp_path / "preview.md"
    edited.write_text("editor document", encoding="utf-8")
    preview.write_text("preview document", encoding="utf-8")
    window = make_data_safety_window()

    _enter_markdown_editor(window, edited)
    edited_document = window._editor.document()
    assert edited_document is not window._editor._parking_document

    window.open_path(str(preview))
    assert window._active_path == str(preview)
    assert window._view_mode == view_mode.PREVIEW
    assert window._editor.document() is window._editor._parking_document

    assert window._close_tab_by_path(str(edited)) is True
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()

    assert not shiboken6.isValid(edited_document)
    assert shiboken6.isValid(window._editor)
    assert shiboken6.isValid(window._editor._parking_document)
    assert window._editor.document() is window._editor._parking_document
    assert window._editor.toPlainText() == ""


def test_recovered_missing_source_conflicts_with_later_external_file(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "created-after-snapshot.md"
    draft = "recovered draft"
    window = make_data_safety_window()
    window._recovery_store.save(
        note,
        draft,
        encoding="utf-8",
        newline="\n",
        cursor=0,
        anchor=0,
        scroll=0,
        source_signature=None,
    )

    class RestoreDialog:
        RESTORE = "restore"
        DISCARD = "discard"
        LATER = "later"

        def __init__(self, *args, **kwargs):
            self.choice = self.RESTORE

        def exec(self):
            return 1

    monkeypatch.setattr(window_mod, "RecoveryDialog", RestoreDialog)
    window.open_path(str(note))
    assert window._editor.toPlainText() == draft
    assert window._tab_state[str(note)]["source_signature"] is None

    note.write_text("new external file", encoding="utf-8")
    prompts = []

    def reject_overwrite(*args, **kwargs):
        prompts.append(args[2])
        return window_mod.QMessageBox.StandardButton.No

    monkeypatch.setattr(window_mod.QMessageBox, "question", reject_overwrite)

    assert window._save_edits() is False
    assert len(prompts) == 1
    assert "沒有檔案" in prompts[0]
    assert note.read_text(encoding="utf-8") == "new external file"
    assert window._tab_state[str(note)]["source_signature"] is None


def test_dirty_migration_moves_recovery_check_and_never_replaces_live_buffer(
    make_data_safety_window, tmp_path, monkeypatch
):
    old = tmp_path / "old.md"
    new = tmp_path / "renamed.md"
    other = tmp_path / "other.md"
    old.write_text("disk", encoding="utf-8")
    other.write_text("other", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, old)
    document = _replace_with_dirty_text(window, "live dirty draft")
    window._save_active_recovery_snapshot()
    assert str(old) in window._recovery_checked_paths

    old.rename(new)
    window._on_browser_paths_migrated({str(old): str(new)})

    assert str(old) not in window._recovery_checked_paths
    assert str(new) in window._recovery_checked_paths
    assert window._recovery_store.load(old) is None
    assert window._recovery_store.load(new).draft == "live dirty draft"

    class UnexpectedRecoveryDialog:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a live tab buffer must win over recovery")

    monkeypatch.setattr(window_mod, "RecoveryDialog", UnexpectedRecoveryDialog)
    window.open_path(str(other))
    window.open_path(str(new))

    assert window._editor.document() is document
    assert window._editor.toPlainText() == "live dirty draft"
    assert window._editor.is_modified()


def test_session_restore_includes_missing_source_when_recovery_exists(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "missing-but-recoverable.md"
    window = make_data_safety_window()
    window._recovery_store.save(
        note,
        "survived crash",
        encoding="utf-8",
        newline="\n",
        cursor=2,
        anchor=2,
        scroll=0,
        source_signature=None,
    )
    settings = window_mod.QSettings("unused", "unused")
    settings.setValue("open_tabs", json.dumps([str(note)]))
    settings.setValue("active_tab", 0)

    class RestoreDialog:
        RESTORE = "restore"
        DISCARD = "discard"
        LATER = "later"

        def __init__(self, *args, **kwargs):
            self.choice = self.RESTORE

        def exec(self):
            return 1

    monkeypatch.setattr(window_mod, "RecoveryDialog", RestoreDialog)

    window.restore_last_session()

    assert window._tab_bar.count() == 1
    assert window._active_path == str(note)
    assert window._editor.toPlainText() == "survived crash"
    assert window._editor.is_modified()


def test_inactive_clean_editor_refreshes_from_disk_before_reactivation(
    make_data_safety_window, tmp_path
):
    first = tmp_path / "background-clean.md"
    second = tmp_path / "foreground.md"
    first.write_text("disk v1", encoding="utf-8")
    second.write_text("foreground", encoding="utf-8")
    window = make_data_safety_window()

    _enter_markdown_editor(window, first)
    old_document = window._editor.document()
    window.open_path(str(second))
    assert window._editor.document() is window._editor._parking_document

    first.write_text("disk v2 from another process", encoding="utf-8")
    window.open_path(str(first))

    state = window._tab_state[str(first)]
    assert window._edit_mode is True
    assert window._editor.document() is state["editor_document"]
    assert window._editor.document() is not old_document
    assert window._editor.toPlainText() == "disk v2 from another process"
    assert window._editor.document().isModified() is False
    assert state["source_signature"] == window._file_signature(first)


def test_inactive_clean_editor_becomes_recoverable_draft_if_source_is_deleted(
    make_data_safety_window, tmp_path
):
    first = tmp_path / "background-deleted.md"
    second = tmp_path / "foreground.md"
    first.write_text("only remaining copy", encoding="utf-8")
    second.write_text("foreground", encoding="utf-8")
    window = make_data_safety_window()

    _enter_markdown_editor(window, first)
    document = window._editor.document()
    window.open_path(str(second))
    first.unlink()
    window.open_path(str(first))

    state = window._tab_state[str(first)]
    assert window._editor.document() is document
    assert document.toPlainText() == "only remaining copy"
    assert document.isModified() is True
    assert state["source_deleted"] is True
    assert window._recovery_store.load(first).draft == "only remaining copy"


def test_saving_an_unchanged_buffer_never_overwrites_a_newer_disk_version(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "clean-save.md"
    note.write_text("disk v1", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    assert window._editor.document().isModified() is False

    note.write_text("newer external version", encoding="utf-8")

    assert window._save_edits() is True
    assert note.read_text(encoding="utf-8") == "newer external version"
