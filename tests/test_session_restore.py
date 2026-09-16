"""Real window/session coordination, with isolated settings and file fixtures."""

import json
import os

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest

from app import session_state, window as window_mod
from tests.test_editor_workspace_integration import (
    _isolated_workspace_dependencies,
    make_workspace_window,
)


def paths(window):
    return [window._tab_bar.tabData(i) for i in range(window._tab_bar.count())]


def settings():
    return session_state.QSettings("markdown-viewer", "MarkdownViewer")


@pytest.fixture
def documents(tmp_path):
    files = [tmp_path / f"document-{i}.md" for i in range(4)]
    for path in files:
        path.write_text(f"# {path.stem}\n", encoding="utf-8")
    return files


def remember(files, active=0):
    settings().setValue("open_tabs", json.dumps([str(p) for p in files]))
    settings().setValue("active_tab", active)


def test_file_launch_keeps_previous_tabs_and_only_loads_requested_file(
    make_workspace_window, documents
):
    remember(documents[:3], active=1)
    window = make_workspace_window()
    session_state.restore_startup(window, str(documents[3]))
    assert paths(window) == [str(p) for p in documents]
    assert window._current_file == documents[3]
    assert window._renderer.loaded_paths == [documents[3]]


def test_file_launch_reuses_a_restored_tab(make_workspace_window, documents):
    remember(documents, active=0)
    window = make_workspace_window()
    session_state.restore_startup(window, str(documents[2]))
    assert paths(window) == [str(p) for p in documents]
    assert window._tab_bar.currentIndex() == 2
    assert window._renderer.loaded_paths == [documents[2]]


def test_missing_earlier_file_does_not_change_active_document(
    make_workspace_window, documents
):
    remember(documents, active=2)
    documents[0].unlink()
    window = make_workspace_window()
    session_state.restore_startup(window)
    assert paths(window) == [str(p) for p in documents[1:]]
    assert window._current_file == documents[2]


def test_explicit_empty_session_does_not_reopen_legacy_last_file(
    make_workspace_window, documents
):
    remember([])
    settings().setValue("last_file", str(documents[0]))
    window = make_workspace_window()
    session_state.restore_startup(window)
    assert paths(window) == []
    assert window._current_file is None


def test_session_saved_while_open_without_close_event(
    make_workspace_window, documents
):
    window = make_workspace_window()
    for path in documents:
        window.open_path(str(path))
    window._tab_bar.moveTab(3, 0)
    window._tab_bar.setCurrentIndex(2)
    QTest.qWait(450)
    expected = paths(window)
    assert json.loads(settings().value("open_tabs") or "[]") == expected

    # Simulate a fresh process reading only the persisted state; the old
    # window deliberately never gets a close event before this assertion.
    restored = make_workspace_window()
    session_state.restore_startup(restored)
    assert paths(restored) == expected
    assert restored._current_file == window._current_file


def test_background_tab_close_and_explicit_close_all_are_checkpointed(
    make_workspace_window, documents
):
    window = make_workspace_window()
    for path in documents:
        window.open_path(str(path))
    assert window._on_tab_close(0)
    QTest.qWait(450)
    assert json.loads(settings().value("open_tabs")) == [str(p) for p in documents[1:]]
    while window._tab_bar.count():
        assert window._on_tab_close(0)
    QTest.qWait(450)
    assert json.loads(settings().value("open_tabs")) == []
    assert not settings().value("last_file")
    restored = make_workspace_window()
    session_state.restore_startup(restored)
    assert restored._current_file is None


def test_normal_close_flushes_before_checkpoint_timer(
    make_workspace_window, documents
):
    window = make_workspace_window()
    for path in documents:
        window.open_path(str(path))
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    assert json.loads(settings().value("open_tabs")) == paths(window)
    assert int(settings().value("active_tab")) == 3


def test_detached_window_does_not_replace_primary_checkpoint(
    make_workspace_window, documents
):
    remember(documents[:3], active=1)
    detached = make_workspace_window()
    detached._is_detached = True
    detached.open_path(str(documents[3]))
    QTest.qWait(450)
    detached.close()
    assert json.loads(settings().value("open_tabs")) == [str(p) for p in documents[:3]]
    assert int(settings().value("active_tab")) == 1


def test_restore_does_not_checkpoint_half_a_session_during_nested_events(
    make_workspace_window, documents, monkeypatch
):
    remember(documents, active=2)
    window = make_workspace_window()
    add = window._add_tab
    observed = []

    def slow_add(path, kind):
        index = add(path, kind)
        QTest.qWait(300)
        observed.append(json.loads(settings().value("open_tabs")))
        return index

    monkeypatch.setattr(window, "_add_tab", slow_add)
    session_state.restore_startup(window)
    assert observed == [[str(p) for p in documents]] * len(documents)
    assert window._current_file == documents[2]


def test_cancel_close_keeps_autosaving_and_unsaved_buffer(
    make_workspace_window, documents, monkeypatch
):
    window = make_workspace_window()
    window.open_path(str(documents[0]))
    window._toggle_edit_mode()
    window._editor.insertPlainText("unsaved ")
    original_text = documents[0].read_text(encoding="utf-8")
    with monkeypatch.context() as patch:
        patch.setattr(window, "_confirm_close_all_edits", lambda: False)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert window._editor.is_modified()
    window.open_path(str(documents[1]))
    QTest.qWait(450)
    assert json.loads(settings().value("open_tabs")) == [str(p) for p in documents[:2]]
    assert window._tab_state[str(documents[0])]["editor_document"].isModified()
    assert documents[0].read_text(encoding="utf-8") == original_text


@pytest.mark.skipif(os.name != "nt", reason="Windows file paths are case-insensitive")
def test_case_variant_cli_path_does_not_duplicate_a_restored_tab(
    make_workspace_window, documents
):
    remember(documents)
    window = make_workspace_window()
    session_state.restore_startup(window, str(documents[2]).upper())
    assert paths(window) == [str(p) for p in documents]
    assert window._current_file == documents[2]


def test_renamed_background_tab_is_saved_without_switching_tabs(
    make_workspace_window, documents
):
    window = make_workspace_window()
    window.open_path(str(documents[0]))
    window.open_path(str(documents[1]))
    moved = documents[0].with_name("renamed.md")
    documents[0].rename(moved)
    window._on_browser_paths_migrated({str(documents[0]): str(moved)})
    QTest.qWait(450)
    assert json.loads(settings().value("open_tabs")) == [str(moved), str(documents[1])]
    assert settings().value("active_tab_path") == str(documents[1])


def test_pdf_reopen_restores_each_page_including_first_page(
    make_workspace_window, tmp_path, monkeypatch, qapp
):
    pymupdf = pytest.importorskip("pymupdf")
    from app.pdf_view import PdfView

    monkeypatch.setattr(window_mod, "PdfView", PdfView)
    files = [tmp_path / f"pages-{i}.pdf" for i in range(3)]
    for index, path in enumerate(files):
        with pymupdf.open() as doc:
            for _ in range(5 - index):
                doc.new_page()
            doc.save(path)
    window = make_workspace_window()
    window.resize(800, 600)
    window.show()
    remembered = [3, 1, 0]
    for path, page in zip(files, remembered):
        window.open_path(str(path))
        qapp.processEvents()
        window._pdf_view.jump_to_page(page)
        qapp.processEvents()
    for path, page in zip(files, remembered):
        window.open_path(str(path))
        qapp.processEvents()
        assert window._pdf_view.current_page() == page
    window.close()
    restored = make_workspace_window()
    restored.resize(800, 600)
    restored.show()
    session_state.restore_startup(restored)
    qapp.processEvents()
    assert paths(restored) == [str(p) for p in files]
    for path, page in zip(files, remembered):
        restored.open_path(str(path))
        qapp.processEvents()
        assert restored._pdf_view.current_page() == page
