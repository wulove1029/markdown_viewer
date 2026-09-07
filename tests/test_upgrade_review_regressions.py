"""Independent regressions for issues found during upgrade review."""
import threading

from PySide6.QtGui import QTextCursor

from tests.test_editor_workspace_integration import (
    _isolated_workspace_dependencies,
    make_workspace_window,
    _enter_markdown_editor,
)


def test_reading_mode_selector_preserves_live_source_selection(
    make_workspace_window, tmp_path,
):
    window = make_workspace_window()
    path = tmp_path / "selection.md"
    path.write_text("# Header\n" + "A long body line\n" * 30, encoding="utf-8")
    _enter_markdown_editor(window, path)
    document = window._editor.document()
    cursor = window._editor.textCursor()
    cursor.setPosition(100)
    cursor.setPosition(120, QTextCursor.MoveMode.KeepAnchor)
    window._editor.setTextCursor(cursor)

    for mode in ("split", "edit", "edit"):
        window._select_reading_mode(window._reading_mode_combo.findData(mode))
        assert window._view_mode == mode
        assert window._editor.document() is document
        cursor = window._editor.textCursor()
        assert (cursor.anchor(), cursor.position()) == (100, 120)


def test_render_disk_io_does_not_hold_ui_stylesheet_lock(qapp, tmp_path, monkeypatch):
    from app import md_converter as converter
    from app.renderer import _MarkdownRenderWorker

    path = tmp_path / "slow.md"
    path.write_text("# Slow disk", encoding="utf-8")
    entered_read = threading.Event()
    release_read = threading.Event()
    original_read = converter.read_text

    def controlled_read(path):
        entered_read.set()
        assert release_read.wait(5), "test did not release disk read"
        return original_read(path)

    monkeypatch.setattr(converter, "read_text", controlled_read)
    worker = _MarkdownRenderWorker(1, path=path)
    thread = threading.Thread(target=worker.run)
    thread.start()
    try:
        assert entered_read.wait(5), "worker never reached disk read"
        # The read is still blocked: this checks ownership, not a speed target.
        acquired = converter._CONVERT_LOCK.acquire(blocking=False)
        assert acquired, "disk IO holds the lock required by UI set_user_css"
        try:
            converter.set_user_css(converter._user_css)
        finally:
            converter._CONVERT_LOCK.release()
    finally:
        release_read.set()
        thread.join(5)
    assert not thread.is_alive()
