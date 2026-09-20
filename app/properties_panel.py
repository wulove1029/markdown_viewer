"""Conservative, explicit-save front matter property editor."""

from pathlib import Path

import yaml
from PySide6.QtCore import Qt
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

from .frontmatter_properties import PropertiesDocument


class PropertiesPanel(QWidget):
    def __init__(self, on_save, parent=None):
        super().__init__(parent)
        self._on_save = on_save
        self._path = None
        self._document = None
        self._original_values = {}
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
        self._path, self._document = path, None
        self._table.setRowCount(0)
        self._original_values = {}
        if path is None or path.suffix.lower() not in {".md", ".markdown"}:
            self._message.setText("開啟 Markdown 文件後可編輯屬性。")
        else:
            try:
                self._document = PropertiesDocument.parse(path.read_bytes())
                for key, value in self._document.values.items():
                    rendered = yaml.safe_dump(value, allow_unicode=True, default_flow_style=True)
                    rendered = rendered.removesuffix("...\n").strip()
                    self._original_values[key] = rendered
                    self._add_row(key, rendered)
                self._message.setText("修改後按「儲存屬性」；本文及未修改的屬性保持原樣。")
            except (OSError, UnicodeError, ValueError) as exc:
                self._message.setText(str(exc))
        enabled = self._document is not None
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
