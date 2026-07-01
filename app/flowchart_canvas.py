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
    QSplitter,
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
        
        # Read colors from theme
        theme = getattr(canvas, "_theme", None)
        if theme:
            if theme.name == "dark":
                bg_color = QColor("#2a204a")
                border_color = QColor("#9d80ff")
                text_color = QColor(theme.text)
            else:
                bg_color = QColor("#f4ebff")
                border_color = QColor("#7f56d9")
                text_color = QColor(theme.text)
        else:
            bg_color = QColor("#f6f1ff")
            border_color = QColor("#8b6cff")
            text_color = QColor("#1d1f23")

        self.setBrush(QBrush(bg_color))
        self.setPen(QPen(border_color, 1.4))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setPos(node.x, node.y)
        self.label = QGraphicsTextItem(node.label, self)
        self.label.setDefaultTextColor(text_color)
        self.label.setTextWidth(self.WIDTH - 20)
        self._center_label()
        self.setAcceptHoverEvents(True)
        self._is_hovered = False

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

    def hoverEnterEvent(self, event):
        self._is_hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._is_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None):
        from PyQt6.QtWidgets import QStyle
        
        is_selected = self.isSelected()
        old_state = option.state
        if is_selected:
            option.state &= ~QStyle.StateFlag.State_Selected
            
        super().paint(painter, option, widget)
        
        option.state = old_state
        
        canvas = self.canvas
        if is_selected:
            theme = getattr(canvas, "_theme", None)
            accent = QColor(theme.accent) if theme else QColor("#8b6cff")
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(accent, 2.5, Qt.PenStyle.SolidLine))
            painter.drawPath(self.path())
            painter.restore()

        # Draw connection ports if in Connect Mode and hovered (or selected as source)
        if canvas._connect_mode and (self._is_hovered or canvas._connect_source == self.node_id):
            theme = getattr(canvas, "_theme", None)
            accent = QColor(theme.accent) if theme else QColor("#8b6cff")
            port_fill = QColor(theme.surface) if theme else QColor("#ffffff")
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(accent, 1.2))
            painter.setBrush(QBrush(port_fill))
            
            r = 3.5 # port radius
            painter.drawEllipse(QPointF(self.WIDTH / 2, 0), r, r)
            painter.drawEllipse(QPointF(self.WIDTH / 2, self.HEIGHT), r, r)
            painter.drawEllipse(QPointF(0, self.HEIGHT / 2), r, r)
            painter.drawEllipse(QPointF(self.WIDTH, self.HEIGHT / 2), r, r)
            painter.restore()

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
        return value

    def mousePressEvent(self, event):
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        canvas = self.canvas
        if canvas._connect_mode:
            if canvas._connect_source is None:
                canvas._connect_source = self.node_id
                canvas._scene.clearSelection()
                self.setSelected(True)
                canvas._update_info_bar()
            else:
                if canvas._connect_source != self.node_id:
                    canvas._clear_temp_line()
                    canvas.add_edge(canvas._connect_source, self.node_id)
                    canvas._connect_source = None
                    canvas._scene.clearSelection()
                    canvas._update_info_bar()
                else:
                    canvas._connect_source = None
                    canvas._clear_temp_line()
                    canvas._scene.clearSelection()
                    canvas._update_info_bar()
            event.accept()
            return
        
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
        
        # Read colors from theme
        theme = getattr(canvas, "_theme", None)
        if theme:
            pen_color = QColor(theme.text_subtle)
            text_color = QColor(theme.text)
        else:
            pen_color = QColor("#575d66")
            text_color = QColor("#1d1f23")
            
        self.setPen(QPen(pen_color, 1.3))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.label = QGraphicsTextItem(edge.label, self)
        self.label.setDefaultTextColor(text_color)
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

    def paint(self, painter: QPainter, option, widget=None):
        from PyQt6.QtWidgets import QStyle
        
        is_selected = self.isSelected()
        old_state = option.state
        if is_selected:
            option.state &= ~QStyle.StateFlag.State_Selected
            
        super().paint(painter, option, widget)
        
        option.state = old_state
        
        if is_selected:
            theme = getattr(self.canvas, "_theme", None)
            accent = QColor(theme.accent) if theme else QColor("#8b6cff")
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(accent, 2.2, Qt.PenStyle.SolidLine))
            painter.drawPath(self.path())
            painter.restore()

    def mouseDoubleClickEvent(self, event):
        self.canvas.edit_edge_label(self.edge_id)
        event.accept()


class _CanvasView(QGraphicsView):
    MIN_ZOOM = 0.35
    MAX_ZOOM = 3.0
    ZOOM_STEP = 1.15

    def __init__(self, canvas: "FlowchartCanvas", scene: QGraphicsScene):
        super().__init__(scene)
        self._canvas = canvas
        self._zoom = 1.0
        self._is_panning = False
        self._pan_last_pos = None
        self._pan_drag_mode = QGraphicsView.DragMode.RubberBandDrag
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.viewport().setMouseTracking(True)

    def zoom_factor(self) -> float:
        return self._zoom

    def zoom_by(self, factor: float):
        if factor <= 0:
            return
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        actual = target / self._zoom
        if math.isclose(actual, 1.0):
            return
        self._zoom = target
        self.scale(actual, actual)

    def reset_zoom(self):
        self._zoom = 1.0
        self.resetTransform()

    def pan_by(self, dx: int, dy: int):
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + dx)
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + dy)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        steps = delta / 120
        self.zoom_by(self.ZOOM_STEP ** steps)
        event.accept()

    def mousePressEvent(self, event):
        if self._should_start_pan(event):
            self._start_pan(event.pos())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._is_panning and self._pan_last_pos is not None:
            delta = event.pos() - self._pan_last_pos
            self.pan_by(-delta.x(), -delta.y())
            self._pan_last_pos = event.pos()
            event.accept()
            return
        super().mouseMoveEvent(event)
        if self._canvas._connect_mode and self._canvas._connect_source:
            scene_pos = self.mapToScene(event.pos())
            try:
                self._canvas._update_temp_line(scene_pos)
            except Exception:
                pass

    def mouseReleaseEvent(self, event):
        if self._is_panning:
            self._finish_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._is_panning:
            self._finish_pan()
        super().leaveEvent(event)

    def _should_start_pan(self, event) -> bool:
        button = event.button()
        if button in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            return True
        if button != Qt.MouseButton.LeftButton or self._canvas._connect_mode:
            return False
        return self.itemAt(event.pos()) is None

    def _start_pan(self, position):
        self._is_panning = True
        self._pan_last_pos = position
        self._pan_drag_mode = self.dragMode()
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _finish_pan(self):
        self._is_panning = False
        self._pan_last_pos = None
        self.setDragMode(self._pan_drag_mode)
        self.viewport().unsetCursor()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._canvas.delete_selected()
            return
        is_reset_zoom = (
            event.key() == Qt.Key.Key_0
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        )
        if is_reset_zoom:
            self.reset_zoom()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self._canvas._connect_mode:
                if self._canvas._connect_source is not None:
                    self._canvas._connect_source = None
                    self._canvas._clear_temp_line()
                    self._canvas._scene.clearSelection()
                    self._canvas._update_info_bar()
                else:
                    self._canvas._cancel_connect_mode()
                event.accept()
                return
        super().keyPressEvent(event)


class FlowchartCanvas(QWidget):
    graph_changed = pyqtSignal(object)
    visual_copy_requested = pyqtSignal()
    selection_changed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("flowchartCanvas")
        self._graph = default_flowchart()
        self._theme = None
        self._unsupported = ""
        self._updating = False
        self._updating_properties = False
        self._connect_mode = False
        self._connect_source: str | None = None
        self._is_dragging_connect = False
        self._drag_start_pos = None
        self._selected_node_id: str | None = None
        self._selected_edge_id: str | None = None
        self._node_items: dict[str, _NodeItem] = {}
        self._edge_items: dict[str, _EdgeItem] = {}

        self._scene = QGraphicsScene(self)
        self._scene.selectionChanged.connect(self._on_scene_selection_changed)
        self._view = _CanvasView(self, self._scene)
        self._view.setObjectName("flowchartCanvasView")
        self._apply_scene_background()

        self._message = QLabel("")
        self._message.setObjectName("flowchartCanvasMessage")
        self._message.setWordWrap(True)
        self._message.hide()

        self._visual_copy_btn = QPushButton("建立視覺化複本")
        self._visual_copy_btn.clicked.connect(self.visual_copy_requested.emit)
        self._visual_copy_btn.hide()

        self._direction_combo = QComboBox()
        self._direction_combo.addItem("由左至右 (LR)", "LR")
        self._direction_combo.addItem("由上至下 (TD)", "TD")
        self._direction_combo.currentIndexChanged.connect(self._direction_changed)

        self._toolbar_widget = QWidget()
        self._toolbar_widget.setObjectName("flowchartToolbar")
        toolbar = QHBoxLayout(self._toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        toolbar.addWidget(self._button("起點", lambda: self.add_node("start")))
        toolbar.addWidget(self._button("程序", lambda: self.add_node("process")))
        toolbar.addWidget(self._button("決策", lambda: self.add_node("decision")))
        toolbar.addWidget(self._button("終點", lambda: self.add_node("end")))
        self._connect_btn = self._button("連線", self._toggle_connect_mode)
        self._connect_btn.setCheckable(True)
        toolbar.addWidget(self._connect_btn)
        toolbar.addWidget(self._button("自動佈局", self.auto_layout))
        toolbar.addWidget(self._button("刪除", self.delete_selected))
        toolbar.addStretch()

        self._info_bar = QLabel("")
        self._info_bar.setObjectName("flowchartCanvasInfoBar")
        self._info_bar.setWordWrap(True)

        self._editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._editor_splitter.addWidget(self._view)
        self._editor_splitter.addWidget(self._build_properties_panel())
        self._editor_splitter.setStretchFactor(0, 4)
        self._editor_splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._toolbar_widget)
        layout.addWidget(self._info_bar)
        layout.addWidget(self._message)
        layout.addWidget(self._visual_copy_btn)
        layout.addWidget(self._editor_splitter, stretch=1)

        self._update_info_bar()
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
            self._toolbar_widget.setEnabled(True)
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
        self._toolbar_widget.setEnabled(False)
        self._clear_temp_line()
        self._scene.clear()
        self._node_items.clear()
        self._edge_items.clear()
        self._selected_node_id = None
        self._selected_edge_id = None
        self._graph = FlowchartGraph()
        self._update_properties()

    def add_node(self, shape: str):
        labels = {
            "start": "起點",
            "process": "程序",
            "decision": "決策？",
            "end": "結束",
        }
        if self._connect_mode:
            self._cancel_connect_mode()
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
        self.select_node(node_id)
        self._node_label.setFocus()
        self._node_label.selectAll()

    def edit_edge_label(self, edge_id: str):
        self.select_edge(edge_id)
        self._edge_label.setFocus()
        self._edge_label.selectAll()

    def _button(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(slot)
        return button

    def _build_properties_panel(self) -> QWidget:
        self._properties = QWidget()
        self._properties.setObjectName("flowchartProperties")
        self._properties.setMinimumWidth(200)
        layout = QVBoxLayout(self._properties)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        title = QLabel("屬性")
        title.setObjectName("flowchartPropertiesTitle")
        layout.addWidget(title)

        graph_form = QFormLayout()
        graph_form.setContentsMargins(0, 0, 0, 0)
        graph_form.addRow("版面方向", self._direction_combo)
        layout.addLayout(graph_form)

        self._selection_stack = QStackedWidget()
        self._empty_panel = QLabel(
            "<b>視覺化編輯器快速指南</b><br><br>"
            "• <b>新增節點</b>：點選上方形狀按鈕。<br>"
            "• <b>自動連線</b>：先選取一個節點，再點選形狀按鈕。<br>"
            "• <b>建立連線</b>：點選<b>「連線」</b>，點擊起點節點，再點擊終點節點。<br>"
            "• <b>修改名稱</b>：雙擊畫布物件，或選取物件後在此修改標籤。<br>"
            "• <b>移動節點</b>：直接拖曳畫布上的節點。<br>"
            "• <b>刪除物件</b>：選取後按 <b>Delete</b> 鍵或點選上方「刪除」按鈕。"
        )
        self._empty_panel.setTextFormat(Qt.TextFormat.RichText)
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
        self._node_shape.addItem("起點", "start")
        self._node_shape.addItem("程序", "process")
        self._node_shape.addItem("決策", "decision")
        self._node_shape.addItem("終點", "end")
        self._node_shape.currentIndexChanged.connect(self._node_shape_changed)
        self._node_x = self._coord_input()
        self._node_y = self._coord_input()
        self._node_x.valueChanged.connect(self._node_position_changed)
        self._node_y.valueChanged.connect(self._node_position_changed)
        
        self._node_connect_to = QComboBox()
        self._node_connect_btn = QPushButton("建立連線")
        self._node_connect_btn.clicked.connect(self._create_link_from_dropdown)
        
        form.addRow("識別碼", self._node_id)
        form.addRow("標籤文字", self._node_label)
        form.addRow("節點形狀", self._node_shape)
        form.addRow("位置 X", self._node_x)
        form.addRow("位置 Y", self._node_y)
        form.addRow("連接至", self._node_connect_to)
        form.addRow("", self._node_connect_btn)
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
        form.addRow("識別碼", self._edge_id)
        form.addRow("起點節點", self._edge_source)
        form.addRow("終點節點", self._edge_target)
        form.addRow("連線標籤", self._edge_label)
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
        return False

    def _toggle_connect_mode(self):
        self._connect_mode = self._connect_btn.isChecked()
        self._connect_source = None
        self._clear_temp_line()
        if self._connect_mode:
            self._view.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._view.setCursor(Qt.CursorShape.ArrowCursor)
        self._update_info_bar()

    def _cancel_connect_mode(self):
        self._connect_mode = False
        self._connect_source = None
        self._connect_btn.setChecked(False)
        self._view.setCursor(Qt.CursorShape.ArrowCursor)
        self._clear_temp_line()
        self._update_info_bar()

    def _update_temp_line(self, scene_pos: QPointF):
        if not self._connect_source or self._connect_source not in self._node_items:
            return
        from PyQt6 import sip
        is_deleted = False
        if hasattr(self, "_temp_line_item") and self._temp_line_item is not None:
            try:
                is_deleted = sip.isdeleted(self._temp_line_item)
            except Exception:
                is_deleted = True
        if not hasattr(self, "_temp_line_item") or self._temp_line_item is None or is_deleted:
            self._temp_line_item = QGraphicsPathItem()
            theme = getattr(self, "_theme", None)
            accent = QColor(theme.accent) if theme else QColor("#8b6cff")
            self._temp_line_item.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
            self._temp_line_item.setZValue(-2)
            self._scene.addItem(self._temp_line_item)
        source_item = self._node_items[self._connect_source]
        start_pos = source_item.center()
        path = QPainterPath(start_pos)
        path.lineTo(scene_pos)
        try:
            self._temp_line_item.setPath(path)
        except Exception:
            self._temp_line_item = None

    def _clear_temp_line(self):
        from PyQt6 import sip
        if hasattr(self, "_temp_line_item") and self._temp_line_item is not None:
            try:
                if not sip.isdeleted(self._temp_line_item):
                    self._scene.removeItem(self._temp_line_item)
            except Exception:
                pass
            self._temp_line_item = None

    def _update_info_bar(self):
        if self._connect_mode:
            if self._connect_source is None:
                self._info_bar.setText("🔗 <b>連線模式</b>：請【雙擊】起點方塊以拉出虛線。（按 Esc 取消）")
            else:
                self._info_bar.setText(f"🔗 <b>連線模式</b>：請【雙擊】終點方塊以確認連線（起點：<b>{self._connect_source}</b>）。（按 Esc 取消）")
        else:
            node_id = self.selected_node_id()
            edge_id = self.selected_edge_id()
            if node_id:
                self._info_bar.setText(f"💡 <b>已選取節點 ({node_id})</b>：點選上方按鈕可新增並自動連線，非連線模式下雙擊可重新命名，或在右側編輯屬性。")
            elif edge_id:
                self._info_bar.setText(f"💡 <b>已選取連線 ({edge_id})</b>：雙擊可重新命名，可在右側編輯標籤，或按 Delete 刪除。")
            else:
                self._info_bar.setText(
                    "💡 <b>操作提示</b>：拖曳節點可移動位置；"
                    "在空白處按住拖曳可平移畫布，滾輪可縮放，Ctrl+0 可重設縮放。"
                    "先點選一個節點後再新增形狀，可自動連線。"
                )

    def apply_theme(self, theme):
        self._theme = theme
        self._apply_scene_background()
        self._rebuild_scene()

    def _apply_scene_background(self):
        color = QColor(self._theme.surface) if self._theme else QColor("#ffffff")
        brush = QBrush(color)
        self._scene.setBackgroundBrush(brush)
        self._view.setBackgroundBrush(brush)

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
        self._update_info_bar()
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
                
                self._node_connect_to.blockSignals(True)
                self._node_connect_to.clear()
                self._node_connect_to.addItem("-- 選擇目標節點 --", "")
                for other in self._graph.nodes:
                    if other.id != node.id:
                        self._node_connect_to.addItem(f"{other.label} ({other.id})", other.id)
                self._node_connect_to.blockSignals(False)
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

    def _create_link_from_dropdown(self):
        if not self._selected_node_id:
            return
        target_id = self._node_connect_to.currentData()
        if not target_id:
            return
        self.add_edge(self._selected_node_id, target_id)
        self._update_properties()
