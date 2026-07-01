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
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .flowchart_model import FlowEdge, FlowNode, FlowchartGraph, default_flowchart


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._graph = default_flowchart()
        self._unsupported = ""
        self._updating = False
        self._connect_mode = False
        self._connect_source: str | None = None
        self._node_items: dict[str, _NodeItem] = {}
        self._edge_items: dict[str, _EdgeItem] = {}

        self._scene = QGraphicsScene(self)
        self._view = _CanvasView(self, self._scene)
        self._view.setObjectName("flowchartCanvasView")

        self._message = QLabel("")
        self._message.setObjectName("flowchartCanvasMessage")
        self._message.setWordWrap(True)
        self._message.hide()

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
        toolbar.addWidget(self._button("Delete", self.delete_selected))
        toolbar.addWidget(QLabel("Direction"))
        toolbar.addWidget(self._direction_combo)
        toolbar.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self._message)
        layout.addWidget(self._view, stretch=1)

        self.set_graph(self._graph)

    def graph(self) -> FlowchartGraph:
        return deepcopy(self._graph)

    def set_graph(self, graph: FlowchartGraph):
        self._updating = True
        try:
            self._unsupported = ""
            self._graph = deepcopy(graph)
            self._direction_combo.setCurrentIndex(
                1 if self._graph.direction == "TD" else 0
            )
            self._message.hide()
            self._view.setEnabled(True)
            self._rebuild_scene()
        finally:
            self._updating = False

    def set_unsupported(self, reason: str):
        self._unsupported = reason
        self._message.setText(reason)
        self._message.show()
        self._view.setEnabled(False)

    def add_node(self, shape: str):
        labels = {
            "start": "Start",
            "process": "Process",
            "decision": "Decision?",
            "end": "Done",
        }
        node = self._graph.add_node(label=labels.get(shape, "Process"), shape=shape)
        if len(self._graph.nodes) > 1:
            prev = self._graph.nodes[-2]
            node.x = prev.x + 180
            node.y = prev.y
        self._rebuild_scene()
        self._emit_changed()

    def add_edge(self, source: str, target: str, label: str = ""):
        self._graph.add_edge(source, target, label)
        self._rebuild_scene()
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
        self._emit_changed()

    def select_node(self, node_id: str):
        item = self._node_items.get(node_id)
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

    def edit_node_label(self, node_id: str):
        node = self._graph.node(node_id)
        text, ok = QInputDialog.getText(self, "Node Label", "Label:", text=node.label)
        if not ok:
            return
        node.label = text.strip() or node.label
        self._rebuild_scene()
        self._emit_changed()

    def edit_edge_label(self, edge_id: str):
        edge = next(edge for edge in self._graph.edges if edge.id == edge_id)
        text, ok = QInputDialog.getText(self, "Edge Label", "Label:", text=edge.label)
        if not ok:
            return
        edge.label = text.strip()
        self._rebuild_scene()
        self._emit_changed()

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _rebuild_scene(self):
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
        self._scene.setSceneRect(self._items_rect())

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

    def _emit_changed(self):
        if not self._updating:
            self.graph_changed.emit(self.graph())
