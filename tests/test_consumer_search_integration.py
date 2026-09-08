"""Search results must navigate the actual document workspace."""
from types import SimpleNamespace

from app import edit_backend, window as window_mod
from tests.test_editor_data_safety import (
    _isolated_editor_dependencies,
    make_data_safety_window,
    _DelayedSnapshotWysiwygView,
)
from tests.test_editor_workspace_integration import _enter_markdown_editor


def test_text_search_selects_requested_duplicate_without_modifying_source(make_data_safety_window, tmp_path):
    path = tmp_path / "note.txt"
    content = "needle first\nother\nneedle second\n"
    path.write_text(content, encoding="utf-8")
    window = make_data_safety_window()
    window._open_global_search_result(str(path), "needle", 3)
    assert window._editor.textCursor().blockNumber() == 2
    assert window._editor.textCursor().selectedText() == "needle"
    assert window._editor_search_bar.isVisibleTo(window)
    assert not window._editor.document().isModified()
    assert path.read_text(encoding="utf-8") == content


def test_source_mode_search_uses_live_buffer_without_losing_unsaved_content(make_data_safety_window, tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# First\nneedle\n", encoding="utf-8")
    window = make_data_safety_window()
    _enter_markdown_editor(window, path)
    window._editor.insertPlainText("unsaved\n")
    document = window._editor.document()
    before = document.toPlainText()
    window._open_global_search_result(str(path), "needle", 3)
    assert window._editor.document() is document
    assert document.toPlainText() == before
    assert document.isModified()
    assert window._editor.textCursor().selectedText() == "needle"


def test_search_waits_for_office_snapshot_then_selects_target_text(
    make_data_safety_window, tmp_path, monkeypatch,
):
    monkeypatch.setattr(window_mod, "WysiwygView", _DelayedSnapshotWysiwygView)
    source = tmp_path / "office.md"
    source.write_text("original", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("other\nneedle", encoding="utf-8")
    window = make_data_safety_window()
    window._edit_backend = edit_backend.SPLIT_BACKEND
    _enter_markdown_editor(window, source)
    window._toggle_edit_backend()
    view = window._wysiwyg_view
    view.edit_without_push("last Office keystroke")
    window._open_global_search_result(str(target), "needle", 2)
    assert window._active_path == str(source)
    view.deliver_snapshot()
    view.deliver_acknowledgement()
    assert window._active_path == str(target)
    assert window._tab_state[str(source)]["editor_document"].toPlainText() == "last Office keystroke"
    assert window._editor.textCursor().selectedText() == "needle"


def test_quick_open_includes_unopened_library_files_without_enumerating_disk(tmp_path):
    path = tmp_path / "not-enumerated" / "nested" / "note.md"
    browser = SimpleNamespace(quick_open_documents=lambda: [(path.name, str(path))],
                              contains_library_path=lambda _path: True)
    stub = SimpleNamespace(_panel=SimpleNamespace(file_browser=browser,
                           recent=SimpleNamespace(paths=lambda: [])), _current_file=path)
    assert window_mod.MainWindow._quick_open_candidates(stub) == [(path.name, str(path))]


def test_search_roots_keep_offline_libraries(make_data_safety_window, tmp_path):
    window = make_data_safety_window()
    missing = tmp_path / "offline-library"
    window._panel.file_browser.configured_library_roots = lambda: [missing]
    assert window._search_roots() == [missing]
