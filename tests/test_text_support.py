"""Plain-text (.txt) support: BOM-aware reading and the new-note dialog."""

import codecs

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app import edit_backend
from app import new_note_dialog as new_note_dialog_mod
from app.md_converter import read_text, read_text_detailed
from app.new_note_dialog import (
    NewNoteDialog,
    normalized_file_name,
    validate_new_note,
)
from app.theme import LIGHT


# ---------------- read_text_detailed ----------------
def test_read_text_detailed_plain_utf8_and_newline(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("線一\n線二\n".encode("utf-8"))
    assert read_text_detailed(path) == ("線一\n線二\n", "utf-8", "\n")


def test_read_text_detailed_detects_crlf(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"one\r\ntwo\r\n")
    assert read_text_detailed(path) == ("one\ntwo\n", "utf-8", "\r\n")


def test_read_text_detailed_utf8_bom_round_trips(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(codecs.BOM_UTF8 + "哈囉\n".encode("utf-8"))
    text, encoding, newline = read_text_detailed(path)
    assert text == "哈囉\n"  # no ﻿ leaks into the buffer
    assert encoding == "utf-8-sig"
    assert newline == "\n"
    # Re-encoding with the returned name restores the BOM byte-for-byte.
    assert text.encode(encoding) == path.read_bytes()


def test_read_text_detailed_utf16_boms_and_crlf(tmp_path):
    le = tmp_path / "le.txt"
    le.write_bytes("甲\r\n乙\r\n".encode("utf-16"))  # native BOM + CRLF
    text, encoding, newline = read_text_detailed(le)
    assert (text, encoding, newline) == ("甲\n乙\n", "utf-16", "\r\n")

    be = tmp_path / "be.txt"
    be.write_bytes(codecs.BOM_UTF16_BE + "丙".encode("utf-16-be"))
    assert read_text_detailed(be) == ("丙", "utf-16", "\n")


def test_read_text_detailed_big5_fallback(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes("繁體中文".encode("cp950"))
    text, encoding, _newline = read_text_detailed(path)
    assert text == "繁體中文"
    assert encoding == "cp950"


def test_read_text_returns_none_for_undecodable_bytes(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"\xff\xff\x00\x81\x81\xfe")
    assert read_text_detailed(path) is None
    assert read_text(path) is None


# ---------------- new-note name helpers ----------------
def test_normalized_file_name_appends_and_never_doubles_suffix():
    assert normalized_file_name("note", ".md") == "note.md"
    assert normalized_file_name("note.md", ".md") == "note.md"
    assert normalized_file_name("Note.MD", ".md") == "Note.md"
    assert normalized_file_name(" note ", ".txt") == "note.txt"
    # A different extension stays part of the stem.
    assert normalized_file_name("note.txt", ".md") == "note.txt.md"


def test_validate_new_note(tmp_path):
    assert validate_new_note(tmp_path, "note", ".md") == ""
    assert validate_new_note(tmp_path, "", ".md") != ""
    assert validate_new_note(tmp_path, "   ", ".md") != ""
    assert validate_new_note(tmp_path, "bad|name", ".md") != ""
    (tmp_path / "taken.txt").write_text("x", encoding="utf-8")
    assert "已存在" in validate_new_note(tmp_path, "taken", ".txt")
    assert "已存在" in validate_new_note(tmp_path, "taken.txt", ".txt")
    assert validate_new_note(tmp_path, "taken", ".md") == ""


# ---------------- dialog behavior ----------------
def test_dialog_creates_empty_file_with_selected_type(qapp, tmp_path):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        dialog._name_input.setText("我的筆記")
        # Switch to 純文字 (.txt).
        dialog._type_buttons[1][0].setChecked(True)
        assert dialog.selected_suffix() == ".txt"
        assert dialog.selected_editor_backend() == edit_backend.SOURCE_BACKEND
        assert dialog._editor_backend_combo.isEnabled() is False
        assert dialog.target_path() == tmp_path / "我的筆記.txt"
        dialog._attempt_create()
        created = dialog.created_path()
        assert created == tmp_path / "我的筆記.txt"
        assert created.read_bytes() == b""
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.close()


def test_dialog_offers_two_markdown_editor_routes(qapp, tmp_path):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        assert dialog.selected_suffix() == ".md"
        assert dialog.selected_editor_backend() == edit_backend.SOURCE_BACKEND
        assert dialog._editor_backend_combo.count() == 2
        assert "原始 Markdown" in dialog._editor_backend_combo.itemText(0)
        assert "Office" in dialog._editor_backend_combo.itemText(1)

        dialog._editor_backend_combo.setCurrentIndex(1)
        assert dialog.selected_editor_backend() == edit_backend.WYSIWYG_BACKEND

        dialog._type_buttons[1][0].setChecked(True)
        assert dialog.selected_editor_backend() == edit_backend.SOURCE_BACKEND
    finally:
        dialog.close()


def test_dialog_can_default_to_explicit_office_route(qapp, tmp_path):
    dialog = NewNoteDialog(
        tmp_path,
        LIGHT,
        default_backend=edit_backend.WYSIWYG_BACKEND,
    )
    try:
        assert dialog.selected_editor_backend() == edit_backend.WYSIWYG_BACKEND
    finally:
        dialog.close()


def test_dialog_duplicate_keeps_input_and_stays_open(qapp, tmp_path):
    (tmp_path / "dup.md").write_text("x", encoding="utf-8")
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        dialog._name_input.setText("dup")
        assert dialog._create_btn.isEnabled() is False
        assert "已存在" in dialog._error_label.text()
        dialog._attempt_create()
        assert dialog.created_path() is None
        assert dialog.result() != dialog.DialogCode.Accepted
        assert dialog._name_input.text() == "dup"
        # The .md name is taken but the .txt one is free.
        dialog._type_buttons[1][0].setChecked(True)
        assert dialog._create_btn.isEnabled() is True
    finally:
        dialog.close()


def test_dialog_change_folder_revalidates_and_creates_there(qapp, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "note.md").write_text("x", encoding="utf-8")
    dialog = NewNoteDialog(first, LIGHT)
    try:
        dialog._name_input.setText("note")
        assert dialog._create_btn.isEnabled() is False
        # Picking another folder (as the 瀏覽… button does) clears the clash.
        dialog.set_folder(second)
        assert dialog.folder() == second
        assert str(second) in dialog._folder_label.text()
        assert dialog._create_btn.isEnabled() is True
        dialog._attempt_create()
        assert dialog.created_path() == second / "note.md"
        assert (second / "note.md").read_bytes() == b""
        assert (first / "note.md").read_text(encoding="utf-8") == "x"
    finally:
        dialog.close()


def test_dialog_with_no_folder_shows_placeholder_and_disables_create(qapp, tmp_path):
    dialog = NewNoteDialog(None, LIGHT)
    try:
        assert dialog.folder() is None
        assert "請選擇資料夾" in dialog._folder_label.text()
        assert dialog._create_btn.isEnabled() is False
        assert dialog.target_path() is None
        dialog._name_input.setText("我的筆記")
        # Typing a name alone must not enable creation without a folder.
        assert dialog._create_btn.isEnabled() is False
        dialog._attempt_create()
        assert dialog.created_path() is None
        assert dialog.result() != dialog.DialogCode.Accepted

        # Browsing (simulated: set_folder is what _browse_folder calls) picks
        # a real location in the same window and unlocks creation.
        dialog.set_folder(tmp_path)
        assert dialog.folder() == tmp_path
        assert dialog._create_btn.isEnabled() is True
        dialog._attempt_create()
        assert dialog.created_path() == tmp_path / "我的筆記.md"
    finally:
        dialog.close()


def test_dialog_browse_with_no_folder_starts_from_empty_directory(
    qapp, tmp_path, monkeypatch
):
    dialog = NewNoteDialog(None, LIGHT)
    seen_start_dir = []
    monkeypatch.setattr(
        new_note_dialog_mod.QFileDialog,
        "getExistingDirectory",
        staticmethod(
            lambda *args, **kwargs: (seen_start_dir.append(args[2]), str(tmp_path))[1]
        ),
    )
    try:
        dialog._browse_folder()
        # Never seeds the picker with the program's working directory.
        assert seen_start_dir == [""]
        assert dialog.folder() == tmp_path
    finally:
        dialog.close()


def test_dialog_cancel_creates_nothing(qapp, tmp_path):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        dialog._name_input.setText("ghost")
        dialog.reject()
        assert dialog.created_path() is None
        assert list(tmp_path.iterdir()) == []
    finally:
        dialog.close()


def test_dialog_write_failure_leaves_no_empty_file_and_keeps_input(
    qapp, tmp_path, monkeypatch
):
    # Stand-in for a read-only folder / permission error: the real write
    # raises OSError. The dialog must stay open with the user's name intact
    # and must not leave a partial/empty file behind.
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        dialog._name_input.setText("locked")

        def _boom(*_a, **_k):
            raise OSError("拒絕存取。")

        monkeypatch.setattr(new_note_dialog_mod.file_ops, "create_document", _boom)

        dialog._attempt_create()

        assert dialog.created_path() is None
        assert dialog.result() != dialog.DialogCode.Accepted
        assert dialog._name_input.text() == "locked"
        assert "拒絕存取" in dialog._error_label.text()
        assert list(tmp_path.iterdir()) == []
    finally:
        dialog.close()


# ---------------- keyboard, tab order, and sizing ----------------
def _show_and_activate(dialog, qapp):
    """Make offscreen focus/hasFocus() reliable regardless of test order.

    ``show()`` alone does not always hand a dialog OS-level activation on the
    offscreen platform once another top-level window already exists (as one
    typically does by the time this test runs alongside the rest of the
    suite), and unfocused windows report ``hasFocus() is False`` even for a
    widget that already called ``setFocus()`` until the activation and
    focus-in events are actually delivered.
    """
    dialog.show()
    dialog.activateWindow()
    dialog.raise_()
    qapp.processEvents()
    dialog._name_input.setFocus()
    qapp.processEvents()


def test_dialog_defaults_focus_to_the_name_field(qapp, tmp_path):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        _show_and_activate(dialog, qapp)
        assert dialog._name_input.hasFocus() is True
    finally:
        dialog.close()


def test_dialog_tab_order_is_name_then_folder_then_type_then_backend_then_buttons(
    qapp, tmp_path
):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        _show_and_activate(dialog, qapp)
        # A disabled 建立 button (empty name) is skipped by Tab, so type a
        # valid name first to exercise the full chain including it.
        dialog._name_input.setText("valid-name")
        assert dialog._create_btn.isEnabled() is True

        widgets_by_id = {
            id(dialog._name_input): "name",
            id(dialog._browse_btn): "browse",
            id(dialog._type_buttons[0][0]): "type_md",
            id(dialog._type_buttons[1][0]): "type_txt",
            id(dialog._editor_backend_combo): "backend",
            id(dialog._cancel_btn): "cancel",
            id(dialog._create_btn): "create",
        }
        dialog._name_input.setFocus()
        qapp.processEvents()
        assert qapp.focusWidget() is dialog._name_input
        order = ["name"]
        for _ in range(6):
            QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
            order.append(widgets_by_id.get(id(qapp.focusWidget()), "other"))

        # Radio buttons in the same exclusive group are one Tab stop (arrow
        # keys move between them; that's standard, expected Qt behavior), so
        # only the currently-checked "md" radio appears here.
        assert order == [
            "name",
            "browse",
            "type_md",
            "backend",
            "cancel",
            "create",
            "name",  # wraps back around
        ]
    finally:
        dialog.close()


def test_dialog_enter_creates_and_escape_cancels_without_leaving_a_file(
    qapp, tmp_path
):
    accept_dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        accept_dialog.show()
        accept_dialog._name_input.setText("via-enter")
        QTest.keyClick(accept_dialog._name_input, Qt.Key.Key_Return)
        assert accept_dialog.result() == accept_dialog.DialogCode.Accepted
        assert accept_dialog.created_path() == tmp_path / "via-enter.md"
        assert (tmp_path / "via-enter.md").exists()
    finally:
        accept_dialog.close()

    cancel_dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        cancel_dialog.show()
        cancel_dialog._name_input.setText("via-escape")
        QTest.keyClick(cancel_dialog, Qt.Key.Key_Escape)
        assert cancel_dialog.result() == cancel_dialog.DialogCode.Rejected
        assert cancel_dialog.created_path() is None
        assert not (tmp_path / "via-escape.md").exists()
    finally:
        cancel_dialog.close()


def test_dialog_at_540px_height_keeps_every_control_visible(qapp, tmp_path):
    dialog = NewNoteDialog(tmp_path, LIGHT)
    try:
        dialog.resize(max(dialog.width(), dialog.minimumWidth()), 540)
        dialog.show()
        for widget in (
            dialog._name_input,
            dialog._folder_label,
            dialog._browse_btn,
            dialog._type_buttons[0][0],
            dialog._type_buttons[1][0],
            dialog._editor_backend_combo,
            dialog._error_label,
            dialog._cancel_btn,
            dialog._create_btn,
        ):
            assert widget.isVisible() is True
            # Every control's geometry (in dialog coordinates) must be
            # within the dialog's own client rect -- nothing clipped or
            # pushed outside a 540px-tall window.
            top_left = widget.mapTo(dialog, widget.rect().topLeft())
            bottom_right = widget.mapTo(dialog, widget.rect().bottomRight())
            assert dialog.rect().contains(top_left)
            assert dialog.rect().contains(bottom_right)
    finally:
        dialog.close()
