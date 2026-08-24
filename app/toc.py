"""Table-of-contents panel for Markdown headings and PDF outlines."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .theme import LIGHT, Theme


_TARGET_ROLE = Qt.ItemDataRole.UserRole
_LEVEL_ROLE = Qt.ItemDataRole.UserRole.value + 1
_DEPTH_ROLE = Qt.ItemDataRole.UserRole.value + 2

_TOC_INDENT = 14
_TOC_LEFT_PADDING = 10
_TOC_RIGHT_PADDING = 10
_TOC_ROW_HEIGHT = 30
_MAX_TOC_DEPTH = 5


def _normalise_level(level) -> int:
    try:
        return max(1, int(level))
    except (TypeError, ValueError):
        return 1


def _visual_depth(level) -> int:
    return min(_MAX_TOC_DEPTH, _normalise_level(level) - 1)


class _TocDelegate(QStyledItemDelegate):
    """Paint compact heading rows with real indentation and hierarchy guides."""

    def __init__(self, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._guide_color = QColor(theme.border)
        self._guide_color.setAlpha(170)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._guide_color = QColor(theme.border)
        self._guide_color.setAlpha(170)
        if self.parent() is not None and hasattr(self.parent(), "viewport"):
            self.parent().viewport().update()

    @staticmethod
    def _depth(index) -> int:
        value = index.data(_DEPTH_ROLE)
        try:
            return min(_MAX_TOC_DEPTH, max(0, int(value)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _font_for(base_font: QFont, depth: int, enabled: bool = True) -> QFont:
        font = QFont(base_font)
        if not enabled:
            font.setWeight(QFont.Weight.Normal)
            return font
        if depth == 0:
            font.setWeight(QFont.Weight.DemiBold)
            point_size = font.pointSize()
            if point_size > 0:
                font.setPointSize(point_size + 1)
            elif font.pixelSize() > 0:
                font.setPixelSize(font.pixelSize() + 1)
        elif depth == 1:
            font.setWeight(QFont.Weight.Medium)
        else:
            font.setWeight(QFont.Weight.Normal)
        return font

    @staticmethod
    def text_rect(
        rect: QRect | QRectF,
        depth: int,
        direction: Qt.LayoutDirection = Qt.LayoutDirection.LeftToRight,
    ) -> QRectF:
        """Available title geometry; exposed to keep indentation testable."""
        rect = QRectF(rect)
        depth = min(_MAX_TOC_DEPTH, max(0, int(depth)))
        indent = _TOC_LEFT_PADDING + depth * _TOC_INDENT
        if direction == Qt.LayoutDirection.RightToLeft:
            return QRectF(
                rect.left() + _TOC_RIGHT_PADDING,
                rect.top(),
                max(0.0, rect.width() - indent - _TOC_RIGHT_PADDING),
                rect.height(),
            )
        return QRectF(
            rect.left() + indent,
            rect.top(),
            max(0.0, rect.width() - indent - _TOC_RIGHT_PADDING),
            rect.height(),
        )

    def sizeHint(self, option, index):  # noqa: N802 (Qt override)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        font = self._font_for(opt.font, self._depth(index), enabled)
        base = super().sizeHint(opt, index)
        return QSize(
            base.width(),
            max(_TOC_ROW_HEIGHT, QFontMetrics(font).height() + 8),
        )

    def paint(self, painter, option, index):  # noqa: N802 (Qt override)
        style = option.widget.style() if option.widget else QApplication.style()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        depth = self._depth(index)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        direction = (
            option.widget.layoutDirection()
            if option.widget is not None
            else Qt.LayoutDirection.LeftToRight
        )

        # Let the active QSS draw one continuous hover/selection surface, then
        # add hierarchy and text ourselves.
        opt.text = ""
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        rect = QRectF(option.rect)
        text_rect = self.text_rect(rect, depth, direction)
        right_to_left = direction == Qt.LayoutDirection.RightToLeft

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # A quiet vertical rail per ancestor makes heading depth visible while
        # avoiding chevrons that would imply the flat outline is collapsible.
        painter.setPen(QPen(self._guide_color, 1.0))
        for lane in range(depth):
            offset = _TOC_LEFT_PADDING + lane * _TOC_INDENT + _TOC_INDENT / 2
            x = rect.right() - offset if right_to_left else rect.left() + offset
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._theme.accent))
            bar_x = rect.right() - 4 if right_to_left else rect.left() + 1
            painter.drawRoundedRect(
                QRectF(bar_x, rect.top() + 4, 3, max(0.0, rect.height() - 8)),
                1.5,
                1.5,
            )

        font = self._font_for(option.font, depth, enabled)
        metrics = QFontMetrics(font)
        elided = metrics.elidedText(
            title,
            Qt.TextElideMode.ElideRight,
            max(0, int(text_rect.width())),
        )
        if not enabled:
            text_color = self._theme.text_subtle
        elif selected or depth < 2:
            text_color = self._theme.text
        else:
            text_color = self._theme.text_muted
        painter.setFont(font)
        painter.setPen(QColor(text_color))
        alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight
            if right_to_left
            else Qt.AlignmentFlag.AlignLeft
        )
        painter.drawText(text_rect, alignment, elided)
        painter.restore()


class TocView(QWidget):
    def __init__(self, on_anchor_clicked, parent=None):
        super().__init__(parent)
        self._on_anchor_clicked = on_anchor_clicked
        self._anchors: list[str | int] = []
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setObjectName("tocList")
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setWordWrap(False)
        self._list.setSpacing(0)
        self._list.setMouseTracking(True)
        self._list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._delegate = _TocDelegate(LIGHT, self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        self.apply_theme(LIGHT)

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(f"background: {theme.surface};")
        self._delegate.set_theme(theme)
        self._list.setStyleSheet(self._list_stylesheet(theme))

    @staticmethod
    def _list_stylesheet(theme: Theme) -> str:
        return f"""
QListWidget#tocList {{
    background: {theme.surface};
    border: none;
    color: {theme.text};
    outline: 0;
}}
QListWidget#tocList:focus {{
    border: none;
}}
QListWidget#tocList::item {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0;
}}
QListWidget#tocList::item:hover {{
    background: {theme.surface_hover};
}}
QListWidget#tocList::item:selected {{
    background: {theme.surface_active};
    border-color: {theme.accent_soft};
}}
QListWidget#tocList::item:selected:active {{
    border-color: {theme.accent};
}}
QListWidget#tocList::item:disabled {{
    background: transparent;
    color: {theme.text_subtle};
}}
"""

    def _populate(self, entries, empty_text: str) -> None:
        self._list.clear()
        self._anchors = []

        if not entries:
            item = QListWidgetItem(empty_text)
            item.setToolTip(empty_text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            return

        for raw_level, raw_title, target in entries:
            level = _normalise_level(raw_level)
            title = str(raw_title)
            item = QListWidgetItem(title)
            item.setToolTip(title)
            item.setData(_TARGET_ROLE, target)
            item.setData(_LEVEL_ROLE, level)
            item.setData(_DEPTH_ROLE, _visual_depth(level))
            self._list.addItem(item)
            self._anchors.append(target)

    def update_headings(self, headings: list[tuple[int, str, str]]):
        """Populate Markdown headings as ``(level, text, anchor_id)``."""
        self._populate(headings, "目前文件沒有標題")

    def update_outline(self, entries: list[tuple[int, str, int]]):
        """Populate a PDF outline as ``(level, title, zero-based page)``."""
        normalised = [
            (level, title, int(page0)) for level, title, page0 in entries
        ]
        self._populate(normalised, "此 PDF 沒有大綱")

    def set_active_anchor(self, anchor: str | int | None):
        if anchor is None or anchor == "":
            self._list.clearSelection()
            self._list.setCurrentItem(None)
            return
        for i in range(self._list.count()):
            if self._list.item(i).data(_TARGET_ROLE) == anchor:
                self._list.blockSignals(True)
                self._list.setCurrentRow(i)
                self._list.blockSignals(False)
                return
        self._list.clearSelection()
        self._list.setCurrentItem(None)

    def _on_item_clicked(self, item: QListWidgetItem):
        target = item.data(_TARGET_ROLE)
        if target is not None:
            self._on_anchor_clicked(target)
