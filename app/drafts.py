"""Recoverable unnamed documents and their first exclusive save."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from PySide6.QtWidgets import QFileDialog, QMessageBox


def draft_directory(window) -> Path:
    return window._recovery_store.directory / "untitled"


def is_draft(window, path) -> bool:
    if not path:
        return False
    candidate = Path(path)
    return candidate.parent == draft_directory(window) and candidate.suffix == ".md"


def create_draft(window, text: str = "") -> None:
    from . import edit_backend

    directory = draft_directory(window)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid4().hex}.md"
        with path.open("xb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        QMessageBox.warning(window, "無法建立草稿", str(exc))
        return
    window._on_browser_note_created(str(path), backend=edit_backend.SPLIT_BACKEND)
    window._editor.document().setModified(True)
    window._stash_active_editor_state()
    window._save_active_recovery_snapshot()
    window._update_dirty_ui()


def save_draft(window, key: str, state, document) -> bool:
    suggestion = window._last_new_note_folder()
    if suggestion is None:
        roots = window._panel.file_browser.library_roots() or []
        suggestion = Path(roots[0]) if roots else Path.home() / "Documents"
    filename, _ = QFileDialog.getSaveFileName(
        window, "儲存未命名草稿", str(suggestion / "未命名筆記.md"),
        "Markdown (*.md *.markdown)",
    )
    if not filename:
        return False
    path = Path(filename)
    if path.suffix.casefold() not in {".md", ".markdown"}:
        path = path.with_suffix(".md")
    if is_draft(window, path):
        QMessageBox.warning(window, "請選擇正式文件位置", "草稿復原目錄不能作為正式文件位置。")
        return False
    text = document.toPlainText()
    try:
        # A changed destination must never be overwritten by a first save.
        with path.open("xb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        QMessageBox.warning(window, "草稿尚未儲存", f"草稿已保留。請選擇未使用的檔名。\n{exc}")
        return False
    window._on_browser_paths_migrated({key: str(path)}, _snapshot_ready=True)
    state["source_signature"] = window._file_signature(path)
    state["editing_encoding"] = "utf-8"
    state["editing_newline"] = "\n"
    document.setModified(False)
    window._recovery_store.clear_after_save(str(path))
    window._remember_new_note_folder(path.parent)
    window._update_dirty_ui()
    window._schedule_session_save()
    # The uniquely created internal draft is retained as a recovery copy.
    return True
