"""Shortcut registry and reference-dialog tests."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame, QPushButton, QScrollArea, QToolButton

from app.shortcuts import (
    ALL_SHORTCUTS,
    WINDOW_SHORTCUTS,
    grouped_shortcuts,
    shortcut_by_id,
)
from app.shortcuts_dialog import ShortcutDialog
from app.theme import DARK, LIGHT
from app.window import MainWindow


def _portable(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText)


def test_shortcut_registry_ids_handlers_and_window_sequences_are_unique():
    command_ids = [spec.command_id for spec in ALL_SHORTCUTS]
    assert len(command_ids) == len(set(command_ids))

    registered = []
    for spec in WINDOW_SHORTCUTS:
        assert spec.handler
        assert callable(getattr(MainWindow, spec.handler))
        registered.extend(_portable(sequence) for sequence in spec.sequences)
        assert shortcut_by_id(spec.command_id) is spec

    assert len(registered) == len(set(registered))
    assert "Ctrl+Shift+M" in registered
    assert "Ctrl++" in registered
    assert "Ctrl+=" in registered
    assert "Esc" not in registered

    assert shortcut_by_id("wikilink.accept").sequences == (
        "Enter",
        "Tab",
        "Shift+Tab",
    )


def test_help_groups_contain_every_registry_entry_once():
    grouped = [spec for _group, specs in grouped_shortcuts() for spec in specs]
    assert len(grouped) == len(ALL_SHORTCUTS)
    assert {spec.command_id for spec in grouped} == {
        spec.command_id for spec in ALL_SHORTCUTS
    }
    assert all(specs for _group, specs in grouped_shortcuts())


def test_shortcut_dialog_is_scrollable_searchable_and_complete(qapp):
    dialog = ShortcutDialog(LIGHT)
    try:
        dialog.show()
        qapp.processEvents()

        scroll = dialog.findChild(QScrollArea, "shortcutScrollArea")
        clear_button = dialog.findChild(
            QToolButton, "shortcutSearchClearButton"
        )
        rows = dialog.findChildren(QFrame, "shortcutRow")
        assert scroll is not None
        assert clear_button is not None
        assert clear_button.icon().isNull() is False
        assert clear_button.toolTip() == "清除搜尋"
        idle_icon = clear_button.icon().cacheKey()
        qapp.sendEvent(clear_button, QEvent(QEvent.Type.Enter))
        hover_icon = clear_button.icon().cacheKey()
        assert hover_icon != idle_icon
        qapp.sendEvent(clear_button, QEvent(QEvent.Type.Leave))
        assert clear_button.icon().cacheKey() != hover_icon
        assert scroll.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert len(rows) == len(ALL_SHORTCUTS)
        assert dialog._count_badge.text() == f"{len(ALL_SHORTCUTS)} 個操作"
        assert dialog.minimumWidth() <= dialog.width()
        assert dialog.minimumHeight() <= dialog.height()
        assert dialog.grab().isNull() is False

        dialog._search.setText("Ctrl+Shift+M")
        qapp.processEvents()
        visible_ids = {
            row.property("commandId") for row in rows if row.isVisible()
        }
        assert visible_ids == {"tools.mermaid_workspace"}
        assert dialog._count_badge.text() == "1 個操作"

        QTest.keyClick(dialog._search, Qt.Key.Key_Return)
        assert dialog.isVisible()

        QTest.mouseClick(clear_button, Qt.MouseButton.LeftButton)
        assert dialog._search.text() == ""

        qapp.processEvents()
        assert all(row.isVisible() for row in rows)

        dialog._search.setText("definitely-no-such-shortcut")
        qapp.processEvents()
        assert not any(row.isVisible() for row in rows)
        assert dialog._empty_state.isVisible()
        assert dialog._count_badge.text() == "0 個操作"

        close_button = dialog.findChild(QPushButton, "shortcutCloseButton")
        assert close_button is not None
        close_button.setFocus()
        QTest.keyClick(close_button, Qt.Key.Key_Return)
        assert dialog.isVisible() is False
    finally:
        dialog.close()


def test_shortcut_dialog_renders_in_dark_theme(qapp):
    dialog = ShortcutDialog(DARK)
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.styleSheet()
        assert DARK.window in dialog.styleSheet()
        assert dialog.grab().isNull() is False
    finally:
        dialog.close()
