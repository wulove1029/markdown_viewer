"""A non-modal inbox for recovery drafts, including orphaned session tabs."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from .atomic_io import atomic_write_bytes
from .document_relocation import rebase_markdown_links
from .file_types import document_kind, is_markdown
from .md_converter import read_text_detailed
from .recovery import RecoverySnapshot


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve(strict=False)))


def pending_recovery_snapshots(window) -> list[RecoverySnapshot]:
    """List distinct, loadable drafts without changing snapshots or sources.

    A live tab buffer is newer than its persisted snapshot. Identical disk
    content needs no recovery, but an absent/unreadable source remains eligible
    even when the saved draft is empty.
    """
    store = getattr(window, "_recovery_store", None)
    if store is None:
        return []
    live_paths = set()
    for path, state in getattr(window, "_tab_state", {}).items():
        if isinstance(state.get("editor_document"), QTextDocument):
            try:
                live_paths.add(_path_key(path))
            except (OSError, ValueError):
                continue
    try:
        snapshots = store.list()
    except OSError:
        return []
    pending = []
    seen = set()
    for candidate in snapshots:
        try:
            path = Path(candidate.source_path)
            key = _path_key(path)
        except (OSError, ValueError):
            continue
        if key in seen or key in live_paths:
            continue
        seen.add(key)
        if document_kind(path) not in {"markdown", "text"}:
            continue
        # The listed JSON must also be reachable by the normal recovery load
        # path; reject misdirected/duplicate/corrupt entries.
        try:
            snapshot = store.load(path)
        except (OSError, ValueError, UnicodeError):
            continue
        if snapshot is None:
            continue
        try:
            disk = read_text_detailed(path)
        except (OSError, ValueError, UnicodeError):
            disk = None
        if disk is not None and snapshot.draft == disk[0]:
            continue
        pending.append(snapshot)
    return pending


def save_recovery_copy(snapshot: RecoverySnapshot, destination: str | Path) -> Path:
    """Save a user-chosen copy while refusing aliases of the source document."""
    target = Path(destination).expanduser().resolve(strict=False)
    source = Path(snapshot.source_path)
    # atomic_write_bytes also writes deterministic .tmp/.bak siblings. Those
    # files can already be links to the source even when target itself is not.
    for writable in (target, target.with_name(target.name + ".tmp"), target.with_name(target.name + ".bak")):
        same_source = _path_key(writable) == _path_key(source)
        try:
            same_source = same_source or writable.samefile(source)
        except (OSError, ValueError):
            pass
        if same_source:
            raise ValueError("請選擇不同於原始檔的位置或檔名；另存副本及其暫存／備份不可指向原始檔。")
    text = snapshot.draft
    if is_markdown(source):
        text = rebase_markdown_links(text, source, target)
    if snapshot.newline != "\n":
        text = text.replace("\n", snapshot.newline)
    try:
        data = text.encode(snapshot.encoding)
    except (LookupError, UnicodeEncodeError):
        data = text.encode("utf-8")
    atomic_write_bytes(target, data)
    return target


def _display_time(timestamp: str) -> str:
    try:
        stamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return stamp.astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return timestamp


class RecoveryBrowser(QDialog):
    """Review one draft at a time; closing the inbox keeps every snapshot."""

    def __init__(self, window, snapshots=None):
        super().__init__(window)
        self._window = window
        self.setWindowTitle("待復原草稿")
        self.setModal(False)
        self.resize(820, 580)
        layout = QVBoxLayout(self)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._list = QListWidget()
        self._list.setAccessibleName("待復原草稿清單")
        self._list.setMinimumHeight(90)
        splitter.addWidget(self._list)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setAccessibleName("選取草稿的內容預覽")
        splitter.addWidget(self._preview)
        splitter.setSizes([210, 260])
        layout.addWidget(splitter, 1)
        self._message = QLabel()
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        buttons = QHBoxLayout()
        self._later = QPushButton("稍後")
        self._copy = QPushButton("另存副本…")
        self._continue = QPushButton("繼續編輯…")
        # QListWidget emits itemActivated on Enter. A dialog default button
        # would receive the same key and open the recovery comparison twice.
        for button in (self._later, self._copy, self._continue):
            button.setAutoDefault(False)
        buttons.addWidget(self._later)
        buttons.addStretch(1)
        buttons.addWidget(self._copy)
        buttons.addWidget(self._continue)
        layout.addLayout(buttons)
        self._later.clicked.connect(self.close)
        self._copy.clicked.connect(self._save_copy)
        self._continue.clicked.connect(self._review_selected)
        self._list.itemActivated.connect(self._review_selected)
        self._list.currentItemChanged.connect(self._selection_changed)
        self.refresh(snapshots)

    def refresh(self, snapshots=None) -> int:
        selected = self.selected_snapshot()
        selected_path = selected.source_path if selected else None
        self._list.clear()
        if snapshots is None:
            snapshots = pending_recovery_snapshots(self._window)
        selected_row = 0
        for index, snapshot in enumerate(snapshots):
            path = Path(snapshot.source_path)
            missing = "（原檔不存在）" if not path.exists() else ""
            item = QListWidgetItem(
                f"{path.name}{missing}　{_display_time(snapshot.updated_at)}\n{path.parent}"
            )
            item.setData(Qt.ItemDataRole.UserRole, snapshot)
            item.setToolTip(snapshot.source_path)
            self._list.addItem(item)
            if snapshot.source_path == selected_path:
                selected_row = index
        self._summary.setText(
            f"找到 {len(snapshots)} 份待復原草稿。選取文件可先預覽，再比較原檔與草稿。\n"
            "稍後會保留草稿，可從「檔案 → 待復原草稿」再次開啟；另存副本不會改動原檔。"
        )
        if snapshots:
            self._list.setCurrentRow(selected_row)
        else:
            self._selection_changed()
        return len(snapshots)

    def selected_snapshot(self) -> RecoverySnapshot | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection_changed(self, *_args):
        snapshot = self.selected_snapshot()
        self._preview.setPlainText(snapshot.draft if snapshot else "")
        self._continue.setEnabled(snapshot is not None)
        self._copy.setEnabled(snapshot is not None)

    def _review_selected(self, *_args):
        snapshot = self.selected_snapshot()
        if snapshot is None:
            return
        self.hide()
        self._window._review_recovery_path(snapshot.source_path)
        self.refresh()
        # Continue editing should reveal the editor. If the comparison chose
        # Later, keep the inbox available instead of losing the selected draft.
        pending = [
            self._list.item(index).data(Qt.ItemDataRole.UserRole).source_path
            for index in range(self._list.count())
        ]
        if snapshot.source_path in pending:
            self.show()
            self.raise_()

    def _save_copy(self):
        selected = self.selected_snapshot()
        if selected is None:
            return
        snapshot = self._window._recovery_store.load(selected.source_path)
        if snapshot is None:
            self.refresh()
            return
        source = Path(snapshot.source_path)
        default = source.with_name(f"{source.stem}-recovered{source.suffix}")
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "另存復原草稿副本",
            str(default),
            "文字文件 (*.md *.markdown *.txt);;所有檔案 (*)",
        )
        if not destination:
            return
        try:
            target = save_recovery_copy(snapshot, destination)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "無法另存副本", str(exc))
            return
        self._message.setText(f"已另存副本：{target}\n原草稿仍保留，可稍後繼續處理。")
