"""Qt visual editor for Mermaid Gantt diagrams."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .gantt_model import GanttChart, GanttSection, GanttTask, default_gantt


class GanttEditor(QWidget):
    graph_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ganttEditor")
        self._chart = default_gantt()
        self._updating = False
        self._selected_task_id: str | None = None
        self._selected_section_name = ""

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(self._button("新增區段", self._add_section_clicked))
        toolbar.addWidget(self._button("新增任務", self._add_task_clicked))
        toolbar.addWidget(self._button("刪除任務", self.delete_selected_task))
        toolbar.addStretch()

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_current_item_changed)

        self._properties = self._build_properties()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(self._list, stretch=2)
        body.addWidget(self._properties, stretch=3)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addLayout(body, stretch=1)

        self.set_chart(self._chart)

    def chart(self) -> GanttChart:
        return deepcopy(self._chart)

    def set_chart(self, chart: GanttChart):
        self._updating = True
        try:
            self._chart = deepcopy(chart)
            self._selected_task_id = None
            self._selected_section_name = (
                self._chart.sections[0].name if self._chart.sections else ""
            )
            self._refresh_list()
            self._update_properties()
        finally:
            self._updating = False

    def add_section(self, name: str = "Section") -> GanttSection:
        section = self._chart.add_section(name or "Section")
        self._selected_section_name = section.name
        self._refresh_list()
        self._select_section(section.name)
        self._emit_changed()
        return section

    def add_task(self, section_name: str | None = None) -> GanttTask:
        task = self._chart.add_task(section_name or self._selected_section_name)
        self._selected_task_id = task.id
        self._refresh_list()
        self.select_task(task.id)
        self._emit_changed()
        return task

    def select_task(self, task_id: str):
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == ("task", task_id):
                self._list.setCurrentItem(item)
                return

    def set_task_name(self, task_id: str, name: str):
        task = self._chart.task(task_id)
        task.name = name.strip() or task.name
        self._refresh_list()
        self.select_task(task_id)
        self._emit_changed()

    def set_task_start(self, task_id: str, start: str):
        task = self._chart.task(task_id)
        task.start = start.strip() or task.start
        self._update_properties()
        self._emit_changed()

    def set_task_duration(self, task_id: str, duration: str):
        task = self._chart.task(task_id)
        task.duration = duration.strip() or task.duration
        self._update_properties()
        self._emit_changed()

    def delete_selected_task(self):
        if not self._selected_task_id:
            return
        self._chart.remove_task(self._selected_task_id)
        self._selected_task_id = None
        self._refresh_list()
        self._update_properties()
        self._emit_changed()

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _build_properties(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("ganttProperties")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        chart_title = QLabel("甘特圖")
        chart_title.setObjectName("flowchartPropertiesTitle")
        layout.addWidget(chart_title)

        chart_form = QFormLayout()
        chart_form.setContentsMargins(0, 0, 0, 0)
        self._title_edit = QLineEdit()
        self._title_edit.editingFinished.connect(self._chart_fields_changed)
        self._date_format_edit = QLineEdit()
        self._date_format_edit.editingFinished.connect(self._chart_fields_changed)
        self._axis_format_edit = QLineEdit()
        self._axis_format_edit.editingFinished.connect(self._chart_fields_changed)
        chart_form.addRow("標題", self._title_edit)
        chart_form.addRow("日期格式", self._date_format_edit)
        chart_form.addRow("軸格式", self._axis_format_edit)
        layout.addLayout(chart_form)

        self._stack = QStackedWidget()
        self._empty = QLabel("選取任務後可編輯名稱、狀態、起始時間與工期。")
        self._empty.setWordWrap(True)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._build_task_panel())
        layout.addWidget(self._stack, stretch=1)
        return panel

    def _build_task_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(0, 0, 0, 0)
        self._task_name = QLineEdit()
        self._task_name.editingFinished.connect(self._task_fields_changed)
        self._task_id = QLineEdit()
        self._task_id.editingFinished.connect(self._task_fields_changed)
        self._task_start = QLineEdit()
        self._task_start.editingFinished.connect(self._task_fields_changed)
        self._task_duration = QLineEdit()
        self._task_duration.editingFinished.connect(self._task_fields_changed)
        self._task_status = QComboBox()
        self._task_status.addItem("一般", "")
        self._task_status.addItem("進行中 active", "active")
        self._task_status.addItem("完成 done", "done")
        self._task_status.addItem("關鍵 crit", "crit")
        self._task_status.addItem("里程碑 milestone", "milestone")
        self._task_status.currentIndexChanged.connect(self._task_fields_changed)
        form.addRow("任務名稱", self._task_name)
        form.addRow("任務 ID", self._task_id)
        form.addRow("起始", self._task_start)
        form.addRow("工期", self._task_duration)
        form.addRow("狀態", self._task_status)
        return panel

    def _refresh_list(self):
        current = self._selected_task_id
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for section in self._chart.sections:
                section_item = QListWidgetItem(section.name)
                section_item.setData(Qt.ItemDataRole.UserRole, ("section", section.name))
                font = section_item.font()
                font.setBold(True)
                section_item.setFont(font)
                self._list.addItem(section_item)
                for task in section.tasks:
                    item = QListWidgetItem(f"  {task.name}   {task.start} / {task.duration}")
                    item.setData(Qt.ItemDataRole.UserRole, ("task", task.id))
                    self._list.addItem(item)
        finally:
            self._list.blockSignals(False)
        if current:
            self.select_task(current)

    def _select_section(self, section_name: str):
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == ("section", section_name):
                self._list.setCurrentItem(item)
                return

    def _on_current_item_changed(self, current, _previous):
        if current is None:
            self._selected_task_id = None
            self._update_properties()
            return
        kind, value = current.data(Qt.ItemDataRole.UserRole)
        if kind == "section":
            self._selected_section_name = value
            self._selected_task_id = None
        else:
            self._selected_task_id = value
            self._selected_section_name = self._section_name_for_task(value)
        self._update_properties()

    def _update_properties(self):
        self._updating = True
        try:
            self._title_edit.setText(self._chart.title)
            self._date_format_edit.setText(self._chart.date_format)
            self._axis_format_edit.setText(self._chart.axis_format)
            if not self._selected_task_id:
                self._stack.setCurrentIndex(0)
                return
            task = self._chart.task(self._selected_task_id)
            self._stack.setCurrentIndex(1)
            self._task_name.setText(task.name)
            self._task_id.setText(task.task_id)
            self._task_start.setText(task.start)
            self._task_duration.setText(task.duration)
            status = task.tags[0] if task.tags else ""
            index = self._task_status.findData(status)
            self._task_status.setCurrentIndex(max(0, index))
        finally:
            self._updating = False

    def _chart_fields_changed(self):
        if self._updating:
            return
        self._chart.title = self._title_edit.text().strip() or self._chart.title
        self._chart.date_format = (
            self._date_format_edit.text().strip() or self._chart.date_format
        )
        self._chart.axis_format = self._axis_format_edit.text().strip()
        self._emit_changed()

    def _task_fields_changed(self):
        if self._updating or not self._selected_task_id:
            return
        task = self._chart.task(self._selected_task_id)
        task.name = self._task_name.text().strip() or task.name
        task.task_id = self._task_id.text().strip()
        task.start = self._task_start.text().strip() or task.start
        task.duration = self._task_duration.text().strip() or task.duration
        status = str(self._task_status.currentData() or "")
        task.tags = [status] if status else []
        self._refresh_list()
        self.select_task(task.id)
        self._emit_changed()

    def _add_section_clicked(self):
        name, ok = QInputDialog.getText(self, "新增區段", "區段名稱：")
        if ok:
            self.add_section(name.strip() or "Section")

    def _add_task_clicked(self):
        self.add_task(self._selected_section_name)

    def _section_name_for_task(self, task_id: str) -> str:
        for section in self._chart.sections:
            for task in section.tasks:
                if task.id == task_id:
                    return section.name
        return ""

    def _emit_changed(self):
        if not self._updating:
            self.graph_changed.emit(self.chart())
