"""Native Qt Graph view for wiki-link relationships."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .document_libraries import DocumentLibraryStore
from .graph_model import (
    GraphData,
    GraphNode,
    assign_node_groups,
    build_graph,
    group_visibility,
    initial_positions,
    layout_step,
    separate_overlapping_nodes,
)
from .links import LinkIndex
from .theme import LIGHT, Theme, app_stylesheet


_GRAPH_HINT = "拖曳空白處平移 · 滾輪縮放 · 拖曳節點調整位置 · 點擊筆記開啟"
_EMPTY_EDGE_HINT = (
    "筆記之間尚無 [[連結]]——在筆記內文輸入 [[筆記名]] 建立連結後，"
    "關聯圖就會出現線條"
)


def _group_palette(theme: Theme, count: int = 8) -> list[QColor]:
    """Derive a readable cyclic graph palette from the active accent token."""

    accent = QColor(theme.accent)
    hue = accent.hslHueF()
    if hue < 0:
        hue = 0.6
    saturation = max(0.48, min(0.78, accent.hslSaturationF()))
    lightness = 0.39 if theme.name == "light" else 0.68
    offsets = (0.0, 0.12, 0.24, 0.39, 0.53, 0.66, 0.78, 0.9)
    return [
        QColor.fromHslF((hue + offsets[index % len(offsets)]) % 1.0, saturation, lightness)
        for index in range(max(0, count))
    ]


class _EdgeItem(QGraphicsLineItem):
    def __init__(self, source: "_NodeItem", target: "_NodeItem", theme: Theme):
        super().__init__()
        self.source = source
        self.target = target
        self._theme = theme
        self._highlighted = False
        self._dimmed = False
        self.setZValue(-1)
        self.apply_theme(theme)
        self.update_line()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        if self._highlighted:
            color = QColor(theme.accent)
            color.setAlpha(245)
            width = 2.2
        else:
            color = QColor(theme.text_subtle)
            if self._dimmed:
                color.setAlpha(32 if theme.name == "light" else 42)
                width = 1.8
            else:
                color.setAlpha(150 if theme.name == "light" else 175)
                width = 2.0
        self.setPen(QPen(color, width))

    def set_emphasis(self, *, highlighted: bool, dimmed: bool):
        if (highlighted, dimmed) == (self._highlighted, self._dimmed):
            return
        self._highlighted = highlighted
        self._dimmed = dimmed
        self.apply_theme(self._theme)

    def update_line(self):
        self.setLine(
            self.source.scenePos().x(),
            self.source.scenePos().y(),
            self.target.scenePos().x(),
            self.target.scenePos().y(),
        )


class _NodeItem(QGraphicsObject):
    def __init__(
        self,
        node: GraphNode,
        canvas: "GraphCanvas",
        theme: Theme,
        group: str | None,
    ):
        super().__init__()
        self.node = node
        self.canvas = canvas
        self.group = group
        self._theme = theme
        self._current = False
        self._hover_primary = False
        self._hover_related = False
        self._press_position = QPointF()
        self._font = QFont("Segoe UI", 9)
        metrics = QFontMetricsF(self._font)
        self._width = max(58.0, metrics.horizontalAdvance(node.label) + 26.0)
        self._height = 34.0
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("不存在的筆記" if node.ghost else (node.path or node.label))

    def boundingRect(self) -> QRectF:
        return QRectF(-self._width / 2, -self._height / 2, self._width, self._height)

    def paint(self, painter: QPainter, _option, _widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.node.ghost:
            pen = QPen(QColor(self._theme.text_subtle), 1.3, Qt.PenStyle.DashLine)
            fill = QColor(self._theme.surface_alt)
            text_color = QColor(self._theme.text_subtle)
        else:
            group_color = self.canvas.group_color(self.group)
            fill = QColor(group_color)
            fill.setAlpha(44 if self._theme.name == "light" else 62)
            if self._current or self._hover_primary:
                pen = QPen(QColor(self._theme.accent), 2.6)
            elif self._hover_related:
                pen = QPen(group_color, 2.0)
            else:
                pen = QPen(group_color, 1.4)
            text_color = QColor(self._theme.text)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(self.boundingRect(), 8, 8)
        painter.setPen(text_color)
        painter.setFont(self._font)
        painter.drawText(self.boundingRect(), Qt.AlignmentFlag.AlignCenter, self.node.label)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.update()

    def set_current(self, current: bool):
        current = bool(current and not self.node.ghost)
        if current != self._current:
            self._current = current
            self.update()

    def set_hover_emphasis(self, *, primary: bool, related: bool, dimmed: bool):
        self._hover_primary = primary
        self._hover_related = related
        self.setOpacity(0.2 if dimmed else 1.0)
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.canvas._on_node_position_changed(self.node.id, value)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._press_position = self.pos()
        self.canvas._pinned.add(self.node.id)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        moved = math.hypot(
            self.pos().x() - self._press_position.x(),
            self.pos().y() - self._press_position.y(),
        )
        super().mouseReleaseEvent(event)
        self.canvas.release_node(self.node.id, moved >= 3.0)
        if moved < 3.0:
            self.canvas.activate_node(self.node.id)

    def hoverEnterEvent(self, event):
        self.canvas.set_hovered_node(self.node.id)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.canvas.set_hovered_node(None)
        super().hoverLeaveEvent(event)


class _GraphGraphicsView(QGraphicsView):
    MIN_ZOOM = 0.2
    MAX_ZOOM = 4.0
    MAX_FIT_ZOOM = 1.5

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self._zoom = 1.0
        self._panning = False
        self._pan_last = QPoint()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        factor = 1.15 ** (delta / 120.0)
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        actual = target / self._zoom
        self._zoom = target
        self.scale(actual, actual)
        event.accept()

    def mousePressEvent(self, event):
        background = self.itemAt(event.position().toPoint()) is None
        if event.button() in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton) or (
            event.button() == Qt.MouseButton.LeftButton and background
        ):
            self._panning = True
            self._pan_last = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            current = event.position().toPoint()
            delta = current - self._pan_last
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_last = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning:
            self._panning = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class GraphCanvas(QWidget):
    """Container facade around the graph scene, items, and frame timer."""

    def __init__(self, on_open_path: Callable[[str], None], parent=None):
        super().__init__(parent)
        self._on_open_path = on_open_path
        self._theme = LIGHT
        self._graph = GraphData((), ())
        self._positions: dict[str, tuple[float, float]] = {}
        self._pinned: set[str] = set()
        self._node_items: dict[str, _NodeItem] = {}
        self._edge_items: list[_EdgeItem] = []
        self._edges_by_node: dict[str, list[_EdgeItem]] = {}
        self._node_groups: dict[str, str | None] = {}
        self._hidden_groups: set[str] = set()
        self._group_colors: dict[str, QColor] = {}
        self._hovered_node: str | None = None
        self._scene = QGraphicsScene(self)
        self.view = _GraphGraphicsView(self._scene, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._layout_frame)
        self._iteration = 0
        self._temperature = 14.0

    @property
    def graph(self) -> GraphData:
        return self._graph

    @property
    def group_names(self) -> tuple[str, ...]:
        return tuple(sorted({group for group in self._node_groups.values() if group}))

    def group_color(self, group: str | None) -> QColor:
        return QColor(self._group_colors.get(group or "", QColor(self._theme.border)))

    def set_graph(
        self,
        graph: GraphData,
        current_path: str | None = None,
        node_groups: dict[str, str | None] | None = None,
    ):
        self._timer.stop()
        self._scene.clear()
        self._graph = graph
        self._positions = initial_positions(graph.nodes)
        self._pinned.clear()
        self._node_items = {}
        self._edge_items = []
        self._edges_by_node = {}
        self._node_groups = dict(node_groups or {node.id: None for node in graph.nodes})
        self._hidden_groups.clear()
        self._hovered_node = None
        self._rebuild_group_colors()

        if not graph.nodes:
            empty = self._scene.addText("目前的文件庫沒有 Markdown 筆記")
            empty.setDefaultTextColor(QColor(self._theme.text_muted))
            self._scene.setSceneRect(empty.boundingRect().adjusted(-80, -60, 80, 60))
            return

        for node in graph.nodes:
            item = _NodeItem(
                node,
                self,
                self._theme,
                self._node_groups.get(node.id),
            )
            self._scene.addItem(item)
            x, y = self._positions[node.id]
            item.setPos(x, y)
            self._node_items[node.id] = item
        self._positions = separate_overlapping_nodes(
            self._positions,
            self._node_sizes(),
            iterations=32,
        )
        for node_id, position in self._positions.items():
            self._node_items[node_id].setPos(*position)
        for edge in graph.edges:
            source = self._node_items.get(edge.source)
            target = self._node_items.get(edge.target)
            if source is None or target is None:
                continue
            item = _EdgeItem(source, target, self._theme)
            self._scene.addItem(item)
            self._edge_items.append(item)
            self._edges_by_node.setdefault(edge.source, []).append(item)
            self._edges_by_node.setdefault(edge.target, []).append(item)

        self.set_current_path(current_path)
        self._update_scene_rect()
        self._iteration = 0
        self._temperature = 14.0
        self._timer.start()
        QTimer.singleShot(0, self.fit_graph)

    def set_current_path(self, path: str | None):
        current = str(Path(path)).casefold() if path else None
        for item in self._node_items.values():
            item_path = item.node.path.casefold() if item.node.path else None
            item.set_current(bool(current and item_path == current))

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._rebuild_group_colors()
        self._scene.setBackgroundBrush(QColor(theme.window))
        for item in self._node_items.values():
            item.apply_theme(theme)
        for item in self._edge_items:
            item.apply_theme(theme)
        for item in self._scene.items():
            if hasattr(item, "setDefaultTextColor"):
                item.setDefaultTextColor(QColor(theme.text_muted))

    def set_group_visible(self, group: str, visible: bool):
        if visible:
            self._hidden_groups.discard(group)
        else:
            self._hidden_groups.add(group)
        visibility = group_visibility(self._node_groups, self._hidden_groups)
        for node_id, item in self._node_items.items():
            item.setVisible(visibility.get(node_id, True))
        for edge in self._edge_items:
            edge.setVisible(edge.source.isVisible() and edge.target.isVisible())
        if self._hovered_node and not visibility.get(self._hovered_node, True):
            self._hovered_node = None
        self._apply_hover_emphasis()
        self._update_scene_rect()

    def set_hovered_node(self, node_id: str | None):
        if node_id is not None:
            item = self._node_items.get(node_id)
            if item is None or not item.isVisible():
                node_id = None
        self._hovered_node = node_id
        self._apply_hover_emphasis()

    def release_node(self, node_id: str, moved: bool):
        self._pinned.discard(node_id)
        if not moved:
            return
        self._positions = separate_overlapping_nodes(
            self._positions,
            self._node_sizes(),
            pinned={node_id},
            iterations=32,
        )
        for other_id, position in self._positions.items():
            item = self._node_items.get(other_id)
            if item is not None and other_id != node_id:
                item.setPos(*position)
        self._update_scene_rect()

    def activate_node(self, node_id: str):
        item = self._node_items.get(node_id)
        if item is None or item.node.ghost or not item.node.path:
            return
        if Path(item.node.path).exists():
            self._on_open_path(item.node.path)

    def fit_graph(self):
        bounds = self._scene.itemsBoundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            self.view.fitInView(
                bounds.adjusted(-70, -70, 70, 70),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            scale = self.view.transform().m11()
            bounded = max(self.view.MIN_ZOOM, min(self.view.MAX_FIT_ZOOM, scale))
            if scale > 0 and not math.isclose(scale, bounded):
                self.view.scale(bounded / scale, bounded / scale)
            self.view._zoom = bounded

    def _on_node_position_changed(self, node_id: str, point: QPointF):
        self._positions[node_id] = (point.x(), point.y())
        for edge in self._edges_by_node.get(node_id, ()):
            edge.update_line()

    def _layout_frame(self):
        if not self._positions:
            self._timer.stop()
            return
        movement = 0.0
        for _ in range(2):
            previous = self._positions
            updated, _movement = layout_step(
                previous,
                self._graph.edges,
                temperature=self._temperature,
                pinned=self._pinned,
            )
            self._positions = separate_overlapping_nodes(
                updated,
                self._node_sizes(),
                pinned=self._pinned,
                iterations=2,
                ensure_separated=False,
            )
            movement = sum(
                math.hypot(
                    self._positions[node_id][0] - previous[node_id][0],
                    self._positions[node_id][1] - previous[node_id][1],
                )
                for node_id in self._positions
            )
            self._temperature = max(0.45, self._temperature * 0.975)
            self._iteration += 1
        for node_id, position in self._positions.items():
            item = self._node_items.get(node_id)
            if item is not None and node_id not in self._pinned:
                item.setPos(*position)
        if self._iteration % 10 == 0:
            self._update_scene_rect()
        if self._iteration >= 180 or movement < 0.02:
            self._positions = separate_overlapping_nodes(
                self._positions,
                self._node_sizes(),
                pinned=self._pinned,
                iterations=32,
            )
            for node_id, position in self._positions.items():
                item = self._node_items.get(node_id)
                if item is not None and node_id not in self._pinned:
                    item.setPos(*position)
            self._timer.stop()
            self._update_scene_rect()

    def _node_sizes(self) -> dict[str, tuple[float, float]]:
        return {
            node_id: (item.boundingRect().width(), item.boundingRect().height())
            for node_id, item in self._node_items.items()
        }

    def _rebuild_group_colors(self):
        palette = _group_palette(self._theme)
        self._group_colors = {
            group: palette[index % len(palette)]
            for index, group in enumerate(self.group_names)
        }

    def _apply_hover_emphasis(self):
        hovered = self._hovered_node
        connected_edges = set(self._edges_by_node.get(hovered, ())) if hovered else set()
        focused = {hovered} if hovered else set()
        for edge in connected_edges:
            focused.add(edge.source.node.id)
            focused.add(edge.target.node.id)
        for node_id, item in self._node_items.items():
            item.set_hover_emphasis(
                primary=node_id == hovered,
                related=bool(hovered and node_id in focused and node_id != hovered),
                dimmed=bool(hovered and node_id not in focused),
            )
        for edge in self._edge_items:
            edge.set_emphasis(
                highlighted=edge in connected_edges,
                dimmed=bool(hovered and edge not in connected_edges),
            )

    def _update_scene_rect(self):
        bounds = self._scene.itemsBoundingRect()
        if bounds.isValid():
            self._scene.setSceneRect(bounds.adjusted(-140, -140, 140, 140))


class GraphWindow(QDialog):
    def __init__(self, on_open_path: Callable[[str], None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("筆記關聯圖")
        self.setModal(False)
        self.resize(960, 700)
        self._theme = LIGHT
        self.canvas = GraphCanvas(on_open_path, self)
        self._legend_buttons: dict[str, QToolButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._legend = QWidget(self)
        self._legend.setObjectName("graphLegend")
        self._legend_layout = QHBoxLayout(self._legend)
        self._legend_layout.setContentsMargins(12, 7, 12, 7)
        self._legend_layout.setSpacing(8)
        layout.addWidget(self._legend)
        layout.addWidget(self.canvas, 1)
        hint = QLabel(_GRAPH_HINT)
        hint.setObjectName("graphHint")
        hint.setContentsMargins(12, 7, 12, 7)
        layout.addWidget(hint)
        self._hint = hint
        self.apply_theme(LIGHT)

    @property
    def graph(self) -> GraphData:
        return self.canvas.graph

    def set_index(
        self,
        index: LinkIndex,
        current_path: str | None = None,
        libraries: list[object] | None = None,
    ):
        graph = build_graph(index)
        if libraries is None:
            try:
                libraries = list(DocumentLibraryStore().load())
            except Exception:
                libraries = []
        groups = assign_node_groups(graph.nodes, libraries)
        self.canvas.set_graph(graph, current_path, groups)
        self._hint.setText(_EMPTY_EDGE_HINT if not graph.edges else _GRAPH_HINT)
        self._rebuild_legend()

    def set_current_path(self, path: str | None):
        self.canvas.set_current_path(path)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(
            app_stylesheet(theme)
            + f"QWidget#graphLegend {{ background: {theme.surface}; border-bottom: 1px solid {theme.border}; }}"
            + f"QLabel#graphHint {{ background: {theme.surface}; color: {theme.text_muted}; border-top: 1px solid {theme.border}; }}"
        )
        self.canvas.apply_theme(theme)
        self._rebuild_legend()

    def _rebuild_legend(self):
        while self._legend_layout.count():
            child = self._legend_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        self._legend_buttons = {}
        groups = self.canvas.group_names
        for group in groups:
            color = self.canvas.group_color(group)
            pixmap = QPixmap(12, 12)
            pixmap.fill(color)
            button = QToolButton(self._legend)
            button.setText(group)
            button.setIcon(QIcon(pixmap))
            button.setIconSize(QSize(12, 12))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setChecked(group not in self.canvas._hidden_groups)
            button.setToolTip(f"顯示或隱藏「{group}」群組")
            button.toggled.connect(
                lambda visible, group_name=group: self.canvas.set_group_visible(
                    group_name, visible
                )
            )
            self._legend_layout.addWidget(button)
            self._legend_buttons[group] = button
        self._legend_layout.addStretch(1)
        self._legend.setVisible(bool(groups))
