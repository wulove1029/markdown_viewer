"""Qt visual editor for the supported Mermaid flowchart subset."""

from __future__ import annotations

from copy import deepcopy
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .flowchart_model import (
    FlowEdge,
    FlowNode,
    FlowchartGraph,
    auto_layout_graph,
    default_flowchart,
)


class _NodeItem(QGraphicsPathItem):
    WIDTH = 140
    HEIGHT = 58

    def __init__(self, canvas: "FlowchartCanvas", node: FlowNode):
        super().__init__()
        self.canvas = canvas
        self.node_id = node.id
        self.shape = node.shape
        self.setPath(self._shape_path(node.shape))
        self.setBrush(QBrush(QColor("#f6f1ff")))
        self.setPen(QPen(QColor("#8b6cff"), 1.4))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPos(node.x, node.y)
        self.label = QGraphicsTextItem(node.label, self)
        self.label.setDefaultTextColor(QColor("#1d1f23"))
        self.label.setTextWidth(self.WIDTH - 20)
        self._center_label()

    def _shape_path(self, shape: str) -> QPainterPath:
        rect = QRectF(0, 0, self.WIDTH, self.HEIGHT)
        path = QPainterPath()
        if shape == "decision":
            path.moveTo(rect.center().x(), rect.top())
            path.lineTo(rect.right(), rect.center().y())
            path.lineTo(rect.center().x(), rect.bottom())
            path.lineTo(rect.left(), rect.center().y())
            path.closeSubpath()
        elif shape in ("start", "end"):
            path.addRoundedRect(rect, self.HEIGHT / 2, self.HEIGHT / 2)
        else:
            path.addRoundedRect(rect, 4, 4)
        return path

    def center(self) -> QPointF:
        return self.pos() + QPointF(self.WIDTH / 2, self.HEIGHT / 2)

    def set_label(self, text: str):
        self.label.setPlainText(text)
        self._center_label()

    def _center_label(self):
        rect = self.label.boundingRect()
        self.label.setPos(
            (self.WIDTH - rect.width()) / 2,
            (self.HEIGHT - rect.height()) / 2,
        )

    def itemChange(self, change, value):  # noqa: N802 (Qt override)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas._node_item_moved(self.node_id, self.pos())
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        if self.canvas._handle_node_click(self.node_id):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.canvas.edit_node_label(self.node_id)
        event.accept()


class _EdgeItem(QGraphicsPathItem):
    def __init__(self, canvas: "FlowchartCanvas", edge: FlowEdge):
        super().__init__()
        self.canvas = canvas
        self.edge_id = edge.id
        self.source = edge.source
        self.target = edge.target
        self.setZValue(-1)
        self.setPen(QPen(QColor("#575d66"), 1.3))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.label = QGraphicsTextItem(edge.label, self)
        self.label.setDefaultTextColor(QColor("#1d1f23"))
        self.update_path()

    def update_path(self):
        source = self.canvas._node_items.get(self.source)
        target = self.canvas._node_items.get(self.target)
        if source is None or target is None:
            return
        a = source.center()
        b = target.center()
        path = QPainterPath(a)
        dx = max(60.0, abs(b.x() - a.x()) / 2)
        c1 = QPointF(a.x() + dx, a.y())
        c2 = QPointF(b.x() - dx, b.y())
        if abs(b.y() - a.y()) > abs(b.x() - a.x()):
            dy = max(40.0, abs(b.y() - a.y()) / 2)
            c1 = QPointF(a.x(), a.y() + dy)
            c2 = QPointF(b.x(), b.y() - dy)
        path.cubicTo(c1, c2, b)
        self.setPath(path)
        self.label.setPos((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 - 22)

    def mouseDoubleClickEvent(self, event):
        self.canvas.edit_edge_label(self.edge_id)
        event.accept()


class _CanvasView(QGraphicsView):
    def __init__(self, canvas: "FlowchartCanvas", scene: QGraphicsScene):
        super().__init__(scene)
        self._canvas = canvas
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._canvas.delete_selected()
            return
        super().keyPressEvent(event)


class FlowchartCanvas(QWidget):
    graph_changed = pyqtSignal(object)
    visual_copy_requested = pyqtSignal()
    selection_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = default_flowchart()
        self._unsupported = ""
        self._updating = False
        self._updating_properties = False
        self._connect_mode = False
        self._connect_source: str | None = None
        self._selected_node_id: str | None = None
        self._selected_edge_id: str | None = None
        self._node_items: dict[str, _NodeItem] = {}
        self._edge_items: dict[str, _EdgeItem] = {}

        self._scene = QGraphicsScene(self)
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._view = _CanvasView(self, self._scene)
        self._view.setObjectName("flowchartCanvasView")

        self._message = QLabel("")
        self._message.setObjectName("flowchartCanvasMessage")
        self._message.setWordWrap(True)
        self._message.hide()

        self._visual_copy_btn = QPushButton("Create visual copy")
        self._visual_copy_btn.clicked.connect(self.visual_copy_requested.emit)
        self._visual_copy_btn.hide()

        self._direction_combo = QComboBox()
        self._direction_combo.addItem("Left to right", "LR")
        self._direction_combo.addItem("Top down", "TD")
        self._direction_combo.currentIndexChanged.connect(self._direction_changed)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(self._button("Start", lambda: self.add_node("start")))
        toolbar.addWidget(self._button("Process", lambda: self.add_node("process")))
        toolbar.addWidget(self._button("Decision", lambda: self.add_node("decision")))
        toolbar.addWidget(self._button("End", lambda: self.add_node("end")))
        self._connect_btn = self._button("Connect", self._toggle_connect_mode)
        self._connect_btn.setCheckable(True)
        toolbar.addWidget(self._connect_btn)
        toolbar.addWidget(self._button("Auto layout", self.auto_layout))
        toolbar.addWidget(self._button("Delete", self.delete_selected))
        toolbar.addStretch()

        editor_row = QWidget()
        editor_row_layout = QHBoxLayout(editor_row)
        editor_row_layout.setContentsMargins(0, 0, 0, 0)
        editor_row_layout.setSpacing(8)
        editor_row_layout.addWidget(self._view, stretch=1)
        editor_row_layout.addWidget(self._build_properties_panel())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self._message)
        layout.addWidget(self._visual_copy_btn)
        layout.addWidget(editor_row, stretch=1)

        self.set_graph(self._graph)

    def graph(self) -> FlowchartGraph:
        return deepcopy(self._graph)

    def set_graph(self, graph: FlowchartGraph):
        self._updating = True
        try:
            self._unsupported = ""
            self._graph = deepcopy(graph)
            self._selected_node_id = None
            self._selected_edge_id = None
            self._direction_combo.setCurrentIndex(
                1 if self._graph.direction == "TD" else 0
            )
            self._message.hide()
            self._visual_copy_btn.hide()
            self._view.setEnabled(True)
            self._properties.setEnabled(True)
            self._rebuild_scene()
        finally:
            self._updating = False

    def set_unsupported(self, reason: str, *, can_create_copy: bool = False):
        self._unsupported = reason
        self._message.setText(reason)
        self._message.show()
        self._visual_copy_btn.setVisible(can_create_copy)
        self._view.setEnabled(False)
        self._properties.setEnabled(False)

    def add_node(self, shape: str):
        labels = {
            "start": "Start",
            "process": "Process",
            "decision": "Decision?",
            "end": "Done",
        }
        source_id = self.selected_node_id()
        x = y = None
        if source_id:
            source = self._graph.node(source_id)
            if self._graph.direction == "TD":
                x = source.x
                y = source.y + 150
            else:
                x = source.x + 220
                y = source.y
        node = self._graph.add_node(
            label=labels.get(shape, "Process"), shape=shape, x=x, y=y
        )
        if source_id:
            self._graph.add_edge(source_id, node.id)
        elif len(self._graph.nodes) > 1:
            prev = self._graph.nodes[-2]
            node.x = prev.x + 180
            node.y = prev.y
        self._rebuild_scene(select_node_id=node.id)
        self._emit_changed()

    def add_edge(self, source: str, target: str, label: str = "") -> FlowEdge:
        edge = self._graph.add_edge(source, target, label)
        self._rebuild_scene(select_edge_id=edge.id)
        self._emit_changed()
        return edge

    def auto_layout(self):
        node_id = self._selected_node_id
        edge_id = self._selected_edge_id
        auto_layout_graph(self._graph)
        self._rebuild_scene(select_node_id=node_id, select_edge_id=edge_id)
        self._emit_changed()

    def set_direction(self, direction: str):
        self._graph.direction = "TD" if direction == "TD" else "LR"
        self._updating = True
        try:
            self._direction_combo.setCurrentIndex(
                1 if self._graph.direction == "TD" else 0
            )
        finally:
            self._updating = False
        self._update_properties()
        self._emit_changed()

    def selected_node_id(self) -> str | None:
        if self._selected_node_id and self._graph.find_node(self._selected_node_id):
            return self._selected_node_id
        return None

    def selected_edge_id(self) -> str | None:
        if self._selected_edge_id and any(
            edge.id == self._selected_edge_id for edge in self._graph.edges
        ):
            return self._selected_edge_id
        return None

    def select_node(self, node_id: str):
        item = self._node_items.get(node_id)
        if item is not None:
            self._scene.clearSelection()
            item.setSelected(True)

    def select_edge(self, edge_id: str):
        item = self._edge_items.get(edge_id)
        if item is not None:
            self._scene.clearSelection()
            item.setSelected(True)

    def delete_selected(self):
        node_ids: list[str] = []
        edge_ids: list[str] = []
        for item in self._scene.selectedItems():
            if isinstance(item, _NodeItem):
                node_ids.append(item.node_id)
            elif isinstance(item, _EdgeItem):
                edge_ids.append(item.edge_id)
        if not node_ids and not edge_ids:
            return
        for node_id in node_ids:
            self._graph.remove_node(node_id)
        for edge_id in edge_ids:
            self._graph.remove_edge(edge_id)
        self._rebuild_scene()
        self._emit_changed()

    def set_node_label(self, node_id: str, label: str):
        node = self._graph.node(node_id)
        node.label = label.strip() or node.label
        self._rebuild_scene(select_node_id=node_id)
        self._emit_changed()

    def set_node_shape(self, node_id: str, shape: str):
        node = self._graph.node(node_id)
        node.shape = (
            shape if shape in ("start", "process", "decision", "end") else "process"
        )
        self._rebuild_scene(select_node_id=node_id)
        self._emit_changed()

    def set_node_position(self, node_id: str, x: float, y: float):
        node = self._graph.node(node_id)
        node.x = float(x)
        node.y = float(y)
        item = self._node_items.get(node_id)
        if item is not None:
            self._updating = True
            try:
                item.setPos(node.x, node.y)
            finally:
                self._updating = False
        for edge_item in self._edge_items.values():
            if edge_item.source == node_id or edge_item.target == node_id:
                edge_item.update_path()
        self._scene.setSceneRect(self._items_rect())
        self._update_properties()
        self._emit_changed()

    def set_edge_label(self, edge_id: str, label: str):
        edge = self._edge(edge_id)
        edge.label = label.strip()
        self._rebuild_scene(select_edge_id=edge_id)
        self._emit_changed()

    def edit_node_label(self, node_id: str):
        node = self._graph.node(node_id)
        text, ok = QInputDialog.getText(self, "Node Label", "Label:", text=node.label)
        if not ok:
            return
        self.set_node_label(node_id, text)

    def edit_edge_label(self, edge_id: str):
        edge = self._edge(edge_id)
        text, ok = QInputDialog.getText(self, "Edge Label", "Label:", text=edge.label)
        if not ok:
            return
        self.set_edge_label(edge_id, text)

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _build_properties_panel(self) -> QWidget:
        self._properties = QWidget()
        self._properties.setObjectName("flowchartProperties")
        self._properties.setFixedWidth(240)
        layout = QVBoxLayout(self._properties)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        title = QLabel("Properties")
        title.setObjectName("flowchartPropertiesTitle")
        layout.addWidget(title)

        graph_form = QFormLayout()
        graph_form.setContentsMargins(0, 0, 0, 0)
        graph_form.addRow("Direction", self._direction_combo)
        layout.addLayout(graph_form)

        self._selection_stack = QStackedWidget()
        self._empty_panel = QLabel("Select a node or connector")
        self._empty_panel.setObjectName("flowchartPropertiesEmpty")
        self._empty_panel.setWordWrap(True)
        self._selection_stack.addWidget(self._empty_panel)
        self._selection_stack.addWidget(self._build_node_panel())
        self._selection_stack.addWidget(self._build_edge_panel())
        layout.addWidget(self._selection_stack, stretch=1)
        return self._properties

    def _build_node_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(0, 0, 0, 0)
        self._node_id = QLineEdit()
        self._node_id.setReadOnly(True)
        self._node_label = QLineEdit()
        self._node_label.editingFinished.connect(self._node_label_changed)
        self._node_shape = QComboBox()
        self._node_shape.addItem("Start", "start")
        self._node_shape.addItem("Process", "process")
        self._node_shape.addItem("Decision", "decision")
        self._node_shape.addItem("End", "end")
        self._node_shape.currentIndexChanged.connect(self._node_shape_changed)
        self._node_x = self._coord_input()
        self._node_y = self._coord_input()
        self._node_x.valueChanged.connect(self._node_position_changed)
        self._node_y.valueChanged.connect(self._node_position_changed)
        form.addRow("ID", self._node_id)
        form.addRow("Label", self._node_label)
        form.addRow("Shape", self._node_shape)
        form.addRow("X", self._node_x)
        form.addRow("Y", self._node_y)
        return panel

    def _build_edge_panel(self) -> QWidget:
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(0, 0, 0, 0)
        self._edge_id = QLineEdit()
        self._edge_id.setReadOnly(True)
        self._edge_source = QLineEdit()
        self._edge_source.setReadOnly(True)
        self._edge_target = QLineEdit()
        self._edge_target.setReadOnly(True)
        self._edge_label = QLineEdit()
        self._edge_label.editingFinished.connect(self._edge_label_changed)
        form.addRow("ID", self._edge_id)
        form.addRow("From", self._edge_source)
        form.addRow("To", self._edge_target)
        form.addRow("Label", self._edge_label)
        return panel

    def _coord_input(self) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(-10000, 10000)
        spin.setDecimals(1)
        spin.setSingleStep(10)
        return spin

    def _rebuild_scene(
        self,
        *,
        select_node_id: str | None = None,
        select_edge_id: str | None = None,
    ):
        if select_node_id is None and select_edge_id is None:
            select_node_id = self._selected_node_id
            select_edge_id = self._selected_edge_id
        self._scene.blockSignals(True)
        try:
            self._scene.clear()
            self._node_items.clear()
            self._edge_items.clear()
            for node in self._graph.nodes:
                item = _NodeItem(self, node)
                self._scene.addItem(item)
                self._node_items[node.id] = item
            for edge in self._graph.edges:
                item = _EdgeItem(self, edge)
                self._scene.addItem(item)
                self._edge_items[edge.id] = item
            if select_node_id in self._node_items:
                self._node_items[select_node_id].setSelected(True)
                select_edge_id = None
            elif select_edge_id in self._edge_items:
                self._edge_items[select_edge_id].setSelected(True)
            self._scene.setSceneRect(self._items_rect())
        finally:
            self._scene.blockSignals(False)
        self._on_scene_selection_changed()

    def _items_rect(self) -> QRectF:
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return QRectF(0, 0, 800, 500)
        return rect.adjusted(-80, -80, 160, 160)

    def _node_item_moved(self, node_id: str, pos: QPointF):
        if self._updating:
            return
        node = self._graph.node(node_id)
        if math.isclose(node.x, pos.x()) and math.isclose(node.y, pos.y()):
            return
        node.x = float(pos.x())
        node.y = float(pos.y())
        for item in self._edge_items.values():
            if item.source == node_id or item.target == node_id:
                item.update_path()
        self._scene.setSceneRect(self._items_rect())
        if self._selected_node_id == node_id:
            self._update_properties()
        self._emit_changed()

    def _handle_node_click(self, node_id: str) -> bool:
        if not self._connect_mode:
            return False
        if self._connect_source is None:
            self._connect_source = node_id
            item = self._node_items.get(node_id)
            if item:
                self._scene.clearSelection()
                item.setSelected(True)
            return True
        if self._connect_source != node_id:
            self.add_edge(self._connect_source, node_id)
        self._connect_source = None
        self._connect_btn.setChecked(False)
        self._connect_mode = False
        return True

    def _toggle_connect_mode(self):
        self._connect_mode = self._connect_btn.isChecked()
        self._connect_source = None

    def _direction_changed(self):
        if self._updating:
            return
        self.set_direction(str(self._direction_combo.currentData() or "LR"))

    def _on_scene_selection_changed(self):
        node_id = None
        edge_id = None
        for item in self._scene.selectedItems():
            if isinstance(item, _NodeItem):
                node_id = item.node_id
                break
            if isinstance(item, _EdgeItem):
                edge_id = item.edge_id
        self._selected_node_id = node_id
        self._selected_edge_id = None if node_id else edge_id
        self._update_properties()
        kind = "node" if node_id else "edge" if edge_id else ""
        self.selection_changed.emit(kind, node_id or edge_id or "")

    def _update_properties(self):
        if not hasattr(self, "_selection_stack"):
            return
        self._updating_properties = True
        try:
            self._direction_combo.setCurrentIndex(
                1 if self._graph.direction == "TD" else 0
            )
            if self._selected_node_id and self._graph.find_node(self._selected_node_id):
                node = self._graph.node(self._selected_node_id)
                self._selection_stack.setCurrentIndex(1)
                self._node_id.setText(node.id)
                self._node_label.setText(node.label)
                self._node_shape.setCurrentIndex(self._node_shape.findData(node.shape))
                self._node_x.setValue(node.x)
                self._node_y.setValue(node.y)
            elif self._selected_edge_id and any(
                edge.id == self._selected_edge_id for edge in self._graph.edges
            ):
                edge = self._edge(self._selected_edge_id)
                self._selection_stack.setCurrentIndex(2)
                self._edge_id.setText(edge.id)
                self._edge_source.setText(edge.source)
                self._edge_target.setText(edge.target)
                self._edge_label.setText(edge.label)
            else:
                self._selection_stack.setCurrentIndex(0)
        finally:
            self._updating_properties = False

    def _node_label_changed(self):
        if self._updating_properties or not self._selected_node_id:
            return
        self.set_node_label(self._selected_node_id, self._node_label.text())

    def _node_shape_changed(self):
        if self._updating_properties or not self._selected_node_id:
            return
        self.set_node_shape(
            self._selected_node_id,
            str(self._node_shape.currentData() or "process"),
        )

    def _node_position_changed(self):
        if self._updating_properties or not self._selected_node_id:
            return
        self.set_node_position(
            self._selected_node_id,
            self._node_x.value(),
            self._node_y.value(),
        )

    def _edge_label_changed(self):
        if self._updating_properties or not self._selected_edge_id:
            return
        self.set_edge_label(self._selected_edge_id, self._edge_label.text())

    def _edge(self, edge_id: str) -> FlowEdge:
        for edge in self._graph.edges:
            if edge.id == edge_id:
                return edge
        raise KeyError(edge_id)

    def _emit_changed(self):
        if not self._updating:
            self.graph_changed.emit(self.graph())
