"""Conservative, explicit-save front matter property editor."""

import threading
import time
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, QRunnable, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import background_jobs
from .frontmatter_properties import PropertiesDocument


class _PropertiesSignals(QObject):
    done = Signal(int, object, object, str)


class _ReadProperties(QRunnable):
    def __init__(self, generation, path, cancel):
        super().__init__()
        self.generation, self.path, self.cancel = generation, path, cancel
        self.signals = _PropertiesSignals()

    def run(self):
        payload, error = None, ""
        try:
            if self.cancel.is_set():
                return
            before = self.path.stat()
            chunks = []
            with self.path.open("rb") as source:
                while not self.cancel.is_set():
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
            if self.cancel.is_set():
                return
            document = PropertiesDocument.parse(b"".join(chunks))
            rows = []
            for key, value in document.values.items():
                if self.cancel.is_set():
                    return
                rendered = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True)
                rows.append((key, rendered.removesuffix("...\n").strip()))
            after = self.path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise OSError("文件在讀取時已變更，請重新載入屬性。")
            payload = document, rows
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            error = f"無法載入屬性：{exc}"
        if not self.cancel.is_set():
            self.signals.done.emit(self.generation, self.path, payload, error)


class PropertiesPanel(QWidget):
    def __init__(self, on_save, parent=None):
        super().__init__(parent)
        self._on_save = on_save
        self._path = None
        self._document = None
        self._original_values = {}
        self._generation = 0
        self._cancel = threading.Event()
        self._pending_document = None
        self._pending_rows = []
        self._row_offset = 0
        self._row_timer = QTimer(self)
        self._row_timer.setSingleShot(True)
        self._row_timer.timeout.connect(self._append_rows)
        layout = QVBoxLayout(self)
        self._message = QLabel("開啟 Markdown 文件後可編輯屬性。")
        self._message.setWordWrap(True)
        layout.addWidget(self._message)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["屬性", "值（YAML）"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)
        row = QHBoxLayout()
        self._add = QPushButton("新增屬性")
        self._add.clicked.connect(self._add_property)
        self._save = QPushButton("儲存屬性")
        self._save.clicked.connect(self._save_properties)
        row.addWidget(self._add)
        row.addWidget(self._save)
        layout.addLayout(row)
        self.set_document(None)

    def set_document(self, path: Path | None):
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self._row_timer.stop()
        self._pending_document, self._pending_rows = None, []
        self._path, self._document = path, None
        self._table.setRowCount(0)
        self._original_values = {}
        self._set_enabled(False)
        if path is None or path.suffix.lower() not in {".md", ".markdown"}:
            self._message.setText("開啟 Markdown 文件後可編輯屬性。")
        else:
            self._message.setText(f"正在載入 {path.name} 的屬性…")
            worker = _ReadProperties(self._generation, path, self._cancel)
            worker.signals.done.connect(self._read_done)
            background_jobs.submit(
                worker, key=(id(self), "properties"), should_cancel=worker.cancel.is_set,
            )

    def _read_done(self, generation, path, payload, error):
        if generation != self._generation or path != self._path or self._cancel.is_set():
            return
        if error:
            self._message.setText(error)
            return
        self._pending_document, self._pending_rows = payload
        self._row_offset = 0
        self._row_timer.start(0)

    def _append_rows(self):
        if self._pending_document is None or self._cancel.is_set():
            return
        deadline = time.perf_counter() + 0.008
        while self._row_offset < len(self._pending_rows):
            key, rendered = self._pending_rows[self._row_offset]
            self._original_values[key] = rendered
            self._add_row(key, rendered)
            self._row_offset += 1
            if time.perf_counter() >= deadline:
                self._row_timer.start(0)
                return
        self._document, self._pending_document = self._pending_document, None
        self._pending_rows = []
        self._message.setText("修改後按「儲存屬性」；本文及未修改的屬性保持原樣。")
        self._set_enabled(True)

    def _set_enabled(self, enabled):
        self._add.setEnabled(enabled)
        self._save.setEnabled(enabled)
        self._table.setEnabled(enabled)

    def _add_row(self, key, value):
        row = self._table.rowCount()
        self._table.insertRow(row)
        name = QTableWidgetItem(key)
        name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 0, name)
        self._table.setItem(row, 1, QTableWidgetItem(value))

    def _add_property(self):
        key, accepted = QInputDialog.getText(self, "新增屬性", "名稱：")
        key = key.strip()
        if accepted and key:
            if any(self._table.item(row, 0).text() == key for row in range(self._table.rowCount())):
                self._message.setText("屬性名稱已存在。")
                return
            self._add_row(key, '""')

    def _save_properties(self):
        if self._document is None:
            return
        updates = {}
        try:
            for row in range(self._table.rowCount()):
                key, value = (self._table.item(row, col).text() for col in range(2))
                if key not in self._original_values or value != self._original_values[key]:
                    updates[key] = yaml.safe_load(value)
            raw = self._document.update(updates)
            if self._on_save(self._path, self._document.raw, raw):
                self.set_document(self._path)
        except (OSError, ValueError, UnicodeError, yaml.YAMLError) as exc:
            self._message.setText(f"未儲存：{exc}")
