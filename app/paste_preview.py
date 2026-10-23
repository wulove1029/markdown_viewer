"""Review clipboard tables and local attachments before changing a document."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
)
from shiboken6 import isValid

_PREVIEW_ROWS = 50
_PREVIEW_COLUMNS = 20
_PREVIEW_CELL_CHARS = 300
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+.!|~-])")


@dataclass(frozen=True)
class ClipboardTable:
    source: str
    rows: tuple[tuple[str, ...], ...]

    @property
    def columns(self):
        return len(self.rows[0])

    def markdown(self, *, preview=False):
        rows = self.rows[:_PREVIEW_ROWS] if preview else self.rows
        columns = min(self.columns, _PREVIEW_COLUMNS) if preview else self.columns

        def cell(value):
            if preview and len(value) > _PREVIEW_CELL_CHARS:
                value = value[:_PREVIEW_CELL_CHARS] + "…"
            value = value.replace("\r\n", "\n").replace("\r", "\n")
            value = value.replace("&", "&amp;")
            value = _MARKDOWN_SPECIAL.sub(r"\\\1", value)
            return value.replace("\n", "<br>").replace("\t", "&#9;")

        lines = ["| " + " | ".join(cell(value) for value in row[:columns]) + " |"
                 for row in rows]
        lines.insert(1, "| " + " | ".join("---" for _ in range(columns)) + " |")
        return "\n".join(lines)


def parse_clipboard_table(text: str) -> ClipboardTable | None:
    """Recognize rectangular TSV, including spreadsheet quoted multiline cells."""
    if "\t" not in text or not any(char in text for char in "\r\n"):
        return None
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter="\t", strict=True))
    except csv.Error:
        return None
    while rows and not rows[-1]:
        rows.pop()
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    columns = len(rows[0])
    if any(row and len(row) != columns for row in rows):
        return None
    return ClipboardTable(text, tuple(tuple(row or [""] * columns) for row in rows))


@dataclass(frozen=True)
class PasteChoice:
    mode: str
    text: str = ""


class TablePastePreviewDialog(QDialog):
    def __init__(self, table: ClipboardTable, parent=None):
        super().__init__(parent)
        self.setWindowTitle("貼上表格預覽")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(720, 430)
        self._table = table
        self._choice = PasteChoice("cancel")
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{len(table.rows):,} 列 × {table.columns:,} 欄；第一列作為欄名。"
            "\n預覽最多顯示 50 列、20 欄，每格 300 字；插入時保留全部內容。",
            self,
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        tabs = QTabWidget(self)
        self.preview = QTableWidget(
            min(len(table.rows), _PREVIEW_ROWS), min(table.columns, _PREVIEW_COLUMNS), self,
        )
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row_index, row in enumerate(table.rows[:_PREVIEW_ROWS]):
            for column, value in enumerate(row[:_PREVIEW_COLUMNS]):
                display = value[:_PREVIEW_CELL_CHARS]
                if len(value) > _PREVIEW_CELL_CHARS:
                    display += "…"
                self.preview.setItem(row_index, column, QTableWidgetItem(display))
        self.preview.horizontalHeader().setDefaultSectionSize(150)
        tabs.addTab(self.preview, "表格")
        markdown = QPlainTextEdit(self)
        markdown.setReadOnly(True)
        markdown.setPlainText(table.markdown(preview=True))
        tabs.addTab(markdown, "Markdown")
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(self)
        self.insert_button = QPushButton("插入表格", self)
        self.insert_button.setDefault(True)
        plain = QPushButton("純文字貼上", self)
        buttons.addButton(self.insert_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(plain, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        self.insert_button.clicked.connect(self._insert_table)
        plain.clicked.connect(self._insert_plain)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _insert_table(self):
        self._choice = PasteChoice("table", self._table.markdown())
        self.accept()

    def _insert_plain(self):
        self._choice = PasteChoice("plain", self._table.source)
        self.accept()

    def choice(self):
        if self.result() == QDialog.DialogCode.Accepted:
            return self._choice
        return PasteChoice("cancel")


def choose_table_paste(text: str, parent=None) -> PasteChoice | None:
    """Return None for ordinary text; cancellation is an explicit choice."""
    table = parse_clipboard_table(text)
    if table is None:
        return None
    dialog = TablePastePreviewDialog(table, parent)
    dialog.exec()
    if not isValid(dialog):
        return PasteChoice("cancel")
    choice = dialog.choice()
    dialog.deleteLater()
    return choice


class ResourcePastePreviewDialog(QDialog):
    def __init__(self, paths, document_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("貼上附件預覽")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(700, 350)
        layout = QVBoxLayout(self)
        summary = QLabel(
            f"將 {len(paths):,} 個檔案加入「{Path(document_path).name}」。"
            "\n外部檔案會複製到文件旁的資源資料夾；原檔案保留。",
            self,
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)
        table = QTableWidget(min(len(paths), 100), 3, self)
        table.setHorizontalHeaderLabels(["檔案", "大小", "來源資料夾"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, raw_path in enumerate(paths[:100]):
            path = Path(raw_path)
            try:
                size = f"{path.stat().st_size:,} 位元組"
            except OSError:
                size = "無法讀取"
            for column, value in enumerate((path.name, size, str(path.parent))):
                table.setItem(row, column, QTableWidgetItem(value))
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 200)
        layout.addWidget(table)
        if len(paths) > 100:
            layout.addWidget(QLabel("預覽列出前 100 個檔案；確認後加入全部檔案。", self))
        buttons = QDialogButtonBox(self)
        buttons.addButton("加入附件", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def confirm_resource_paste(paths, document_path, parent=None) -> bool:
    dialog = ResourcePastePreviewDialog(paths, document_path, parent)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    if isValid(dialog):
        dialog.deleteLater()
    return accepted


def resource_clipboard_snapshot(mime, names):
    """Match browser filenames to native local URLs and capture file identities."""
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError("剪貼簿附件資訊無效，請重新複製檔案。")
    urls = mime.urls() if mime is not None and mime.hasUrls() else []
    if len(urls) != len(names) or not all(url.isLocalFile() for url in urls):
        raise ValueError("剪貼簿來源已變更，請重新複製檔案。")
    snapshot = []
    for url, expected_name in zip(urls, names, strict=True):
        path = Path(url.toLocalFile()).resolve(strict=True)
        if not path.is_file() or path.name != expected_name:
            raise ValueError("剪貼簿來源已變更，請重新複製檔案。")
        stat = path.stat()
        snapshot.append((path, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))
    return tuple(snapshot)
