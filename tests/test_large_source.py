"""Large Markdown enters an explicit, complete and cancellable source route."""

import time

from PySide6.QtTest import QTest

from app import edit_backend, large_source, view_mode
from tests.test_editor_workspace_integration import (  # noqa: F401
    _isolated_workspace_dependencies,
    make_workspace_window,
)


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not predicate():
        QTest.qWait(10)
    assert predicate()


def test_large_markdown_loads_complete_utf16_source_and_saves_without_truncation(
    make_workspace_window, tmp_path, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr(large_source, "MAX_PREVIEW_BYTES", 1024)
    source = tmp_path / "large.md"
    text = "# 完整來源\n" + "繁中與 emoji 😀\n" * 1000 + "END-SENTINEL\n"
    original = text.replace("\n", "\r\n").encode("utf-16")
    source.write_bytes(original)
    window = make_workspace_window()
    window.open_path(str(source))
    assert window._open_source_editor(view_mode.SPLIT)
    wait_until(lambda: window._edit_mode)
    assert window._view_mode == view_mode.EDIT
    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert window._editor.toPlainText() == text
    assert not window._large_source_notice.isHidden()
    assert "全文" in window._large_source_notice.text()
    assert window._editor._markdown_services_suspended
    assert not window._workspace_controls.backend.isEnabled()
    assert source.read_bytes() == original
    window._editor.moveCursor(window._editor.textCursor().MoveOperation.End)
    window._editor.insertPlainText("最後修改")
    assert window._save_edits()
    assert source.read_bytes().decode("utf-16") == (text + "最後修改").replace("\n", "\r\n")
    assert window._editor.toPlainText().endswith("END-SENTINEL\n最後修改")


def test_large_office_request_uses_source_and_normal_document_restores_services(
    make_workspace_window, tmp_path, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr(large_source, "MAX_PREVIEW_BYTES", 1024)
    source, small = tmp_path / "large.md", tmp_path / "small.md"
    source.write_text("# Long\n" + "line\n" * 500, encoding="utf-8")
    small.write_text("# Small", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(source))
    assert window._open_office_editor()
    wait_until(lambda: window._edit_mode)
    assert window._active_edit_backend == edit_backend.SOURCE_BACKEND
    window.open_path(str(small))
    assert window._open_source_editor()
    assert window._large_source_notice.isHidden()
    assert not window._editor._markdown_services_suspended
    assert window._workspace_controls.backend.isEnabled()


def test_large_background_read_can_be_abandoned_without_installing_old_buffer(
    make_workspace_window, tmp_path, monkeypatch,  # noqa: F811
):
    monkeypatch.setattr(large_source, "MAX_PREVIEW_BYTES", 1024)
    source, small = tmp_path / "large.md", tmp_path / "small.md"
    source.write_text("# Long\n" + "line\n" * 500, encoding="utf-8")
    small.write_text("# Current", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(source))
    assert window._open_source_editor()
    window.open_path(str(small))
    assert window._open_source_editor()
    QTest.qWait(100)
    assert window._current_file == small
    assert window._editor.toPlainText() == "# Current"
    assert not window._tab_state[str(source)].get("editor_document")
