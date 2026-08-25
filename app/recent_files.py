"""Recent files panel."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from .file_types import is_pdf
from .theme import LIGHT, Theme, svg_icon

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
_PATHS_KEY = "recent_files"
_OPENED_AT_KEY = "recent_file_opened_at"
_MAX = 10

_PATH_ROLE = Qt.ItemDataRole.UserRole
_KIND_ROLE = Qt.ItemDataRole.UserRole.value + 1
_PARENT_ROLE = Qt.ItemDataRole.UserRole.value + 2
_META_ROLE = Qt.ItemDataRole.UserRole.value + 3
_OPENED_AT_ROLE = Qt.ItemDataRole.UserRole.value + 4
_MISSING_ROLE = Qt.ItemDataRole.UserRole.value + 5

_KIND_FILE = "file"
_KIND_HEADER = "header"
_KIND_EMPTY = "empty"

_FILE_ROW_HEIGHT = 54
_HEADER_ROW_HEIGHT = 30
_EMPTY_ROW_HEIGHT = 48
_ROW_LEFT_PADDING = 12
_ROW_RIGHT_PADDING = 10
_ICON_SIZE = 16
_ICON_TEXT_GAP = 8
_META_GAP = 8


def _local_datetime(value: datetime) -> datetime:
    """Return an aware local datetime, accepting a naive test clock too."""
    if value.tzinfo is None:
        return value.astimezone()
    return value.astimezone()


def _timestamp_ms(value: datetime) -> int:
    return int(_local_datetime(value).timestamp() * 1000)


def _datetime_for(timestamp_ms: int | None, now: datetime) -> datetime | None:
    if timestamp_ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=now.tzinfo)
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _time_group(timestamp_ms: int | None, now: datetime) -> str:
    opened = _datetime_for(timestamp_ms, now)
    if opened is None:
        return "earlier"
    day_delta = (now.date() - opened.date()).days
    if day_delta <= 0:
        return "today"
    if day_delta == 1:
        return "yesterday"
    return "earlier"


def _relative_time(timestamp_ms: int | None, now: datetime) -> str:
    opened = _datetime_for(timestamp_ms, now)
    if opened is None:
        return "較早開啟"

    day_delta = (now.date() - opened.date()).days
    if day_delta <= 0:
        seconds = max(0, int((now - opened).total_seconds()))
        if seconds < 60:
            return "剛剛"
        if seconds < 3600:
            return f"{seconds // 60} 分鐘前"
        return f"{seconds // 3600} 小時前"
    if day_delta == 1:
        return "昨天"
    if day_delta < 7:
        return f"{day_delta} 天前"
    return f"{opened.year}/{opened.month}/{opened.day}"


def _exact_time(timestamp_ms: int | None, now: datetime) -> str:
    opened = _datetime_for(timestamp_ms, now)
    if opened is None:
        return "較早開啟（未記錄時間）"
    return f"{opened.year}/{opened.month}/{opened.day} {opened:%H:%M}"


class _RecentFileDelegate(QStyledItemDelegate):
    """Paint section headers and compact two-line recent-file rows."""

    def __init__(self, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._markdown_icon = QIcon()
        self._pdf_icon = QIcon()
        self._missing_icon = QIcon()
        self._empty_icon = QIcon()
        self.set_theme(theme)

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._markdown_icon = svg_icon("file-text", theme.text_muted, _ICON_SIZE)
        self._pdf_icon = svg_icon("file-text", theme.danger, _ICON_SIZE)
        self._missing_icon = svg_icon("alert", theme.text_subtle, _ICON_SIZE)
        self._empty_icon = svg_icon("history", theme.text_subtle, _ICON_SIZE)
        parent = self.parent()
        if parent is not None and hasattr(parent, "viewport"):
            parent.viewport().update()

    @staticmethod
    def _smaller_font(base: QFont, weight=QFont.Weight.Normal) -> QFont:
        font = QFont(base)
        font.setWeight(weight)
        if font.pointSize() > 0:
            font.setPointSize(max(7, font.pointSize() - 1))
        elif font.pixelSize() > 0:
            font.setPixelSize(max(9, font.pixelSize() - 2))
        return font

    def sizeHint(self, option, index):  # noqa: N802 (Qt override)
        kind = index.data(_KIND_ROLE)
        base = super().sizeHint(option, index)
        if kind == _KIND_HEADER:
            height = _HEADER_ROW_HEIGHT
        elif kind == _KIND_EMPTY:
            height = _EMPTY_ROW_HEIGHT
        else:
            height = _FILE_ROW_HEIGHT
        return QSize(base.width(), height)

    def paint(self, painter, option, index):  # noqa: N802 (Qt override)
        style = option.widget.style() if option.widget else QApplication.style()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        kind = index.data(_KIND_ROLE)
        title = opt.text

        # Let the scoped QSS paint hover, selection, focus, and rounded surfaces.
        opt.text = ""
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        rect = QRectF(option.rect)
        right_to_left = (
            option.widget is not None
            and option.widget.layoutDirection() == Qt.LayoutDirection.RightToLeft
        )
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        if kind == _KIND_HEADER:
            self._paint_header(painter, rect, title, option.font, right_to_left)
        elif kind == _KIND_EMPTY:
            self._paint_empty(painter, rect, title, option.font, right_to_left)
        else:
            self._paint_file(painter, option, index, rect, title, right_to_left)

        painter.restore()

    def _paint_header(self, painter, rect, title, base_font, right_to_left):
        font = self._smaller_font(base_font, QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._theme.text_muted))
        text_rect = rect.adjusted(
            _ROW_LEFT_PADDING, 3, -_ROW_RIGHT_PADDING, 0
        )
        alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight
            if right_to_left
            else Qt.AlignmentFlag.AlignLeft
        )
        painter.drawText(text_rect, alignment, title)

    def _paint_empty(self, painter, rect, title, base_font, right_to_left):
        icon_x = (
            rect.right() - _ROW_LEFT_PADDING - _ICON_SIZE
            if right_to_left
            else rect.left() + _ROW_LEFT_PADDING
        )
        icon_rect = QRectF(
            icon_x,
            rect.top() + (rect.height() - _ICON_SIZE) / 2,
            _ICON_SIZE,
            _ICON_SIZE,
        ).toRect()
        self._empty_icon.paint(painter, icon_rect)

        text_left = (
            rect.left() + _ROW_RIGHT_PADDING
            if right_to_left
            else icon_rect.right() + 1 + _ICON_TEXT_GAP
        )
        text_right = (
            icon_rect.left() - _ICON_TEXT_GAP
            if right_to_left
            else rect.right() - _ROW_RIGHT_PADDING
        )
        text_rect = QRectF(
            text_left,
            rect.top(),
            max(0.0, text_right - text_left),
            rect.height(),
        )
        painter.setFont(base_font)
        painter.setPen(QColor(self._theme.text_subtle))
        alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight
            if right_to_left
            else Qt.AlignmentFlag.AlignLeft
        )
        painter.drawText(text_rect, alignment, title)

    def _paint_file(self, painter, option, index, rect, title, right_to_left):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        missing = bool(index.data(_MISSING_ROLE))

        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._theme.accent))
            bar_x = rect.right() - 4 if right_to_left else rect.left() + 1
            painter.drawRoundedRect(
                QRectF(bar_x, rect.top() + 6, 3, max(0.0, rect.height() - 12)),
                1.5,
                1.5,
            )

        icon_x = (
            rect.right() - _ROW_LEFT_PADDING - _ICON_SIZE
            if right_to_left
            else rect.left() + _ROW_LEFT_PADDING
        )
        icon_rect = QRectF(
            icon_x,
            rect.top() + 10,
            _ICON_SIZE,
            _ICON_SIZE,
        ).toRect()
        path = str(index.data(_PATH_ROLE) or "")
        if missing:
            icon = self._missing_icon
        elif is_pdf(path):
            icon = self._pdf_icon
        else:
            icon = self._markdown_icon
        icon.paint(painter, icon_rect)

        text_left = (
            rect.left() + _ROW_RIGHT_PADDING
            if right_to_left
            else icon_rect.right() + 1 + _ICON_TEXT_GAP
        )
        text_right = (
            icon_rect.left() - _ICON_TEXT_GAP
            if right_to_left
            else rect.right() - _ROW_RIGHT_PADDING
        )
        width = max(0.0, text_right - text_left)

        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.Medium)
        title_metrics = QFontMetrics(title_font)
        title_rect = QRectF(text_left, rect.top() + 6, width, 20)
        title_elided = title_metrics.elidedText(
            title, Qt.TextElideMode.ElideRight, max(0, int(width))
        )
        painter.setFont(title_font)
        painter.setPen(
            QColor(self._theme.text_muted if missing else self._theme.text)
        )
        title_alignment = Qt.AlignmentFlag.AlignVCenter | (
            Qt.AlignmentFlag.AlignRight
            if right_to_left
            else Qt.AlignmentFlag.AlignLeft
        )
        painter.drawText(title_rect, title_alignment, title_elided)

        detail_font = self._smaller_font(option.font)
        detail_metrics = QFontMetrics(detail_font)
        detail_rect = QRectF(text_left, rect.top() + 28, width, 18)
        meta = str(index.data(_META_ROLE) or "")
        parent = str(index.data(_PARENT_ROLE) or "")
        meta_width = min(
            detail_rect.width(), float(detail_metrics.horizontalAdvance(meta))
        )
        parent_width = max(0.0, detail_rect.width() - meta_width - _META_GAP)

        if right_to_left:
            meta_rect = QRectF(
                detail_rect.left(), detail_rect.top(), meta_width, detail_rect.height()
            )
            parent_rect = QRectF(
                meta_rect.right() + _META_GAP,
                detail_rect.top(),
                parent_width,
                detail_rect.height(),
            )
            meta_alignment = Qt.AlignmentFlag.AlignLeft
            parent_alignment = Qt.AlignmentFlag.AlignRight
        else:
            parent_rect = QRectF(
                detail_rect.left(), detail_rect.top(), parent_width, detail_rect.height()
            )
            meta_rect = QRectF(
                parent_rect.right() + _META_GAP,
                detail_rect.top(),
                meta_width,
                detail_rect.height(),
            )
            meta_alignment = Qt.AlignmentFlag.AlignRight
            parent_alignment = Qt.AlignmentFlag.AlignLeft

        parent_elided = detail_metrics.elidedText(
            parent, Qt.TextElideMode.ElideMiddle, max(0, int(parent_rect.width()))
        )
        painter.setFont(detail_font)
        painter.setPen(QColor(self._theme.text_subtle))
        painter.drawText(
            parent_rect,
            Qt.AlignmentFlag.AlignVCenter | parent_alignment,
            parent_elided,
        )
        painter.setPen(
            QColor(self._theme.danger if missing else self._theme.text_muted)
        )
        painter.drawText(
            meta_rect,
            Qt.AlignmentFlag.AlignVCenter | meta_alignment,
            meta,
        )


class RecentFilesView(QListWidget):
    def __init__(
        self,
        on_file_selected,
        tag_index=None,
        parent=None,
        clock: Callable[[], datetime] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("recentFilesList")
        self._on_file_selected = on_file_selected
        self._tag_index = tag_index
        self._active_tag = ""
        self._theme = LIGHT
        self._clock = clock or (lambda: datetime.now().astimezone())

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setWordWrap(False)
        self.setSpacing(0)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._delegate = _RecentFileDelegate(LIGHT, self)
        self.setItemDelegate(self._delegate)

        self.itemClicked.connect(self._on_clicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Keep relative labels and date groups accurate while the app stays open.
        self._relative_timer = QTimer(self)
        self._relative_timer.setInterval(60_000)
        self._relative_timer.timeout.connect(self._refresh)
        self._relative_timer.start()

        self.apply_theme(LIGHT)
        self._refresh()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._delegate.set_theme(theme)
        self.setStyleSheet(self._stylesheet(theme))

    @staticmethod
    def _stylesheet(theme: Theme) -> str:
        return f"""
QListWidget#recentFilesList {{
    background: {theme.surface};
    border: none;
    color: {theme.text};
    outline: 0;
}}
QListWidget#recentFilesList:focus {{
    border: none;
}}
QListWidget#recentFilesList::item {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 0;
}}
QListWidget#recentFilesList::item:hover {{
    background: {theme.surface_hover};
}}
QListWidget#recentFilesList::item:selected {{
    background: {theme.surface_active};
    border-color: {theme.accent_soft};
}}
QListWidget#recentFilesList::item:selected:active {{
    border-color: {theme.accent};
}}
QListWidget#recentFilesList::item:disabled {{
    background: transparent;
    border-color: transparent;
    color: {theme.text_subtle};
}}
"""

    def add(self, filepath: str, opened_at: datetime | None = None):
        paths = self._load()
        times = self._load_times()
        fp = str(Path(filepath).resolve())
        if fp in paths:
            paths.remove(fp)
        paths.insert(0, fp)
        paths = paths[:_MAX]
        times[fp] = _timestamp_ms(opened_at or self._now())
        self._save(paths)
        self._save_times({path: times[path] for path in paths if path in times})
        self._refresh(select_path=fp)

    def clear_all(self):
        self._save([])
        self._save_times({})
        self._refresh()

    def migrate_paths(self, mapping: dict):
        """Re-point entries and their timestamps after files move on disk."""
        resolved = {
            str(Path(old).resolve()): str(Path(new).resolve())
            for old, new in mapping.items()
        }
        paths = self._load()
        times = self._load_times()
        updated = [resolved.get(path, path) for path in paths]
        if updated == paths:
            return

        deduped = []
        updated_times: dict[str, int] = {}
        for old_path, new_path in zip(paths, updated):
            if new_path not in deduped:
                deduped.append(new_path)
            timestamp = times.get(old_path, times.get(new_path))
            if timestamp is not None and new_path not in updated_times:
                updated_times[new_path] = timestamp
        self._save(deduped)
        self._save_times(updated_times)
        self._refresh()

    def remove_paths(self, targets):
        keys = {str(Path(path).resolve()) for path in targets}
        paths = self._load()
        remaining = [path for path in paths if path not in keys]
        if remaining != paths:
            self._save(remaining)
            times = self._load_times()
            self._save_times(
                {path: times[path] for path in remaining if path in times}
            )
        self._refresh()

    def _now(self) -> datetime:
        return _local_datetime(self._clock())

    def _refresh(self, select_path: str | None = None):
        if select_path is None:
            current = self.currentItem()
            if current is not None:
                select_path = current.data(_PATH_ROLE)

        self.clear()
        now = self._now()
        times = self._load_times()
        allowed = None
        if self._active_tag and self._tag_index is not None:
            allowed = {
                str(Path(path).resolve())
                for path in self._tag_index.files_with_tag(self._active_tag)
            }

        grouped: dict[str, list[tuple[str, int | None]]] = {
            "today": [],
            "yesterday": [],
            "earlier": [],
        }
        for raw_path in self._load():
            path = str(Path(raw_path))
            if allowed is not None and str(Path(path).resolve()) not in allowed:
                continue
            timestamp = times.get(path)
            grouped[_time_group(timestamp, now)].append((path, timestamp))

        group_labels = (
            ("today", "今天"),
            ("yesterday", "昨天"),
            ("earlier", "更早"),
        )
        selected_item = None
        has_items = False
        for group_key, label in group_labels:
            records = grouped[group_key]
            if not records:
                continue
            self._add_header(label)
            for path, timestamp in records:
                item = self._add_file_item(path, timestamp, now)
                if path == select_path:
                    selected_item = item
                has_items = True

        if not has_items:
            msg = "沒有符合標籤的檔案" if self._active_tag else "尚無最近開啟的檔案"
            item = QListWidgetItem(msg)
            item.setData(_KIND_ROLE, _KIND_EMPTY)
            item.setToolTip(msg)
            item.setFlags(
                item.flags()
                & ~Qt.ItemFlag.ItemIsEnabled
                & ~Qt.ItemFlag.ItemIsSelectable
            )
            self.addItem(item)
        elif selected_item is not None:
            self.setCurrentItem(selected_item)

    def _add_header(self, label: str) -> None:
        item = QListWidgetItem(label)
        item.setData(_KIND_ROLE, _KIND_HEADER)
        item.setFlags(
            item.flags()
            & ~Qt.ItemFlag.ItemIsEnabled
            & ~Qt.ItemFlag.ItemIsSelectable
        )
        self.addItem(item)

    def _add_file_item(
        self, path_str: str, timestamp: int | None, now: datetime
    ) -> QListWidgetItem:
        path = Path(path_str)
        missing = not path.exists()
        meta = "位置不存在" if missing else _relative_time(timestamp, now)
        exact = _exact_time(timestamp, now)
        tooltip = f"{path_str}\n最後開啟：{exact}"
        if missing:
            tooltip += "\n位置不存在"

        item = QListWidgetItem(path.name or path_str)
        item.setToolTip(tooltip)
        item.setData(_PATH_ROLE, path_str)
        item.setData(_KIND_ROLE, _KIND_FILE)
        item.setData(_PARENT_ROLE, str(path.parent))
        item.setData(_META_ROLE, meta)
        item.setData(_OPENED_AT_ROLE, timestamp)
        item.setData(_MISSING_ROLE, missing)
        item.setData(
            Qt.ItemDataRole.AccessibleTextRole,
            f"{item.text()}，{path.parent}，{meta}",
        )
        self.addItem(item)
        return item

    def set_tag_filter(self, tag: str):
        self._active_tag = tag or ""
        self._refresh()

    def paths(self) -> list[str]:
        """Existing recent file paths, most-recent first."""
        return [path for path in self._load() if Path(path).exists()]

    def _show_context_menu(self, pos: QPoint):
        menu = self._build_context_menu(self.itemAt(pos))
        if menu.actions():
            menu.exec(self.viewport().mapToGlobal(pos))

    def _build_context_menu(self, item: QListWidgetItem | None) -> QMenu:
        """Build a row or empty-area menu without opening its modal popup."""
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        is_file = item is not None and item.data(_KIND_ROLE) == _KIND_FILE
        if is_file:
            path = str(item.data(_PATH_ROLE) or "")
            exists = bool(path and Path(path).exists())

            open_act = menu.addAction("開啟文件")
            open_act.setEnabled(exists)
            open_act.triggered.connect(
                lambda _=False, selected_path=path: self._open_path(selected_path)
            )

            reveal_act = menu.addAction("在檔案總管中顯示")
            reveal_act.setEnabled(exists)
            reveal_act.triggered.connect(
                lambda _=False, selected_path=path: self._open_location_path(
                    selected_path
                )
            )

            menu.addSeparator()
            remove_act = menu.addAction("從最近清單移除")
            remove_act.triggered.connect(
                lambda _=False, selected_path=path: self._remove_path(selected_path)
            )

        if self._load():
            if is_file:
                menu.addSeparator()
            clear_act = menu.addAction("清除最近清單")
            clear_act.triggered.connect(self.clear_all)

        return menu

    def _menu_stylesheet(self) -> str:
        theme = self._theme
        return f"""
QMenu {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 4px;
    color: {theme.text};
}}
QMenu::item {{
    padding: 6px 20px;
    color: {theme.text};
}}
QMenu::item:selected {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QMenu::item:disabled {{
    color: {theme.text_subtle};
}}
QMenu::separator {{
    height: 1px;
    background: {theme.border};
    margin: 4px 8px;
}}
"""

    @staticmethod
    def _open_location_path(path: str | None):
        if path and Path(path).exists():
            subprocess.run(["explorer", "/select,", path])

    def _remove_path(self, path: str | None):
        if path:
            self.remove_paths([path])

    def _open_path(self, path: str | None):
        if path and Path(path).exists():
            self._on_file_selected(path)

    def _on_clicked(self, item: QListWidgetItem):
        if item.data(_KIND_ROLE) != _KIND_FILE:
            return
        self._open_path(item.data(_PATH_ROLE))

    @staticmethod
    def _load() -> list[str]:
        raw = QSettings(_ORG, _APP).value(_PATHS_KEY, []) or []
        if isinstance(raw, str):
            return [raw]
        try:
            return [str(path) for path in raw if path]
        except TypeError:
            return []

    @staticmethod
    def _save(paths: list[str]):
        QSettings(_ORG, _APP).setValue(_PATHS_KEY, paths)

    @staticmethod
    def _load_times() -> dict[str, int]:
        raw = QSettings(_ORG, _APP).value(_OPENED_AT_KEY, "") or ""
        if isinstance(raw, dict):
            payload = raw
        else:
            try:
                payload = json.loads(str(raw))
            except (TypeError, ValueError):
                return {}
        if not isinstance(payload, dict):
            return {}

        times: dict[str, int] = {}
        for path, timestamp in payload.items():
            try:
                times[str(path)] = int(timestamp)
            except (TypeError, ValueError):
                continue
        return times

    @staticmethod
    def _save_times(times: dict[str, int]):
        payload = json.dumps(times, ensure_ascii=False, separators=(",", ":"))
        QSettings(_ORG, _APP).setValue(_OPENED_AT_KEY, payload)
