from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from app.drafts import is_draft
from app.reading_tools import Excerpt, excerpts_markdown
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


def test_unnamed_draft_survives_cancel_and_first_save(make_window, tmp_path, monkeypatch):  # noqa: F811
    window = make_window()
    window._new_draft("# 草稿\n\n中文 😀\n")
    original = str(window._current_file)
    document = window._editor.document()
    assert is_draft(window, original)
    assert document.isModified()
    assert "未命名草稿" in window._tab_bar.tabText(window._tab_bar.currentIndex())
    assert window._recovery_store.load(original) is not None
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: ("", ""))
    assert not window._save_edits()
    assert str(window._current_file) == original
    assert document.isModified()
    target = tmp_path / "正式筆記.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), ""))
    assert window._save_edits()
    assert window._current_file == target
    assert window._editor.document() is document
    assert not document.isModified()
    assert target.read_text(encoding="utf-8") == "# 草稿\n\n中文 😀\n"
    assert window._tab_state[str(target)]["source_signature"] == window._file_signature(target)


def test_excerpts_preserve_multiline_text_and_source_page(tmp_path):
    source = tmp_path / "空白 與#括號].pdf"
    result = excerpts_markdown(source, [Excerpt("a", "第一行\n第二行", "心得", ("研究",), 12)])
    assert "> 第一行\n> 第二行" in result
    assert "第 13 頁" in result
    assert "#page=13>" in result
    assert "%23" in result
    assert "心得" in result and "研究" in result


def test_search_overlay_uses_dirty_live_document(make_window, md_files):  # noqa: F811
    window = make_window()
    window._open_file(str(md_files[0]))
    window._toggle_edit_mode()
    window._editor.insertPlainText("未存內容")
    overlay = window._search_buffer_overlays()
    assert "未存內容" in overlay[str(md_files[0])]
    assert "未存內容" not in Path(md_files[0]).read_text(encoding="utf-8")


def test_reading_status_resets_for_document_and_hides_during_writing(
    make_window, md_files,  # noqa: F811
):
    window = make_window()
    window._open_file(str(md_files[0]))
    tools = window._reading_tools
    tools._progress_changed(0.6)
    assert "60%" in tools.status.text()
    window._open_file(str(md_files[1]))
    assert "0%" in tools.status.text()
    assert not tools.status.isHidden()
    window._toggle_edit_mode()
    assert tools.status.isHidden()
    tools.dock.show()
    window._properties_dock.show()
    window.show()
    assert window._properties_dock in window.tabifiedDockWidgets(tools.dock)
