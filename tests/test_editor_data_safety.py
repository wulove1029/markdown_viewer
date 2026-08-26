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
import sys
import types
from pathlib import Path
import weakref

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer, Signal
from PySide6.QtGui import QAction, QTextDocument
from PySide6.QtWidgets import QWidget

from app import edit_backend, export_actions, session_state, view_mode
from app import window as window_mod
from app.recovery import RecoveryStore
from tests.test_window_integration import (
    _FakePanel,
    _FakePdfView,
    _FakeRenderer,
    _FakeTagIndex,
)


class _FakeWysiwygView(QWidget):
    """Stand-in for app.wysiwyg_view.WysiwygView (no real QWebEngineView).

    Mirrors the real widget's public surface (load_markdown / signals /
    apply_theme / flush_pending_edits) so window.py's backend-switch and
    shadow-document-push wiring can be exercised without QtWebEngine, which
    this project keeps out of ordinary (non-opt-in) test runs.
    """

    content_changed = Signal(str)
    save_requested = Signal()
    view_ready = Signal()
    esc_requested = Signal()
    toolbar_action = Signal(str)
    zoom_requested = Signal(int)
    context_menu_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded: list[str] = []
        self.theme_calls = 0
        self.flush_calls = 0
        self.focus_near_text_calls: list[str] = []
        self.zoom_factors: list[float] = []

    def load_markdown(self, text: str) -> None:
        self.loaded.append(text)

    def apply_theme(self, theme) -> None:
        self.theme_calls += 1

    def flush_pending_edits(self) -> None:
        self.flush_calls += 1

    def focus_near_text(self, snippet: str) -> None:
        self.focus_near_text_calls.append(snippet)

    def type_markdown(self, text: str) -> None:
        """Test helper: simulate the JS push that a keystroke would cause."""
        self.content_changed.emit(text)

    def press_esc(self) -> None:
        """Test helper: simulate vditor_glue.js's escRequested bridge call."""
        self.esc_requested.emit()

    def page(self):
        # _toggle_edit_backend's wysiwyg->split direction asks the live page
        # for its current value via runJavaScript(js, callback). There is no
        # real page here, so answer with nothing pending (None): the caller
        # falls back to whatever content_changed has already pushed.
        outer = self

        class _FakePage:
            def runJavaScript(_self, _js, callback):
                callback(None)

            def setZoomFactor(_self, factor):
                outer.zoom_factors.append(factor)

        return _FakePage()


class _DelayedSnapshotWysiwygView(_FakeWysiwygView):
    """Controllable stand-in for WebEngine's two asynchronous callbacks.

    The ordinary fake above intentionally flushes synchronously for broad,
    fast integration coverage.  These tests need the opposite: snapshot and
    acknowledgement callbacks stay queued until the test releases them, so
    tab/save/timeout races can be exercised without a nested event loop or a
    real Chromium process.
    """

    content_changed_detailed = Signal(str, int, int, str, int, int)
    save_with_content_detailed = Signal(str, int, int, str, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ready = True
        self._generation = 0
        self.live_markdown = ""
        self.last_acknowledged_markdown = ""
        self.revision = 0
        self._snapshot_token = 0
        self.snapshot_requests: list[tuple[object, dict]] = []
        self.acknowledgements: list[tuple[str, int, int, object]] = []
        self.cancelled_snapshot_tokens: list[int] = []
        self.saved_markdown: list[str | None] = []
        self.document_path = None

    @staticmethod
    def _qt_length(text: str) -> int:
        return len(text.encode("utf-16-le")) // 2

    def load_markdown(self, text: str) -> None:
        self._generation += 1
        self.loaded.append(text)
        self.live_markdown = text
        self.last_acknowledged_markdown = text
        self.revision = 0

    def set_document_path(self, path) -> None:
        self.document_path = path

    def edit_without_push(self, text: str) -> None:
        """Change only the visible value, leaving Python's shadow stale."""
        self.live_markdown = text

    def request_markdown_snapshot_envelope(self, callback) -> None:
        self._snapshot_token += 1
        envelope = {
            "markdown": self.live_markdown,
            "generation": self._generation,
            "token": self._snapshot_token,
            "baseRevision": self.revision,
            "revision": self.revision + 1,
            "length": self._qt_length(self.live_markdown),
            # A full replacement keeps this fake small while exercising the
            # production UTF-16 incremental-patch path.
            "start": 0,
            "deleteCount": self._qt_length(self.last_acknowledged_markdown),
            "inserted": self.live_markdown,
        }
        self.snapshot_requests.append((callback, envelope))

    def deliver_snapshot(self) -> None:
        callback, envelope = self.snapshot_requests.pop(0)
        callback(envelope)

    def acknowledge_markdown(
        self, markdown: str, token: int, revision: int, callback=None
    ) -> None:
        self.acknowledgements.append((markdown, token, revision, callback))

    def deliver_acknowledgement(self, result=True) -> None:
        markdown, _token, revision, callback = self.acknowledgements.pop(0)
        self.last_acknowledged_markdown = markdown
        self.revision = revision
        if callback is not None:
            callback(result)

    def cancel_snapshot(self, token: int = 0) -> None:
        self.cancelled_snapshot_tokens.append(token)

    def mark_saved(self, markdown: str | None = None) -> None:
        self.saved_markdown.append(markdown)


@pytest.fixture(autouse=True)
def _isolated_editor_dependencies(tmp_path, monkeypatch):
    """Keep dialogs, settings, recovery, and WebEngine out of these tests."""

    monkeypatch.setattr(window_mod, "RendererView", _FakeRenderer)
    monkeypatch.setattr(window_mod, "PdfView", _FakePdfView)
    monkeypatch.setattr(window_mod, "LeftPanel", _FakePanel)
    monkeypatch.setattr(window_mod, "TagIndex", _FakeTagIndex)
    monkeypatch.setattr(window_mod, "WysiwygView", _FakeWysiwygView)
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


def _capture_menu_exec(monkeypatch, window):
    """Replace QMenu after window construction and return captured positions."""
    positions = []

    class MenuSpy:
        def __init__(self, _parent=None):
            self.actions = []

        def addAction(self, *args):
            if args and isinstance(args[0], QAction):
                action = args[0]
            else:
                action = QAction(str(args[0]), window)
                if len(args) > 1 and callable(args[1]):
                    action.triggered.connect(args[1])
            self.actions.append(action)
            return action

        def addSeparator(self):
            return None

        def sizeHint(self):
            return types.SimpleNamespace(width=lambda: 180)

        def exec(self, position):
            positions.append(type(position)(position))

    monkeypatch.setattr(window_mod, "QMenu", MenuSpy)
    return positions


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
    assert (
        window._tab_bar.tabText(0)
        == "● deleted-dirty.md（原檔已刪除）"
    )
    assert window._tab_bar.mode_badge_text(0) == "MD"

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
    assert window._tab_bar.tabText(0) == "deleted-dirty.md"
    assert window._tab_bar.mode_badge_text(0) == "MD"


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


# ── WYSIWYG backend: shadow-document push model (app/edit_backend.py) ─────
#
# WysiwygView itself is a QWebEngineView and stays out of these tests (see
# _FakeWysiwygView above); RUN_WEBENGINE_TESTS=1 tests/test_wysiwyg_webengine.py
# covers the real Vditor/QWebChannel round trip. What matters here is that
# window.py's wiring around the fake -- toggling the backend, writing a JS
# push into the tab's real QTextDocument, and every dirty/save path reading
# that same document -- behaves exactly like a split-backend edit would.

def test_wysiwyg_toggle_loads_current_text_and_switches_the_stack(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-toggle.md"
    note.write_text("# toggled\n", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # exercise the split->wysiwyg toggle
    _enter_markdown_editor(window, note)
    document = window._editor.document()

    window._toggle_edit_backend()

    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._stack.currentWidget() is window._wysiwyg_view
    assert window._wysiwyg_view.loaded[-1] == document.toPlainText()
    assert window._wysiwyg_btn.isChecked() is True

    # A push while WYSIWYG is active still writes straight into the tab's
    # real document (the shadow-document push model), exactly as it would
    # coming from a real Vditor instance.
    window._wysiwyg_view.type_markdown("# toggled back\n")
    assert document.toPlainText() == "# toggled back\n"

    # And toggling away reads the (fake) live page value via the same
    # runJavaScript-callback path the real WysiwygView uses.
    window._toggle_edit_backend()
    assert window._active_edit_backend == edit_backend.SPLIT_BACKEND
    assert window._stack.currentWidget() is window._editor_split


def test_wysiwyg_context_menu_scales_js_client_coordinates_by_page_zoom(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "wysiwyg-context-menu-zoom.md"
    note.write_text("context menu", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.resize(640, 480)
    page = types.SimpleNamespace(
        zoomFactor=lambda: 1.75,
        action=lambda _web_action: QAction(window),
    )
    monkeypatch.setattr(view, "page", lambda: page)
    positions = _capture_menu_exec(monkeypatch, window)

    window._on_wysiwyg_context_menu(20, 24)

    assert positions == [view.mapToGlobal(window_mod.QPoint(35, 42))]


def test_wysiwyg_wheel_zoom_applies_one_final_factor_and_defers_persistence(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-wheel-zoom.md"
    note.write_text("dirty draft", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._open_office_editor()
    view = window._wysiwyg_view
    document = window._editor.document()
    view.type_markdown("dirty draft updated")
    zoom_call_count = len(view.zoom_factors)
    settings = session_state.QSettings("markdown-viewer", "MarkdownViewer")
    renderer_zoom = window._renderer._zoom
    preview_zoom = window._edit_preview._zoom

    view.zoom_requested.emit(3)

    assert window._content_zoom == pytest.approx(1.5)
    assert view.zoom_factors[zoom_call_count:] == [pytest.approx(1.5)]
    assert window._renderer._zoom == pytest.approx(renderer_zoom)
    assert window._edit_preview._zoom == pytest.approx(preview_zoom)
    assert settings.value("content_zoom") is None
    assert window._pending_office_zoom == pytest.approx(1.5)
    assert window._office_zoom_sync_timer.isActive()
    assert window._editor.document() is document
    assert document.toPlainText() == "dirty draft updated"
    assert document.isModified() is True

    window._commit_office_zoom()

    assert window._renderer._zoom == pytest.approx(1.5)
    assert window._edit_preview._zoom == pytest.approx(1.5)
    assert view.zoom_factors[zoom_call_count:] == [pytest.approx(1.5)]
    assert float(settings.value("content_zoom")) == pytest.approx(1.5)
    assert window._pending_office_zoom is None
    assert window._office_zoom_sync_timer.isActive() is False


def test_wysiwyg_wheel_zoom_ignores_hidden_view_and_clamped_edge(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-hidden-wheel.md"
    note.write_text("text", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._open_office_editor()
    view = window._wysiwyg_view

    window._apply_zoom(3.0)
    window._commit_office_zoom()
    before = list(view.zoom_factors)
    view.zoom_requested.emit(1)
    assert view.zoom_factors == before
    assert window._office_zoom_sync_timer.isActive() is False

    window._stack.setCurrentWidget(window._renderer)
    view.zoom_requested.emit(-2)
    assert window._content_zoom == pytest.approx(3.0)
    assert view.zoom_factors == before


def test_wysiwyg_keyboard_zoom_updates_only_visible_page_until_idle(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-keyboard-zoom.md"
    note.write_text("text", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._open_office_editor()
    view = window._wysiwyg_view
    zoom_call_count = len(view.zoom_factors)
    settings = session_state.QSettings("markdown-viewer", "MarkdownViewer")

    window._zoom_in()
    window._zoom_in()
    window._zoom_in()

    assert window._content_zoom == pytest.approx(1.5)
    assert view.zoom_factors[zoom_call_count:] == pytest.approx(
        [1.1, 1.25, 1.5]
    )
    assert window._renderer._zoom == pytest.approx(1.0)
    assert window._edit_preview._zoom == pytest.approx(1.0)
    assert settings.value("content_zoom") is None
    assert window._office_zoom_sync_timer.isActive()

    window._commit_office_zoom()

    assert window._renderer._zoom == pytest.approx(1.5)
    assert window._edit_preview._zoom == pytest.approx(1.5)
    assert view.zoom_factors[zoom_call_count:] == pytest.approx(
        [1.1, 1.25, 1.5]
    )
    assert float(settings.value("content_zoom")) == pytest.approx(1.5)


def test_pending_wysiwyg_wheel_zoom_flushes_before_view_transition(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-flush-zoom.md"
    note.write_text("text", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._open_office_editor()
    settings = session_state.QSettings("markdown-viewer", "MarkdownViewer")

    window._wysiwyg_view.zoom_requested.emit(2)
    assert settings.value("content_zoom") is None

    window._toggle_office_mode()

    assert float(settings.value("content_zoom")) == pytest.approx(1.25)
    assert window._renderer._zoom == pytest.approx(1.25)
    assert window._edit_preview._zoom == pytest.approx(1.25)
    assert window._pending_office_zoom is None
    assert window._office_zoom_sync_timer.isActive() is False
    assert window._stack.currentWidget() is window._renderer


def test_pending_wysiwyg_zoom_flushes_before_source_split_becomes_visible(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-to-source-split-zoom.md"
    note.write_text("text", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, note)
    window._open_office_editor()
    settings = session_state.QSettings("markdown-viewer", "MarkdownViewer")

    window._wysiwyg_view.zoom_requested.emit(2)
    assert window._edit_preview._zoom == pytest.approx(1.0)
    assert settings.value("content_zoom") is None

    window._toggle_split_mode()

    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert window._view_mode == view_mode.SPLIT
    assert window._stack.currentWidget() is window._editor_split
    assert window._renderer._zoom == pytest.approx(1.25)
    assert window._edit_preview._zoom == pytest.approx(1.25)
    assert float(settings.value("content_zoom")) == pytest.approx(1.25)
    assert window._pending_office_zoom is None
    assert window._office_zoom_sync_timer.isActive() is False


def test_wysiwyg_export_menu_anchors_to_scaled_toolbar_button_rect(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "wysiwyg-export-menu-anchor.md"
    note.write_text("export menu", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.resize(640, 480)
    scripts = []

    def run_javascript(script, callback):
        scripts.append(script)
        callback(json.dumps({"x": 80.0, "y": 32.0}))

    page = types.SimpleNamespace(
        zoomFactor=lambda: 1.5,
        runJavaScript=run_javascript,
    )
    monkeypatch.setattr(view, "page", lambda: page)
    positions = _capture_menu_exec(monkeypatch, window)

    window._show_wysiwyg_export_menu()

    assert len(scripts) == 1
    assert '[data-type="export"]' in scripts[0]
    assert "getBoundingClientRect" in scripts[0]
    assert positions == [view.mapToGlobal(window_mod.QPoint(120, 48))]


def test_wysiwyg_push_marks_the_tab_document_modified(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-push.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # toggle into wysiwyg below
    _enter_markdown_editor(window, note)
    document = window._editor.document()
    assert document.isModified() is False

    window._toggle_edit_backend()
    window._wysiwyg_view.type_markdown("start, edited in Vditor")

    assert document.toPlainText() == "start, edited in Vditor"
    assert document.isModified() is True
    assert window._editor.is_modified() is True
    assert window._tab_state[str(note)]["editor_document"] is document


def test_wysiwyg_delta_replaces_an_emoji_using_utf16_offsets(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-emoji-delta.md"
    note.write_text("A😀B", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    document = window._editor.document()
    document.setModified(False)

    # JavaScript and QTextCursor both address the emoji as two UTF-16 code
    # units.  A Python-code-point offset here would leave a lone surrogate or
    # consume the trailing B.
    window._on_wysiwyg_content_delta(
        "A😺B",
        start=1,
        delete_count=2,
        inserted="😺",
        base_revision=0,
        final_length=4,
    )

    assert document.toPlainText() == "A😺B"
    assert document.isModified() is True
    assert window._wysiwyg_shadow_text == "A😺B"
    assert window._wysiwyg_shadow_qt_length == 4
    assert window._wysiwyg_shadow_revision == 1


def test_wysiwyg_delta_revision_mismatch_uses_the_full_snapshot(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-stale-delta.md"
    note.write_text("abcdef", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    document = window._editor.document()
    document.setModified(False)

    full_snapshot = "replacement from full snapshot"
    window._on_wysiwyg_content_delta(
        full_snapshot,
        start=0,
        delete_count=1,
        inserted="X",
        base_revision=9,
        final_length=len(full_snapshot),
    )

    # Applying the stale delta would produce "Xbcdef".  The revision guard
    # must instead converge the Qt shadow document to the full Vditor value.
    assert document.toPlainText() == full_snapshot
    assert document.isModified() is True
    assert window._wysiwyg_shadow_text == full_snapshot
    assert window._wysiwyg_shadow_qt_length == len(full_snapshot)
    assert window._wysiwyg_shadow_revision == 10


def test_wysiwyg_older_revision_cannot_overwrite_an_accepted_snapshot(
    make_data_safety_window, tmp_path
):
    newest = "newest accepted snapshot"
    note = tmp_path / "wysiwyg-out-of-order-delta.md"
    note.write_text(newest, encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    document = window._editor.document()
    document.setModified(False)
    # Model a transition snapshot that Python has already accepted and acked.
    window._wysiwyg_shadow_revision = 2

    window._on_wysiwyg_content_delta(
        "older queued push",
        start=0,
        delete_count=document.characterCount() - 1,
        inserted="older queued push",
        base_revision=0,
        final_length=len("older queued push"),
    )

    assert document.toPlainText() == newest
    assert document.isModified() is False
    assert window._wysiwyg_shadow_text == newest
    assert window._wysiwyg_shadow_revision == 2


def test_wysiwyg_noop_delta_advances_revision_without_marking_dirty(
    make_data_safety_window, tmp_path
):
    markdown = "same 😀"
    note = tmp_path / "wysiwyg-noop-delta.md"
    note.write_text(markdown, encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    document = window._editor.document()
    qt_length = document.characterCount() - 1
    document.setModified(False)

    window._on_wysiwyg_content_delta(
        markdown,
        start=qt_length,
        delete_count=0,
        inserted="",
        base_revision=0,
        final_length=qt_length,
    )

    assert document.toPlainText() == markdown
    assert document.isModified() is False
    assert window._editor.is_modified() is False
    assert window._wysiwyg_shadow_revision == 1


def test_wysiwyg_push_is_caught_by_confirm_discard_edits(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "wysiwyg-discard.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # toggle into wysiwyg below
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    window._wysiwyg_view.type_markdown("unsaved wysiwyg edit")
    assert window._editor.is_modified() is True

    prompts = []

    def question(*args, **kwargs):
        prompts.append(args[1:3])
        return window_mod.QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(window_mod.QMessageBox, "question", question)

    assert window._confirm_discard_edits() is False
    assert len(prompts) == 1
    assert window._editor.is_modified() is True
    assert window._editor.toPlainText() == "unsaved wysiwyg edit"


def test_wysiwyg_save_writes_the_file_and_marks_the_document_clean(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "wysiwyg-save.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # toggle into wysiwyg below
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    window._wysiwyg_view.type_markdown("saved from Vditor")

    assert window._save_edits() is True

    assert note.read_text(encoding="utf-8") == "saved from Vditor"
    assert window._editor.document().isModified() is False
    # Ctrl+S must never depend on an async runJavaScript round trip; the
    # fake's flush is still called as a best-effort nudge (see _save_edits).
    assert window._wysiwyg_view.flush_calls >= 1


def test_wysiwyg_async_save_waits_for_ack_and_writes_the_live_snapshot(
    make_data_safety_window, tmp_path, monkeypatch
):
    """The save continuation must use the value newer than the debounce push."""
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "wysiwyg-delayed-save.md"
    note.write_text("old disk and shadow", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("newest visible WebEngine text")

    window._save_edits()

    assert len(view.snapshot_requests) == 1
    assert note.read_text(encoding="utf-8") == "old disk and shadow"
    assert window._editor.toPlainText() == "old disk and shadow"

    view.deliver_snapshot()

    assert len(view.acknowledgements) == 1
    assert window._editor.toPlainText() == "newest visible WebEngine text"
    # Applying the snapshot is not permission to write yet: continuation is
    # released only after JS has aligned the next delta's baseline.
    assert note.read_text(encoding="utf-8") == "old disk and shadow"

    view.deliver_acknowledgement()

    assert note.read_text(encoding="utf-8") == "newest visible WebEngine text"
    assert window._editor.document().isModified() is False
    assert view.saved_markdown[-1] == "newest visible WebEngine text"
    assert window._wysiwyg_snapshot_busy is False
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_wysiwyg_async_tab_switch_stashes_the_live_snapshot_before_transition(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    first = tmp_path / "wysiwyg-delayed-tab-a.md"
    second = tmp_path / "wysiwyg-delayed-tab-b.md"
    first.write_text("old first shadow", encoding="utf-8")
    second.write_text("second document", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, first)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("latest first-tab text")
    second_index = window._add_tab(second, "markdown")

    window._tab_bar.setCurrentIndex(second_index)

    # currentChanged fires synchronously, but the old tab remains selected and
    # active until both WebEngine callbacks finish.
    assert window._active_path == str(first)
    assert window._tab_bar.currentIndex() == window._index_of_path(str(first))
    assert len(view.snapshot_requests) == 1

    view.deliver_snapshot()
    assert window._active_path == str(first)
    view.deliver_acknowledgement()

    assert window._active_path == str(second)
    first_document = window._tab_state[str(first)]["editor_document"]
    assert first_document.toPlainText() == "latest first-tab text"
    assert first_document.isModified() is True


def test_wysiwyg_snapshot_timeout_fails_closed_and_ignores_late_result(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "wysiwyg-snapshot-timeout.md"
    note.write_text("durable shadow", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("renderer value that never replied")
    continued = []
    existing_timers = set(window.findChildren(QTimer))

    assert window._request_live_wysiwyg_snapshot(
        lambda: continued.append(True), purpose="測試逾時"
    ) is True
    snapshot_timers = [
        timer
        for timer in window.findChildren(QTimer)
        if timer not in existing_timers and timer.isActive()
    ]
    assert len(snapshot_timers) == 1
    assert snapshot_timers[0].interval() == 2500
    assert window._wysiwyg_snapshot_busy is True
    assert view.isEnabled() is False

    snapshot_timers[0].timeout.emit()

    assert continued == []
    assert window._wysiwyg_snapshot_busy is False
    assert view.isEnabled() is True
    assert window._tab_bar.isEnabled() is True
    assert view.cancelled_snapshot_tokens == [0]
    assert window._editor.toPlainText() == "durable shadow"

    # A WebEngine callback arriving after the timeout cannot revive the
    # cancelled transition or apply its now-untrusted payload.
    view.deliver_snapshot()
    assert continued == []
    assert window._editor.toPlainText() == "durable shadow"
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_wysiwyg_stale_context_between_snapshot_and_ack_never_continues(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "wysiwyg-stale-ack.md"
    note.write_text("initial", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("accepted snapshot")
    continued = []

    window._request_live_wysiwyg_snapshot(
        lambda: continued.append(True), purpose="測試 stale ack"
    )
    view.deliver_snapshot()
    assert window._editor.toPlainText() == "accepted snapshot"
    assert len(view.acknowledgements) == 1

    # A document reload between the two async callbacks advances this exact
    # generation in the real view.  Even a nominally successful old ack must
    # not release its continuation into the replacement document.
    view._generation += 1
    view.deliver_acknowledgement(result=True)

    assert continued == []
    assert window._wysiwyg_snapshot_busy is False
    assert view.isEnabled() is True
    assert window._tab_bar.isEnabled() is True
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_browser_rename_waits_for_wysiwyg_snapshot_and_updates_document_path(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    old_path = tmp_path / "before-rename.md"
    new_path = tmp_path / "after-rename.md"
    old_path.write_text("old shadow", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, old_path)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("unpublished rename draft")
    old_path.rename(new_path)

    window._on_browser_paths_migrated({str(old_path): str(new_path)})

    assert len(view.snapshot_requests) == 1
    assert window._active_path == str(old_path)
    assert window._tab_bar.tabText(0) == "before-rename.md"
    assert window._tab_bar.mode_badge_text(0) == "Office"
    assert str(old_path) in window._tab_state
    assert str(new_path) not in window._tab_state
    assert Path(view.document_path) == old_path

    view.deliver_snapshot()
    assert window._active_path == str(old_path)
    view.deliver_acknowledgement()

    assert window._active_path == str(new_path)
    assert window._current_file == new_path
    assert str(old_path) not in window._tab_state
    state = window._tab_state[str(new_path)]
    assert state["editor_document"].toPlainText() == "unpublished rename draft"
    assert state["editor_document"].isModified() is True
    assert Path(view.document_path) == new_path
    assert window._tab_bar.tabText(0) == "● after-rename.md"
    assert window._tab_bar.mode_badge_text(0) == "Office"
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_browser_rename_snapshot_timeout_still_reconciles_the_new_path(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    old_path = tmp_path / "timeout-before.md"
    new_path = tmp_path / "timeout-after.md"
    old_path.write_text("last confirmed shadow", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, old_path)
    window._toggle_office_mode()
    existing_timers = set(window.findChildren(QTimer))
    old_path.rename(new_path)

    window._on_browser_paths_migrated({str(old_path): str(new_path)})

    snapshot_timers = [
        timer
        for timer in window.findChildren(QTimer)
        if timer not in existing_timers and timer.isActive()
    ]
    assert len(snapshot_timers) == 1
    snapshot_timers[0].timeout.emit()

    assert window._active_path == str(new_path)
    assert window._current_file == new_path
    state = window._tab_state[str(new_path)]
    assert state["editor_document"].isModified() is True
    assert state["editor_document"].toPlainText() == "last confirmed shadow"
    assert session_state.load_document_edit_backend(old_path) is None
    assert session_state.load_document_edit_backend(new_path) == (
        edit_backend.WYSIWYG_BACKEND
    )
    assert window._recovery_store.load(new_path) is not None
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_chained_renames_during_snapshot_reconcile_in_filesystem_order(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    first = tmp_path / "chain-a.md"
    second = tmp_path / "chain-b.md"
    final = tmp_path / "chain-c.md"
    first.write_text("confirmed", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, first)
    window._toggle_office_mode()
    view = window._wysiwyg_view
    first.rename(second)

    window._on_browser_paths_migrated({str(first): str(second)})
    assert window._wysiwyg_snapshot_busy is True
    second.rename(final)
    window._on_browser_paths_migrated({str(second): str(final)})

    assert window._wysiwyg_snapshot_busy is False
    assert view.cancelled_snapshot_tokens == [0]
    assert window._active_path == str(final)
    assert window._current_file == final
    assert str(first) not in window._tab_state
    assert str(second) not in window._tab_state
    assert str(final) in window._tab_state
    assert session_state.load_document_edit_backend(first) is None
    assert session_state.load_document_edit_backend(second) is None
    assert session_state.load_document_edit_backend(final) == (
        edit_backend.WYSIWYG_BACKEND
    )
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_rename_then_delete_during_snapshot_keeps_a_recoverable_draft(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    first = tmp_path / "delete-chain-a.md"
    renamed = tmp_path / "delete-chain-b.md"
    first.write_text("last confirmed", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, first)
    window._toggle_office_mode()
    first.rename(renamed)

    window._on_browser_paths_migrated({str(first): str(renamed)})
    assert window._wysiwyg_snapshot_busy is True
    renamed.unlink()
    window._on_browser_paths_deleted([renamed])

    assert window._wysiwyg_snapshot_busy is False
    assert window._active_path == str(renamed)
    state = window._tab_state[str(renamed)]
    assert state["source_deleted"] is True
    assert state["editor_document"].isModified() is True
    assert state["editor_document"].toPlainText() == "last confirmed"
    recovery = window._recovery_store.load(renamed)
    assert recovery is not None
    assert recovery.draft == "last confirmed"
    assert session_state.load_document_edit_backend(renamed) == (
        edit_backend.WYSIWYG_BACKEND
    )
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_browser_delete_waits_for_snapshot_and_preserves_unpushed_dirty_draft(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "delete-unpushed-wysiwyg.md"
    note.write_text("clean shadow", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("draft that exists only in WebEngine")
    note.unlink()

    window._on_browser_paths_deleted([note])

    state = window._tab_state[str(note)]
    assert len(view.snapshot_requests) == 1
    assert window._tab_bar.count() == 1
    assert state["editor_document"].toPlainText() == "clean shadow"
    assert state["editor_document"].isModified() is False
    assert "source_deleted" not in state

    view.deliver_snapshot()
    assert "source_deleted" not in state
    view.deliver_acknowledgement()

    assert window._tab_bar.count() == 1
    assert window._active_path == str(note)
    assert state["source_deleted"] is True
    assert state["editor_document"].toPlainText() == (
        "draft that exists only in WebEngine"
    )
    assert state["editor_document"].isModified() is True
    recovery = window._recovery_store.load(note)
    assert recovery is not None
    assert recovery.draft == "draft that exists only in WebEngine"
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_external_change_waits_for_snapshot_then_prompts_for_dirty_wysiwyg(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "external-change-unpushed-wysiwyg.md"
    note.write_text("disk version one", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("local draft newer than the shadow")
    loaded_signature = window._loaded_signature
    note.write_text("disk version two from another process", encoding="utf-8")
    prompts = []

    def keep_local_draft(*args, **_kwargs):
        prompts.append(args[1:3])
        return window_mod.QMessageBox.StandardButton.No

    monkeypatch.setattr(window_mod.QMessageBox, "question", keep_local_draft)

    window._on_file_changed(str(note))

    assert len(view.snapshot_requests) == 1
    assert prompts == []
    assert window._loaded_signature == loaded_signature
    assert window._editor.toPlainText() == "disk version one"

    view.deliver_snapshot()
    assert prompts == []
    view.deliver_acknowledgement()

    assert len(prompts) == 1
    title, message = prompts[0]
    assert title == "檔案已在外部變更"
    assert "但你有未儲存的編輯" in message
    assert window._editor.toPlainText() == "local draft newer than the shadow"
    assert window._editor.document().isModified() is True
    assert note.read_text(encoding="utf-8") == (
        "disk version two from another process"
    )
    assert window._loaded_signature == window._file_signature(note)
    window._active_edit_backend = edit_backend.SPLIT_BACKEND


def test_wysiwyg_unsaved_docx_export_uses_the_live_buffer_not_disk(
    make_data_safety_window, tmp_path, monkeypatch
):
    """v4: exporting from WYSIWYG with unsaved edits feeds the buffer.

    ``export_docx`` used to unconditionally read the file back off disk and
    refuse entirely while ``_edit_mode`` was set; this pins the relaxed
    WYSIWYG-only path in app/export_actions.py: the text handed to
    ``export_markdown_to_docx`` is the live (unsaved) editor buffer, never
    the stale on-disk content. ``docx_export.py`` itself needs the
    ``python-docx`` package, which is not guaranteed installed in every test
    environment (see tests/test_docx_export.py's own skip), so this stubs
    ``app.docx_export`` in sys.modules to capture the text argument rather
    than depending on that package.
    """
    note = tmp_path / "wysiwyg-export.md"
    note.write_text("# disk version\n", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # toggle into wysiwyg below
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    window._wysiwyg_view.type_markdown("# buffer version (unsaved)\n")
    assert window._editor.is_modified() is True
    # Never actually written to disk -- this is the crux of the test.
    assert note.read_text(encoding="utf-8") == "# disk version\n"

    captured = {}
    fake_module = types.ModuleType("app.docx_export")

    def fake_export_markdown_to_docx(text, path, **_kwargs):
        captured["text"] = text
        Path(path).write_text("stub docx bytes", encoding="utf-8")

    fake_module.export_markdown_to_docx = fake_export_markdown_to_docx
    monkeypatch.setitem(sys.modules, "app.docx_export", fake_module)

    out_path = tmp_path / "exported.docx"
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(out_path), ""),
    )

    export_actions.export_docx(window)

    assert out_path.exists()
    assert captured["text"] == "# buffer version (unsaved)\n"
    assert "disk version" not in captured["text"]


def test_wysiwyg_html_export_writes_vditor_get_html_output(
    make_data_safety_window, tmp_path, monkeypatch
):
    """v4: HTML export has no Python render pipeline -- it writes Vditor's own
    ``getHTML()`` output (here, the fake WysiwygView's ``get_html`` stub)."""
    note = tmp_path / "wysiwyg-html.md"
    note.write_text("# original\n", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    window._toggle_edit_backend()
    window._wysiwyg_view.type_markdown("# edited\n")

    window._wysiwyg_view.get_html = lambda cb: cb("<h1>edited</h1>")

    out_path = tmp_path / "exported.html"
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(out_path), ""),
    )

    export_actions.export_html(window)

    assert out_path.read_text(encoding="utf-8") == "<h1>edited</h1>"


def test_export_blocked_in_split_backend_edit_mode(
    make_data_safety_window, tmp_path, monkeypatch
):
    """The split/source-code editing guard is unchanged: exports stay blocked."""
    note = tmp_path / "split-export.md"
    note.write_text("# content\n", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, note)
    assert window._active_edit_backend == edit_backend.SPLIT_BACKEND

    called = []
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: called.append(True) or ("", ""),
    )

    export_actions.export_docx(window)
    export_actions.export_html(window)
    assert called == []  # never even reached the save dialog


def test_txt_files_always_force_the_split_backend(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "plain.txt"
    note.write_text("plain text content", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.WYSIWYG_BACKEND  # user's global default

    window.open_path(str(note))
    if not window._edit_mode:
        window._toggle_edit_mode()

    assert window._current_kind == "text"
    assert window._active_edit_backend == edit_backend.SPLIT_BACKEND
    assert window._stack.currentWidget() is window._editor_split
    assert window._wysiwyg_btn.isEnabled() is False
    # The toggle shortcut/button is a no-op outside Markdown edit mode.
    window._toggle_edit_backend()
    assert window._active_edit_backend == edit_backend.SPLIT_BACKEND


# ── Explicit dual editor routes ─────────────────────────────────────────

def test_fresh_markdown_source_shortcuts_never_enter_office(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "source-routes.md"
    note.write_text("# source\n", encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(note))

    window._toggle_edit_mode()

    assert window._view_mode == view_mode.EDIT
    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert window._stack.currentWidget() is window._editor_split
    assert window._wysiwyg_view is None
    assert window._editor_mode_badge.text() == "原始 Markdown"
    assert window._tab_bar.tabText(0) == "source-routes.md"
    assert window._tab_bar.mode_badge_text(0) == "MD"

    window._toggle_edit_mode()
    assert window._view_mode == view_mode.PREVIEW

    window._toggle_split_mode()
    assert window._view_mode == view_mode.SPLIT
    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert window._editor_mode_badge.text() == "原始 Markdown · 並排"
    assert window._wysiwyg_view is None


def test_office_shortcut_owns_a_separate_preview_route(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "office-route.md"
    note.write_text("# office\n", encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(note))

    window._toggle_office_mode()

    assert window._view_mode == view_mode.EDIT
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._stack.currentWidget() is window._wysiwyg_view
    assert window._editor_mode_badge.text() == "Office 視覺編輯"
    assert window._wysiwyg_btn.isChecked() is True
    assert window._tab_bar.tabText(0) == "office-route.md"
    assert window._tab_bar.mode_badge_text(0) == "Office"

    window._toggle_office_mode()

    assert window._view_mode == view_mode.PREVIEW
    assert window._stack.currentWidget() is window._renderer
    assert window._wysiwyg_btn.isChecked() is False
    assert window._tab_bar.tabText(0) == "office-route.md"
    assert window._tab_bar.mode_badge_text(0) == "MD"


def test_each_markdown_document_restores_its_remembered_editor(
    make_data_safety_window, tmp_path
):
    office_note = tmp_path / "remember-office.md"
    source_note = tmp_path / "remember-source.md"
    office_note.write_text("Office", encoding="utf-8")
    source_note.write_text("Source", encoding="utf-8")
    window = make_data_safety_window()

    window.open_path(str(office_note))
    window._toggle_office_mode()
    window.open_path(str(source_note))
    window._toggle_edit_mode()
    window._toggle_edit_mode()

    assert [window._tab_bar.tabText(i) for i in range(2)] == [
        "remember-office.md",
        "remember-source.md",
    ]
    assert [window._tab_bar.mode_badge_text(i) for i in range(2)] == [
        "Office",
        "MD",
    ]

    assert session_state.load_document_edit_backend(office_note) == (
        edit_backend.WYSIWYG_BACKEND
    )
    assert session_state.load_document_edit_backend(source_note) == (
        edit_backend.SOURCE_BACKEND
    )

    reopened = make_data_safety_window()
    reopened.open_path(str(office_note))
    reopened.open_path(str(source_note))

    assert reopened._tab_state[str(office_note)]["edit_backend"] == (
        edit_backend.WYSIWYG_BACKEND
    )
    assert reopened._tab_state[str(source_note)]["edit_backend"] == (
        edit_backend.SOURCE_BACKEND
    )
    assert [reopened._tab_bar.tabText(i) for i in range(2)] == [
        "remember-office.md",
        "remember-source.md",
    ]
    assert [reopened._tab_bar.mode_badge_text(i) for i in range(2)] == [
        "MD",
        "MD",
    ]


@pytest.mark.parametrize(
    ("route", "expected_mode"),
    [
        ("_toggle_edit_mode", view_mode.EDIT),
        ("_toggle_split_mode", view_mode.SPLIT),
    ],
)
def test_office_to_source_waits_for_the_latest_snapshot(
    make_data_safety_window, tmp_path, monkeypatch, route, expected_mode
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / f"office-to-{expected_mode}.md"
    note.write_text("old shadow", encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(note))
    window._toggle_office_mode()
    view = window._wysiwyg_view
    view.edit_without_push("latest visible Office text")

    getattr(window, route)()

    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._stack.currentWidget() is view
    assert len(view.snapshot_requests) == 1
    assert window._tab_bar.tabText(0) == note.name
    assert window._tab_bar.mode_badge_text(0) == "Office"

    view.deliver_snapshot()
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._tab_bar.tabText(0) == f"● {note.name}"
    assert window._tab_bar.mode_badge_text(0) == "Office"
    view.deliver_acknowledgement()

    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert window._view_mode == expected_mode
    assert window._stack.currentWidget() is window._editor_split
    assert window._editor.toPlainText() == "latest visible Office text"
    assert window._editor.document().isModified() is True
    assert window._tab_bar.tabText(0) == f"● {note.name}"
    assert window._tab_bar.mode_badge_text(0) == "MD"


def test_office_compatibility_warning_cancels_safely_and_acks_one_revision(
    make_data_safety_window, tmp_path, monkeypatch
):
    note = tmp_path / "office-risk.md"
    original = "---\ntitle: Risky\n---\n\n> [!NOTE] Keep me\n"
    note.write_text(original, encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(note))
    answers = [
        window_mod.QMessageBox.StandardButton.Cancel,
        window_mod.QMessageBox.StandardButton.Open,
        window_mod.QMessageBox.StandardButton.Open,
    ]
    prompts = []

    def answer_warning(*args, **_kwargs):
        prompts.append(args[1:3])
        return answers.pop(0)

    monkeypatch.setattr(window_mod.QMessageBox, "warning", answer_warning)

    window._toggle_office_mode()

    assert window._view_mode == view_mode.PREVIEW
    assert window._wysiwyg_view is None
    assert note.read_text(encoding="utf-8") == original
    assert len(prompts) == 1
    assert session_state.load_document_edit_backend(note) is None

    window._toggle_office_mode()
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert len(prompts) == 2
    assert session_state.load_document_edit_backend(note) == (
        edit_backend.WYSIWYG_BACKEND
    )
    assert "front matter" in prompts[-1][1]
    assert "Obsidian callouts" in prompts[-1][1]

    # Reopening the exact acknowledged revision does not nag again.
    window._toggle_office_mode()
    window._toggle_office_mode()
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert len(prompts) == 2

    # A source edit changes the fingerprint, so the next Office entry asks
    # again instead of silently treating new content as acknowledged.
    window._toggle_office_mode()
    window._toggle_edit_mode()
    window._editor.setPlainText(original + "[[Another risk]]\n")
    window._editor.document().setModified(True)
    assert window._save_edits() is True
    window._toggle_edit_mode()
    window._toggle_office_mode()

    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert len(prompts) == 3
    assert "wiki-links" in prompts[-1][1]


def test_failed_office_to_source_snapshot_keeps_the_remembered_office_route(
    make_data_safety_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    note = tmp_path / "office-source-timeout.md"
    note.write_text("Office", encoding="utf-8")
    window = make_data_safety_window()
    window.open_path(str(note))
    window._toggle_office_mode()
    view = window._wysiwyg_view
    existing_timers = set(window.findChildren(QTimer))

    window._toggle_edit_mode()

    snapshot_timers = [
        timer
        for timer in window.findChildren(QTimer)
        if timer not in existing_timers and timer.isActive()
    ]
    assert len(snapshot_timers) == 1
    snapshot_timers[0].timeout.emit()

    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._stack.currentWidget() is view
    assert session_state.load_document_edit_backend(note) == (
        edit_backend.WYSIWYG_BACKEND
    )


def test_source_route_preserves_markdown_bom_crlf_and_trailing_whitespace(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "source-bytes.md"
    original_text = "---\r\ntitle: Exact\r\n---\r\n\r\nline  \r\n\r\n"
    note.write_bytes(original_text.encode("utf-8-sig"))
    window = make_data_safety_window()
    window.open_path(str(note))
    window._toggle_edit_mode()

    window._editor.setPlainText(
        window._editor.toPlainText().replace("title: Exact", "title: Exact edit")
    )
    window._editor.document().setModified(True)
    assert window._save_edits() is True

    expected = original_text.replace("title: Exact", "title: Exact edit")
    assert note.read_bytes() == expected.encode("utf-8-sig")


# ---------------------------------------------------------------------------
# v2: PREVIEW double-click -> WYSIWYG -> Esc -> back to PREVIEW, buffer intact
# (acceptance condition b).

def test_double_click_from_preview_enters_wysiwyg_directly(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "dblclick.md"
    note.write_text("# hello\n", encoding="utf-8")
    window = make_data_safety_window()
    window._preview_double_click = edit_backend.PREVIEW_DOUBLE_CLICK_WYSIWYG
    window.open_path(str(note))
    assert window._edit_mode is False  # still in PREVIEW

    window._on_preview_wysiwyg_edit_requested(0)

    assert window._edit_mode is True
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._stack.currentWidget() is window._wysiwyg_view
    assert window._wysiwyg_view.loaded[-1] == "# hello\n"


def test_double_click_ignored_when_preference_is_inline(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "dblclick-inline.md"
    note.write_text("# hello\n", encoding="utf-8")
    window = make_data_safety_window()
    window._preview_double_click = edit_backend.PREVIEW_DOUBLE_CLICK_INLINE
    window.open_path(str(note))

    window._on_preview_wysiwyg_edit_requested(0)

    assert window._edit_mode is False  # v1 behaviour: nothing happens


def test_wysiwyg_esc_returns_to_preview_keeping_the_dirty_buffer(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "dblclick-esc.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    window._preview_double_click = edit_backend.PREVIEW_DOUBLE_CLICK_WYSIWYG
    window.open_path(str(note))
    window._on_preview_wysiwyg_edit_requested(0)
    document = window._editor.document()

    window._wysiwyg_view.type_markdown("start, typed in WYSIWYG")
    assert document.toPlainText() == "start, typed in WYSIWYG"
    assert document.isModified() is True

    window._wysiwyg_view.press_esc()

    # Esc leaves WYSIWYG straight back to PREVIEW: no dialog, no discard.
    assert window._edit_mode is False
    assert window._stack.currentWidget() is window._renderer
    assert window._tab_state[str(note)]["editor_document"] is document
    assert document.isModified() is True
    assert document.toPlainText() == "start, typed in WYSIWYG"

    # The explicit Office route resumes the same unsaved text, still dirty.
    window._toggle_office_mode()
    assert window._edit_mode is True
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._wysiwyg_view.loaded[-1] == "start, typed in WYSIWYG"
    assert window._editor.document().isModified() is True

    # Ctrl+S afterwards saves and cleans the buffer as usual.
    assert window._save_edits() is True
    assert note.read_text(encoding="utf-8") == "start, typed in WYSIWYG"
    assert window._editor.document().isModified() is False


def test_wysiwyg_esc_is_a_noop_outside_the_wysiwyg_backend(
    make_data_safety_window, tmp_path
):
    """Guards against a stray/late esc_requested reaching a split-backend tab."""
    note = tmp_path / "not-wysiwyg.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND  # stay on the split backend
    _enter_markdown_editor(window, note)
    assert window._active_edit_backend == edit_backend.SPLIT_BACKEND

    window._on_wysiwyg_esc()

    assert window._edit_mode is True  # untouched
    assert window._stack.currentWidget() is window._editor_split


def _esc_parked_dirty_tab(window, note):
    """Open *note*, edit it in WYSIWYG, then Esc back to PREVIEW (parked)."""
    window._preview_double_click = edit_backend.PREVIEW_DOUBLE_CLICK_WYSIWYG
    window.open_path(str(note))
    window._on_preview_wysiwyg_edit_requested(0)
    document = window._editor.document()
    window._wysiwyg_view.type_markdown("TYPED-UNSAVED")
    window._wysiwyg_view.press_esc()
    assert window._tab_state[str(note)]["wysiwyg_parked"] is True
    return document


def test_wysiwyg_esc_parked_tab_survives_a_tab_round_trip_as_preview(
    make_data_safety_window, tmp_path
):
    """A tab Esc'd out of WYSIWYG must stay on PREVIEW after switching away
    and back -- tab_state["view_mode"] is left at EDIT/SPLIT on purpose (so
    Ctrl+E/double-click can resume the parked buffer), so restoring the tab
    must not treat that as "re-open the editor"."""
    a = tmp_path / "roundtrip-a.md"
    a.write_text("start", encoding="utf-8")
    b = tmp_path / "roundtrip-b.md"
    b.write_text("other", encoding="utf-8")
    window = make_data_safety_window()
    document = _esc_parked_dirty_tab(window, a)
    assert window._stack.currentWidget() is window._renderer

    window.open_path(str(b))
    window.open_path(str(a))  # back to the Esc'd tab

    assert window._stack.currentWidget() is window._renderer
    assert window._view_mode == view_mode.PREVIEW
    assert document.isModified() is True
    assert document.toPlainText() == "TYPED-UNSAVED"

    # The explicit Office shortcut resumes the parked buffer; Ctrl+E now owns
    # the separate original-Markdown route.
    window._toggle_office_mode()
    assert window._edit_mode is True
    assert window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert window._wysiwyg_view.loaded[-1] == "TYPED-UNSAVED"
    assert "wysiwyg_parked" not in window._tab_state[str(a)]


def test_inline_preview_edit_is_blocked_by_a_parked_dirty_wysiwyg_buffer(
    make_data_safety_window, tmp_path
):
    """Esc leaves self._view_mode == PREVIEW, so the ordinary inline-edit
    guard (view_mode-based) would let the preview write straight to disk
    behind the parked, unsaved editor buffer. wysiwyg_parked must close
    that hole for paragraph edits, table edits and the task-checkbox
    write-back alike, since they all route through _inline_edit_context /
    the same view_mode-only check."""
    note = tmp_path / "parked-inline.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    document = _esc_parked_dirty_tab(window, note)
    assert window._view_mode == view_mode.PREVIEW

    assert window._inline_edit_context() is None

    reply = window._inline_edit_commit(
        0, 0, "start", "REWRITTEN-ON-DISK", window._inline_edit_signature()
    )
    assert reply == {"ok": False, "error": "unavailable"}
    assert note.read_text(encoding="utf-8") == "start"
    assert document.toPlainText() == "TYPED-UNSAVED"

    # The task-checkbox write-back shares the same hole; it must also be shut.
    window._on_task_toggled(0, True)
    assert note.read_text(encoding="utf-8") == "start"


def test_window_title_shows_dirty_marker_for_a_parked_wysiwyg_buffer(
    make_data_safety_window, tmp_path
):
    note = tmp_path / "parked-title.md"
    note.write_text("start", encoding="utf-8")
    window = make_data_safety_window()
    _esc_parked_dirty_tab(window, note)

    assert window.windowTitle().startswith("● ")
    assert window._toolbar_title.text().startswith("● ")
    assert window._tab_bar.tabText(0) == "● parked-title.md"
    assert window._tab_bar.mode_badge_text(0) == "MD"

    # Saving (via the resumed Office editor) clears all three markers together.
    window._toggle_office_mode()
    assert window._save_edits() is True
    assert not window.windowTitle().startswith("● ")
    assert window._tab_bar.tabText(0) == "parked-title.md"
    assert window._tab_bar.mode_badge_text(0) == "Office"
