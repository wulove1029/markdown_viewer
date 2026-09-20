"""Confirmation and clean-buffer synchronization for wiki backlink renames."""

from pathlib import Path

from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QMessageBox

from .backlink_rename import prepare_backlink_updates
from .md_converter import _decode_bytes


def _check_buffers(window, updates):
    for path in updates:
        state = window._tab_state.get(str(path), {})
        document = state.get("editor_document")
        if isinstance(document, QTextDocument) and document.isModified():
            raise OSError(f"引用文件有未儲存的編輯，請先儲存再改名：{path}")
        if str(path) == window._active_path and (window._edit_mode or window._preview_editing):
            raise OSError(f"引用文件正在編輯，請先儲存並離開編輯模式：{path}")
        if state.get("pending_recovery") or window._recovery_store.load(path) is not None:
            raise OSError(f"引用文件有待復原草稿，請先處理草稿：{path}")


def prepare_for_window(window, old, new):
    updates = prepare_backlink_updates(old, new, window._link_roots())
    if not updates:
        return updates
    _check_buffers(window, updates)
    box = QMessageBox(window)
    box.setWindowTitle("重新命名並更新連結")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"將「{old.name}」改為「{new.name}」，並更新 {len(updates)} 份文件的 wikilink。")
    box.setInformativeText("範圍為目前文件庫及來源資料夾（沿用排除設定）。展開詳細資料可查看完整檔案清單。")
    box.setDetailedText("\n".join(str(path) for path in sorted(updates)))
    confirm = box.addButton("重新命名並更新", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    if box.clickedButton() is not confirm:
        return None
    _check_buffers(window, updates)
    # The ordinary relocation flow prepared the clean source buffer before
    # this confirmation. Keep its self-links consistent with the disk plan.
    for path, (_before, after) in updates.items():
        prepared = getattr(window, "_prepared_relocation", {}).get(str(path))
        if prepared is not None and "text" in prepared:
            prepared["text"] = _decode_bytes(after)[0].replace("\r\n", "\n").replace("\r", "\n")
    return updates


def apply_to_window(window, updates, old, new):
    for path, (_before, after) in updates.items():
        destination = new if path == Path(old).resolve() else path
        state = window._tab_state.get(str(destination), {})
        document = state.get("editor_document")
        if isinstance(document, QTextDocument):
            text, encoding = _decode_bytes(after)
            document.setPlainText(text.replace("\r\n", "\n").replace("\r", "\n"))
            document.setModified(False)
            state["editing_encoding"] = encoding
            state["editing_newline"] = "\r\n" if "\r\n" in text else "\n"
            state["source_signature"] = window._file_signature(destination)
        if window._current_file == destination and not window._edit_mode:
            window._reload_preview()

