from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QKeyEvent

from app.command_palette import CommandPalette
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
    md_files,
)


def test_focus_restores_prior_visibility_and_live_buffer(make_window, md_files):  # noqa: F811
    window = make_window()
    window._open_file(str(md_files[0]))
    window._toggle_edit_mode()
    window._editor.insertPlainText("中文草稿 😀")
    before = window._editor.toPlainText()
    window._panel.hide()
    controls = window._workspace_controls
    controls.toggle_focus()
    assert controls.focused
    assert window._toolbar.isHidden()
    assert not controls.focus_rail.isHidden()
    controls.toggle_focus()
    assert not window._toolbar.isHidden()
    assert window._panel.isHidden()
    assert window._editor.toPlainText() == before
    assert window._editor.is_modified()


def test_workspace_save_reports_disk_result_and_keeps_mode(make_window, md_files):  # noqa: F811
    window = make_window()
    window._open_file(str(md_files[0]))
    window._toggle_edit_mode()
    window._editor.insertPlainText("新內容")
    controls = window._workspace_controls
    assert controls.save_button.text() == "儲存變更"
    controls.save()
    assert controls.save_button.text() == "已儲存"
    assert "新內容" in md_files[0].read_text(encoding="utf-8")
    assert window._edit_mode


def test_layout_toggle_preserves_commands_and_mode(make_window, md_files):  # noqa: F811
    window = make_window()
    window._open_file(str(md_files[0]))
    controls = window._workspace_controls
    assert window._export_btn.isHidden()
    controls.set_compact(False)
    assert not window._export_btn.isHidden()
    assert controls.more_menu.actions()
    controls.mode.setCurrentIndex(1)
    controls._select_mode(1)
    assert window._edit_mode
    assert not controls.backend.isHidden()


def test_palette_filters_chinese_and_never_executes_disabled_action(qapp):
    save = QAction("儲存\tCtrl+S")
    remove = QAction("刪除")
    remove.setEnabled(False)
    palette = CommandPalette([("檔案 › 儲存", save), ("編輯 › 刪除", remove)])
    palette.query.setText("刪除")
    assert palette.results.count() == 1
    palette._accept_item(palette.results.item(0))
    assert palette.selected_action is None
    palette.query.setText("儲存")
    palette.eventFilter(palette.query, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier
    ))
    assert palette.selected_action is save
    palette.deleteLater()


def test_pinned_tab_survives_bulk_close_and_unpins(make_window, md_files):  # noqa: F811
    window = make_window()
    for path in md_files[:3]:
        window._open_file(str(path))
    pinned = str(md_files[0])
    window._toggle_tab_pin(pinned)
    window._close_other_tabs(str(md_files[1]))
    assert window._index_of_path(pinned) >= 0
    assert not window._close_tab_by_path(pinned)
    window._toggle_tab_pin(pinned)
    assert window._close_tab_by_path(pinned)


def test_palette_leaves_ime_candidate_navigation_unhandled(qapp):
    from PySide6.QtGui import QInputMethodEvent
    action = QAction("儲存")
    palette = CommandPalette([("儲存", action)])
    palette.eventFilter(palette.query, QInputMethodEvent("ㄓ", []))
    assert not palette.eventFilter(palette.query, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert palette.selected_action is None
    palette.eventFilter(palette.query, QInputMethodEvent("", []))
    palette.deleteLater()
