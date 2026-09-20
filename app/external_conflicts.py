"""Read-only comparison and exclusive local-copy preservation for conflicts."""

import difflib
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from .md_converter import read_text_detailed


def write_local_copy(path: Path, text: str, encoding="utf-8", newline="\n") -> Path:
    """Create and flush a unique copy without ever replacing an existing file."""
    if newline != "\n":
        text = text.replace("\n", newline)
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError:
        data = text.encode("utf-8")
    number = 1
    while True:
        suffix = "" if number == 1 else f" {number}"
        candidate = path.with_name(f"{path.stem} (本機副本){suffix}{path.suffix}")
        try:
            handle = candidate.open("xb")
        except FileExistsError:
            number += 1
            continue
        try:
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            # After close another process can replace this path. Retain it
            # rather than risk deleting externally replaced data.
            raise OSError(f"本機副本未確認寫入成功，請保留並檢查：{candidate}\n{exc}") from exc
        return candidate


def show_diff(window, path: Path, local: str) -> None:
    disk = read_text_detailed(path)
    if disk is None:
        raise OSError("無法辨識外部版本的編碼，未修改任何檔案。")
    diff = "".join(difflib.unified_diff(
        disk[0].splitlines(keepends=True), local.splitlines(keepends=True),
        fromfile="外部磁碟版本", tofile="本機未儲存版本",
    ))
    dialog = QDialog(window)
    dialog.setWindowTitle(f"查看差異：{path.name}")
    dialog.resize(850, 550)
    layout = QVBoxLayout(dialog)
    text = QPlainTextEdit(dialog)
    text.setReadOnly(True)
    text.setPlainText(diff or "兩個版本的文字內容相同。")
    layout.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()


def choose_action(window, path: Path) -> str:
    box = QMessageBox(window)
    box.setWindowTitle("檔案已在外部變更")
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(f"{path.name} 已被其他程式修改，本機也有未儲存的編輯。")
    actions = {}
    for key, label in (("diff", "查看差異"), ("both", "保留雙方"),
                       ("overwrite", "以本機覆寫"), ("reload", "捨棄本機並載入外部")):
        actions[key] = box.addButton(label, QMessageBox.ButtonRole.ActionRole)
    cancel = box.addButton("稍後處理", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return next((key for key, button in actions.items() if box.clickedButton() is button), "cancel")


def handle_dirty_conflict(window) -> None:
    path = window._current_file
    while True:
        choice = choose_action(window, path)
        try:
            if choice == "diff":
                show_diff(window, path, window._editor.toPlainText())
                continue
            if choice == "both":
                copy = write_local_copy(path, window._editor.toPlainText(),
                                        window._editing_encoding, window._editing_newline)
                window._reload_editor_from_disk()
                window.statusBar().showMessage(f"已保留本機副本：{copy}", 10000)
            elif choice == "reload":
                window._reload_editor_from_disk()
            elif choice == "overwrite":
                window._save_edits_after_snapshot()
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(window, "無法處理外部變更", str(exc))
        return
