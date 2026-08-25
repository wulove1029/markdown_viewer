from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from app.recovery_dialog import RecoveryDialog


def test_recovery_dialog_compares_versions_and_restores(qapp, tmp_path):
    dialog = RecoveryDialog(
        tmp_path / "note.md", "disk", "draft", "2026-08-25T10:00:00Z"
    )
    assert dialog.disk_editor.toPlainText() == "disk"
    assert dialog.draft_editor.toPlainText() == "draft"
    button = next(
        item
        for item in dialog.findChildren(QPushButton)
        if item.text() == "復原草稿"
    )
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert dialog.choice == RecoveryDialog.RESTORE


def test_recovery_dialog_can_discard_snapshot(qapp, tmp_path):
    dialog = RecoveryDialog(tmp_path / "note.md", "disk", "draft", "now")
    button = next(
        item for item in dialog.findChildren(QPushButton)
        if item.text() == "使用磁碟版本"
    )
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert dialog.choice == RecoveryDialog.DISCARD
