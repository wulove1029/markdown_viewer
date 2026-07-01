"""Qt table editor for structured Mermaid diagram types."""

from __future__ import annotations

from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .structured_mermaid import StructuredDiagram, StructuredRow


_KIND_TITLES = {
    "sequence": "Sequence Diagram",
    "class": "Class Diagram",
    "state": "State Diagram",
    "er": "ER Diagram",
}

_KIND_COLUMNS = {
    "sequence": ["role", "name", "source", "arrow", "target", "text"],
    "class": ["role", "class", "member", "source", "left", "arrow", "right", "target"],
    "state": ["role", "source", "target", "label"],
    "er": [
        "role",
        "entity",
        "field_type",
        "field_name",
        "source",
        "connector",
        "target",
        "label",
    ],
}

_KIND_ROLES = {
    "sequence": ["participant", "message"],
    "class": ["class", "member", "relation"],
    "state": ["transition"],
    "er": ["entity", "field", "relation"],
}

_ROLE_FIELDS = {
    ("sequence", "participant"): ["name"],
    ("sequence", "message"): ["source", "arrow", "target", "text"],
    ("class", "class"): ["class"],
    ("class", "member"): ["class", "member"],
    ("class", "relation"): ["source", "left", "arrow", "right", "target"],
    ("state", "transition"): ["source", "target", "label"],
    ("er", "entity"): ["entity"],
    ("er", "field"): ["entity", "field_type", "field_name"],
    ("er", "relation"): ["source", "connector", "target", "label"],
}

_COLUMN_LABELS = {
    "role": "Type",
    "name": "Name",
    "source": "Source",
    "arrow": "Arrow",
    "target": "Target",
    "text": "Text",
    "class": "Class",
    "member": "Member",
    "left": "Left Label",
    "right": "Right Label",
    "label": "Label",
    "entity": "Entity",
    "field_type": "Field Type",
    "field_name": "Field Name",
    "connector": "Connector",
}


class StructuredMermaidEditor(QWidget):
    """Edit sequence, class, state, and ER diagrams as structured rows."""

    diagram_changed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._diagram = StructuredDiagram("sequence", "sequenceDiagram")
        self._updating = False
        self._property_edits: dict[str, QLineEdit] = {}

        self._title = QLabel()
        self._title.setObjectName("structuredDiagramTitle")

        self._role_combo = QComboBox()
        self._add_btn = QPushButton("Add")
        self._delete_btn = QPushButton("Delete")
        self._up_btn = QPushButton("Up")
        self._down_btn = QPushButton("Down")

        self._add_btn.clicked.connect(self._add_selected_role)
        self._delete_btn.clicked.connect(self.delete_current_row)
        self._up_btn.clicked.connect(lambda: self.move_current_row(-1))
        self._down_btn.clicked.connect(lambda: self.move_current_row(1))

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(self._title)
        toolbar.addStretch()
        toolbar.addWidget(QLabel("New row"))
        toolbar.addWidget(self._role_combo)
        toolbar.addWidget(self._add_btn)
        toolbar.addWidget(self._delete_btn)
        toolbar.addWidget(self._up_btn)
        toolbar.addWidget(self._down_btn)

        self._table = QTableWidget()
        self._table.setObjectName("structuredTable")
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.cellChanged.connect(self._on_cell_changed)
        self._table.currentCellChanged.connect(self._on_current_cell_changed)

        self._properties = self._build_properties()

        body = QSplitter()
        body.addWidget(self._table)
        body.addWidget(self._properties)
        body.setStretchFactor(0, 4)
        body.setStretchFactor(1, 2)
        body.setSizes([680, 260])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(body, stretch=1)

        self.set_diagram(self._diagram)

    def diagram(self) -> StructuredDiagram:
        return deepcopy(self._diagram)

    def set_diagram(self, diagram: StructuredDiagram):
        self._updating = True
        try:
            self._diagram = deepcopy(diagram)
            if self._diagram.kind not in _KIND_COLUMNS:
                self._diagram.kind = "sequence"
                self._diagram.header = "sequenceDiagram"
            self._refresh_role_combo()
            self._refresh_table(select_row=0 if self._diagram.rows else -1)
        finally:
            self._updating = False
        self._update_properties()

    def add_row(self, role: str | None = None) -> StructuredRow:
        actual_role = role or str(self._role_combo.currentData() or "")
        if actual_role not in _KIND_ROLES.get(self._diagram.kind, []):
            actual_role = _KIND_ROLES[self._diagram.kind][0]
        row = _default_row(self._diagram.kind, actual_role, self._diagram.rows)
        self._diagram.rows.append(row)
        self._refresh_table(select_row=len(self._diagram.rows) - 1)
        self._emit_changed()
        return deepcopy(row)

    def delete_current_row(self):
        row_index = self._current_row()
        if row_index < 0 or row_index >= len(self._diagram.rows):
            return
        del self._diagram.rows[row_index]
        next_row = min(row_index, len(self._diagram.rows) - 1)
        self._refresh_table(select_row=next_row)
        self._emit_changed()

    def move_current_row(self, offset: int):
        row_index = self._current_row()
        target = row_index + offset
        if (
            row_index < 0
            or row_index >= len(self._diagram.rows)
            or target < 0
            or target >= len(self._diagram.rows)
        ):
            return
        self._diagram.rows[row_index], self._diagram.rows[target] = (
            self._diagram.rows[target],
            self._diagram.rows[row_index],
        )
        self._refresh_table(select_row=target)
        self._emit_changed()

    def set_cell(self, row_index: int, key: str, value: str):
        if row_index < 0 or row_index >= len(self._diagram.rows):
            raise IndexError("Structured Mermaid row index out of range.")
        row = self._diagram.rows[row_index]
        if key == "role":
            if value not in _KIND_ROLES.get(self._diagram.kind, []):
                raise ValueError(f"Unsupported row role for {self._diagram.kind}: {value}")
            row.role = value
        else:
            row.cells[key] = value
        self._refresh_table(select_row=row_index)
        self._emit_changed()

    def _build_properties(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("structuredProperties")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self._properties_title = QLabel("Properties")
        self._properties_title.setObjectName("flowchartPropertiesTitle")
        layout.addWidget(self._properties_title)

        self._form_host = QWidget()
        self._form = QFormLayout(self._form_host)
        self._form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._form_host)

        self._empty = QLabel("Select a row to edit its fields.")
        self._empty.setObjectName("flowchartPropertiesEmpty")
        self._empty.setWordWrap(True)
        layout.addWidget(self._empty)
        layout.addStretch()
        return panel

    def _refresh_role_combo(self):
        self._role_combo.blockSignals(True)
        try:
            self._role_combo.clear()
            for role in _KIND_ROLES.get(self._diagram.kind, []):
                self._role_combo.addItem(role.title(), role)
        finally:
            self._role_combo.blockSignals(False)
        self._title.setText(_KIND_TITLES.get(self._diagram.kind, "Mermaid Diagram"))

    def _refresh_table(self, *, select_row: int = -1):
        columns = _KIND_COLUMNS[self._diagram.kind]
        was_updating = self._updating
        self._updating = True
        try:
            self._table.clear()
            self._table.setColumnCount(len(columns))
            self._table.setRowCount(len(self._diagram.rows))
            self._table.setHorizontalHeaderLabels(
                [_COLUMN_LABELS.get(column, column.title()) for column in columns]
            )
            for row_index, row in enumerate(self._diagram.rows):
                editable_fields = _editable_fields(self._diagram.kind, row.role)
                for column_index, key in enumerate(columns):
                    text = row.role if key == "role" else row.cells.get(key, "")
                    item = QTableWidgetItem(text)
                    if key == "role" or key not in editable_fields:
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(row_index, column_index, item)
            if select_row >= 0 and self._diagram.rows:
                safe_row = min(select_row, len(self._diagram.rows) - 1)
                self._table.setCurrentCell(safe_row, 0)
        finally:
            self._updating = was_updating
        self._update_properties()

    def _on_cell_changed(self, row_index: int, column_index: int):
        if self._updating:
            return
        columns = _KIND_COLUMNS[self._diagram.kind]
        if column_index < 0 or column_index >= len(columns):
            return
        key = columns[column_index]
        if key == "role" or row_index < 0 or row_index >= len(self._diagram.rows):
            return
        row = self._diagram.rows[row_index]
        if key not in _editable_fields(self._diagram.kind, row.role):
            return
        item = self._table.item(row_index, column_index)
        row.cells[key] = item.text() if item else ""
        self._update_properties()
        self._emit_changed()

    def _on_current_cell_changed(self, current_row, _current_col, _previous_row, _previous_col):
        if self._updating:
            return
        self._update_properties()

    def _update_properties(self):
        self._property_edits.clear()
        while self._form.rowCount():
            self._form.removeRow(0)

        row_index = self._current_row()
        has_row = 0 <= row_index < len(self._diagram.rows)
        self._form_host.setVisible(has_row)
        self._empty.setVisible(not has_row)
        if not has_row:
            self._properties_title.setText("Properties")
            return

        row = self._diagram.rows[row_index]
        self._properties_title.setText(f"{row.role.title()} Properties")
        for key in _editable_fields(self._diagram.kind, row.role):
            edit = QLineEdit(row.cells.get(key, ""))
            edit.editingFinished.connect(
                lambda key=key, edit=edit: self._on_property_changed(key, edit)
            )
            self._property_edits[key] = edit
            self._form.addRow(_COLUMN_LABELS.get(key, key.title()), edit)

    def _on_property_changed(self, key: str, edit: QLineEdit):
        if self._updating:
            return
        row_index = self._current_row()
        if row_index < 0 or row_index >= len(self._diagram.rows):
            return
        value = edit.text()
        self._diagram.rows[row_index].cells[key] = value
        column = _KIND_COLUMNS[self._diagram.kind].index(key)
        was_updating = self._updating
        self._updating = True
        try:
            item = self._table.item(row_index, column)
            if item is not None:
                item.setText(value)
        finally:
            self._updating = was_updating
        self._emit_changed()

    def _add_selected_role(self):
        self.add_row(str(self._role_combo.currentData() or ""))

    def _current_row(self) -> int:
        return self._table.currentRow()

    def _emit_changed(self):
        if not self._updating:
            self.diagram_changed.emit(self.diagram())


def _default_row(kind: str, role: str, rows: list[StructuredRow]) -> StructuredRow:
    if kind == "sequence":
        if role == "participant":
            return StructuredRow("participant", {"name": _unique_name(rows, "Participant")})
        return StructuredRow(
            "message",
            {
                "source": _first_value(rows, "name", "User"),
                "arrow": "->>",
                "target": _second_value(rows, "name", "App"),
                "text": "Message",
            },
        )

    if kind == "class":
        if role == "class":
            return StructuredRow("class", {"class": _unique_name(rows, "Class")})
        if role == "member":
            return StructuredRow(
                "member",
                {"class": _first_value(rows, "class", "Class"), "member": "+field"},
            )
        return StructuredRow(
            "relation",
            {
                "source": _first_value(rows, "class", "ClassA"),
                "left": "",
                "arrow": "-->",
                "right": "",
                "target": _second_value(rows, "class", "ClassB"),
            },
        )

    if kind == "state":
        return StructuredRow(
            "transition",
            {"source": "StateA", "target": "StateB", "label": "event"},
        )

    if kind == "er":
        if role == "entity":
            return StructuredRow("entity", {"entity": _unique_name(rows, "ENTITY")})
        if role == "field":
            return StructuredRow(
                "field",
                {
                    "entity": _first_value(rows, "entity", "ENTITY"),
                    "field_type": "string",
                    "field_name": "field",
                },
            )
        return StructuredRow(
            "relation",
            {
                "source": _first_value(rows, "entity", "ENTITY"),
                "connector": "||--o{",
                "target": _second_value(rows, "entity", "OTHER"),
                "label": "relates",
            },
        )

    return StructuredRow(role, {})


def _editable_fields(kind: str, role: str) -> list[str]:
    return _ROLE_FIELDS.get((kind, role), [])


def _first_value(rows: list[StructuredRow], key: str, fallback: str) -> str:
    for row in rows:
        value = row.cells.get(key, "").strip()
        if value:
            return value
    return fallback


def _second_value(rows: list[StructuredRow], key: str, fallback: str) -> str:
    found_first = False
    first = ""
    for row in rows:
        value = row.cells.get(key, "").strip()
        if not value:
            continue
        if not found_first:
            found_first = True
            first = value
            continue
        return value
    return fallback if found_first and first != fallback else fallback


def _unique_name(rows: list[StructuredRow], base: str) -> str:
    existing = {value for row in rows for value in row.cells.values()}
    if base not in existing:
        return base
    index = 2
    while f"{base}{index}" in existing:
        index += 1
    return f"{base}{index}"
