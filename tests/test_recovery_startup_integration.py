"""Real window/editor recovery integration with isolated settings and renderers."""

import pytest

from app import session_state
from app import window as window_mod
from app.recovery_browser import pending_recovery_snapshots
from tests.test_editor_workspace_integration import (
    _isolated_workspace_dependencies,
    make_workspace_window,
)
from tests.test_startup_recovery import save_draft


def choose_recovery(monkeypatch, choices):
    calls = []
    decisions = iter(choices)

    class Dialog:
        RESTORE = "restore"
        DISCARD = "discard"
        LATER = "later"

        def __init__(self, source, disk, draft, updated_at, parent):
            calls.append((str(source), disk, draft))
            self.choice = next(decisions)

        def exec(self):
            return None

    monkeypatch.setattr(window_mod, "RecoveryDialog", Dialog)
    return calls


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_later_then_review_same_active_path_restores_draft(
    make_workspace_window, tmp_path, monkeypatch, suffix,
):
    source = tmp_path / f"later{suffix}"
    source.write_text("disk version", encoding="utf-8")
    window = make_workspace_window()
    save_draft(window._recovery_store, source, "recovered draft")
    calls = choose_recovery(monkeypatch, ["later", "restore"])

    window.open_path(str(source))
    assert window._active_path == str(source)
    assert window._tab_state[str(source)]["pending_recovery"]
    assert [item.source_path for item in pending_recovery_snapshots(window)] == [str(source)]
    browser = session_state.show_pending_recovery(window)
    browser._review_selected()

    assert len(calls) == 2
    assert window._editor.toPlainText() == "recovered draft"
    assert window._editor.document().isModified()
    assert not window._tab_state[str(source)].get("pending_recovery")
    assert not browser.isVisible()
    assert source.read_text(encoding="utf-8") == "disk version"


def test_review_preserves_live_document_and_undo_history(
    make_workspace_window, tmp_path, monkeypatch,
):
    source = tmp_path / "live.md"
    source.write_text("disk version", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(source))
    window._toggle_edit_mode()
    window._editor.insertPlainText("new live content ")
    window._stash_active_editor_state(snapshot=False)
    document = window._editor.document()
    original_text = document.toPlainText()
    original_undo = document.isUndoAvailable()
    save_draft(window._recovery_store, source, "older stale draft")
    calls = choose_recovery(monkeypatch, [])

    window._review_recovery_path(str(source))

    assert calls == []
    assert window._editor.document() is document
    assert document.toPlainText() == original_text
    assert document.isUndoAvailable() == original_undo
    assert source.read_text(encoding="utf-8") == "disk version"


def test_deferred_markdown_cannot_overwrite_snapshot_by_starting_a_new_edit(
    make_workspace_window, tmp_path, monkeypatch,
):
    source = tmp_path / "deferred.md"
    source.write_text("disk version", encoding="utf-8")
    other = tmp_path / "other.md"
    other.write_text("another document", encoding="utf-8")
    window = make_workspace_window()
    save_draft(window._recovery_store, source, "protected older draft")
    calls = choose_recovery(monkeypatch, ["later", "later"])

    window.open_path(str(source))
    window._toggle_edit_mode()

    assert len(calls) == 2
    assert not window._edit_mode
    assert window._tab_state[str(source)]["pending_recovery"]
    assert window._tab_state[str(source)].get("editor_document") is None
    window.open_path(str(other))
    assert window._recovery_store.load(source).draft == "protected older draft"
    assert source.read_text(encoding="utf-8") == "disk version"


@pytest.mark.parametrize("suffix", [".md", ".txt"])
def test_empty_draft_for_missing_source_can_be_restored(
    make_workspace_window, tmp_path, monkeypatch, suffix,
):
    source = tmp_path / f"deleted{suffix}"
    window = make_workspace_window()
    save_draft(window._recovery_store, source, "")
    calls = choose_recovery(monkeypatch, ["restore"])

    session_state.restore_startup(window)
    window._recovery_browser._review_selected()

    assert len(calls) == 1
    assert window._editor.toPlainText() == ""
    assert window._editor.document().isModified()
    assert window._recovery_store.load(source).draft == ""
    assert not source.exists()
