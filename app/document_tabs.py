"""Readable, scrollable document tabs and the all-tabs picker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from .theme import LIGHT, Theme, svg_icon

MIN_TAB_WIDTH = 132
MAX_TAB_WIDTH = 200
TAB_STRIP_HEIGHT = 36
_TAB_CHROME_WIDTH = 54
_CLOSE_BUTTON_SIZE = 20
_MODE_BADGE_SIZE = QSize(46, 18)
_MODE_BADGE_GAP = 6
_MODE_BADGES = {"markdown": "MD", "office": "Office"}
_ASSETS_DIR = Path(__file__).parent.parent / "assets"


def _parent_parts(path: Path) -> list[str]:
    anchor = path.anchor.casefold()
    return [
        part.rstrip("\\/")
        for part in path.parent.parts
        if part.rstrip("\\/") and part.casefold() != anchor
    ]


def _parent_suffix(path: Path, depth: int) -> str:
    parts = _parent_parts(path)
    if not parts:
        return str(path.parent) or path.anchor or "."
    return "/".join(parts[-max(1, depth):])


def disambiguated_tab_labels(paths) -> list[str]:
    """Return filenames, adding the shortest unique parent only for duplicates."""
    path_objects = [Path(path) for path in paths]
    labels = [path.name or str(path) for path in path_objects]
    groups: dict[str, list[int]] = {}
    for index, path in enumerate(path_objects):
        groups.setdefault(path.name.casefold(), []).append(index)

    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        max_depth = max(len(_parent_parts(path_objects[index])) for index in indexes)
        depth = 1
        while depth < max(1, max_depth):
            suffixes = {
                _parent_suffix(path_objects[index], depth).casefold()
                for index in indexes
            }
            if len(suffixes) == len(indexes):
                break
            depth += 1
        suffixes = {
            _parent_suffix(path_objects[index], depth).casefold()
            for index in indexes
        }
        for index in indexes:
            path = path_objects[index]
            suffix = _parent_suffix(path, depth)
            if len(suffixes) != len(indexes):
                # The directory tail can still collide across drive letters or
                # UNC shares. Include the anchor only in that final fallback.
                suffix = str(path.parent).replace("\\", "/")
            labels[index] = f"{path.name} · {suffix}"
    return labels


class DocumentTabBar(QTabBar):
    """A one-line tab bar that scrolls before filenames become unreadable."""

    tabsChanged = Signal()

    def __init__(self, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self._mode_badges: list[str | None] = []
        self._mode_badge_icons: dict[str, QIcon] = {}
        self.setObjectName("documentTabs")
        self.setAccessibleName("開啟的文件分頁")
        self.setTabsClosable(True)
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setIconSize(_MODE_BADGE_SIZE)
        self.setMouseTracking(True)
        self.setFixedHeight(TAB_STRIP_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._theme = theme
        self._hovered_index = -1

        self.currentChanged.connect(self._sync_close_buttons)
        self.tabMoved.connect(self._on_tab_moved)

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._mode_badge_icons.clear()
        for index, mode in enumerate(self._mode_badges):
            self.setTabIcon(
                index, self._mode_badge_icon(mode) if mode else QIcon()
            )
        self._sync_close_buttons()
        self.update()

    def mode_badge(self, index: int) -> str | None:
        """Return ``markdown``/``office`` for *index*, if it has a mode pill."""
        if index < 0 or index >= len(self._mode_badges):
            return None
        return self._mode_badges[index]

    def set_mode_badge(self, index: int, mode: str | None) -> None:
        """Render a compact, theme-aware Markdown/Office pill in the icon slot."""
        if index < 0 or index >= self.count():
            return
        mode = mode if mode in _MODE_BADGES else None
        if self._mode_badges[index] == mode:
            return
        self._mode_badges[index] = mode
        self.setTabIcon(
            index, self._mode_badge_icon(mode) if mode else QIcon()
        )
        self.updateGeometry()

    def mode_badge_text(self, index: int) -> str:
        """Return the compact text painted in a tab's mode pill."""
        return _MODE_BADGES.get(self.mode_badge(index), "")

    def _mode_badge_icon(self, mode: str) -> QIcon:
        cached = self._mode_badge_icons.get(mode)
        if cached is not None:
            return cached
        office = mode == "office"
        background = self._theme.accent_soft if office else self._theme.surface_alt
        foreground = self._theme.accent if office else self._theme.success
        scale = 2
        pixmap = QPixmap(
            _MODE_BADGE_SIZE.width() * scale,
            _MODE_BADGE_SIZE.height() * scale,
        )
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(background))
        painter.setPen(QPen(QColor(foreground), 1))
        rect = QRectF(
            0.5,
            0.5,
            _MODE_BADGE_SIZE.width() - 1,
            _MODE_BADGE_SIZE.height() - 1,
        )
        painter.drawRoundedRect(rect, 5, 5)
        font = painter.font()
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _MODE_BADGES[mode])
        painter.end()
        icon = QIcon(pixmap)
        self._mode_badge_icons[mode] = icon
        return icon

    def tabSizeHint(self, index: int) -> QSize:  # noqa: N802 (Qt override)
        text_width = self.fontMetrics().horizontalAdvance(self.tabText(index))
        badge_width = (
            _MODE_BADGE_SIZE.width() + _MODE_BADGE_GAP
            if self.mode_badge(index)
            else 0
        )
        width = max(
            MIN_TAB_WIDTH,
            min(MAX_TAB_WIDTH, text_width + _TAB_CHROME_WIDTH + badge_width),
        )
        return QSize(width, TAB_STRIP_HEIGHT)

    def minimumTabSizeHint(self, index: int) -> QSize:  # noqa: N802
        # Qt normally compresses every tab to its much smaller minimum hint
        # before showing scroll buttons. Matching the readable size hint makes
        # the bar overflow and scroll instead of degrading titles to "A…".
        return self.tabSizeHint(index)

    def tabInserted(self, index: int):  # noqa: N802 (Qt override)
        super().tabInserted(index)
        self._mode_badges.insert(index, None)
        self._hovered_index = -1
        self._sync_close_buttons()
        self.tabsChanged.emit()

    def tabRemoved(self, index: int):  # noqa: N802 (Qt override)
        super().tabRemoved(index)
        if 0 <= index < len(self._mode_badges):
            self._mode_badges.pop(index)
        self._hovered_index = -1
        self._sync_close_buttons()
        self.tabsChanged.emit()

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 (Qt override)
        hovered = self.tabAt(event.position().toPoint())
        if hovered != self._hovered_index:
            self._hovered_index = hovered
            self._sync_close_buttons()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt override)
        self._hovered_index = -1
        self._sync_close_buttons()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.tabCloseRequested.emit(index)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def visible_tabs_rect(self) -> QRect:
        """Usable tab geometry, excluding Qt's visible native scroll buttons."""
        left = 0
        right = self.width()
        for button in self.findChildren(QToolButton):
            if (
                button.objectName() != "documentTabCloseButton"
                and button.isVisible()
            ):
                geometry = button.geometry()
                if geometry.center().x() < self.width() / 2:
                    left = max(left, geometry.right() + 1)
                else:
                    right = min(right, geometry.left())
        return QRect(left, 0, max(0, right - left), self.height())

    def has_overflow(self) -> bool:
        return self.visible_tabs_rect().width() < self.width()

    def refresh_close_buttons(self) -> None:
        self._sync_close_buttons()

    def ensure_tab_visible(self, index: int) -> None:
        """Select a tab and make Qt scroll it into view, even if already active."""
        if index < 0 or index >= self.count():
            return
        if index != self.currentIndex():
            self.setCurrentIndex(index)
            return
        if self.count() < 2:
            return

        # QTabBar does not expose ensureTabVisible(), and selecting the current
        # index is a no-op. A signal-blocked round trip through a neighbour
        # updates Qt's private scroll offset without reloading the document.
        neighbour = index - 1 if index > 0 else 1
        signals_were_blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(neighbour)
            self.setCurrentIndex(index)
        finally:
            self.blockSignals(signals_were_blocked)
        self._sync_close_buttons()

    def _on_tab_moved(self, _from: int, _to: int) -> None:
        if (
            0 <= _from < len(self._mode_badges)
            and 0 <= _to < len(self._mode_badges)
        ):
            self._mode_badges.insert(_to, self._mode_badges.pop(_from))
        self._hovered_index = -1
        self.ensure_tab_visible(self.currentIndex())
        self._sync_close_buttons()
        self.tabsChanged.emit()

    def _close_button(self, index: int):
        for side in (
            QTabBar.ButtonPosition.RightSide,
            QTabBar.ButtonPosition.LeftSide,
        ):
            button = self.tabButton(index, side)
            if button is not None and button.objectName() != "documentTabModeBadge":
                button.setObjectName("documentTabCloseButton")
                tab_name = self.accessibleTabName(index) or self.tabText(index)
                button.setAccessibleName(f"關閉 {tab_name}")
                button.setToolTip("關閉分頁")
                button.setFixedSize(_CLOSE_BUTTON_SIZE, _CLOSE_BUTTON_SIZE)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                return button
        return None

    def _sync_close_buttons(self, *_args) -> None:
        current = self.currentIndex()
        for index in range(self.count()):
            button = self._close_button(index)
            if button is not None:
                button.setVisible(index in (current, self._hovered_index))


class DocumentTabStrip(QWidget):
    """Document tab bar plus a searchable all-tabs utility button."""

    def __init__(self, theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self.setObjectName("documentTabStrip")
        self.setFixedHeight(TAB_STRIP_HEIGHT)
        self._theme = theme

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_bar = DocumentTabBar(theme, self)
        self.overflow_button = QToolButton(self)
        self.overflow_button.setObjectName("documentTabsOverflowButton")
        self.overflow_button.setFixedSize(TAB_STRIP_HEIGHT, TAB_STRIP_HEIGHT)
        self.overflow_button.setToolTip("所有開啟的分頁")
        self.overflow_button.setAccessibleName("所有開啟的分頁")
        self.overflow_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overflow_button.clicked.connect(self.show_tabs_menu)

        layout.addWidget(self.tab_bar, stretch=1)
        layout.addWidget(self.overflow_button)

        self.tab_bar.tabsChanged.connect(self.sync_visibility)
        self.tab_bar.currentChanged.connect(self._update_button_state)
        self.apply_theme(theme)
        self.sync_visibility()

    def apply_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.tab_bar.apply_theme(theme)
        self.overflow_button.setIcon(svg_icon("list-tree", theme.text_muted, 17))
        self.overflow_button.setIconSize(QSize(17, 17))
        self.setStyleSheet(self._stylesheet(theme))

    def sync_visibility(self, *_args) -> None:
        count = self.tab_bar.count()
        self.tab_bar.setVisible(count > 0)
        self.overflow_button.setVisible(count > 1)
        self.setVisible(count > 0)
        self._update_button_state()

    def _update_button_state(self, *_args) -> None:
        count = self.tab_bar.count()
        self.overflow_button.setToolTip(f"所有開啟的分頁（{count}）")

    def build_tabs_menu(self) -> QMenu:
        """Build the searchable menu without showing its modal popup."""
        # Keep the popup Python-owned. Parenting each transient menu to the
        # strip would retain every previously opened menu until window close.
        menu = QMenu()
        menu.setObjectName("documentTabsMenu")
        menu.setMinimumWidth(340)
        menu.setStyleSheet(self._stylesheet(self._theme))

        search = QLineEdit(menu)
        search.setObjectName("documentTabsSearch")
        search.setPlaceholderText("搜尋開啟的分頁")
        search.setClearButtonEnabled(True)
        search.setAccessibleName("搜尋開啟的分頁")
        search_action = QWidgetAction(menu)
        search_action.setDefaultWidget(search)
        menu.addAction(search_action)
        menu.addSeparator()

        tab_actions = []
        current = self.tab_bar.currentIndex()
        for index in range(self.tab_bar.count()):
            path = str(self.tab_bar.tabData(index) or "")
            label = self.tab_bar.tabText(index) or Path(path).name or path
            badge_text = self.tab_bar.mode_badge_text(index)
            mode = f"[{badge_text}] " if badge_text else ""
            action = menu.addAction(f"{mode}{label}".replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(index == current)
            action.setData(path)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, selected_path=path: self.select_path(
                    selected_path
                )
            )
            tab_actions.append(action)

        no_results = menu.addAction("找不到符合的分頁")
        no_results.setEnabled(False)
        no_results.setVisible(False)

        def _filter_tabs(query: str) -> None:
            needle = query.strip().casefold()
            shown = 0
            for action in tab_actions:
                haystack = f"{action.text()}\n{action.data()}".casefold()
                visible = not needle or needle in haystack
                action.setVisible(visible)
                shown += int(visible)
            no_results.setVisible(shown == 0)

        search.textChanged.connect(_filter_tabs)
        menu.aboutToShow.connect(search.setFocus)
        return menu

    def show_tabs_menu(self) -> None:
        if self.tab_bar.count() < 1:
            return
        menu = self.build_tabs_menu()
        menu.ensurePolished()
        menu.adjustSize()
        x = self.overflow_button.width() - menu.sizeHint().width()
        try:
            menu.exec(self.overflow_button.mapToGlobal(QPoint(x, self.height())))
        finally:
            menu.deleteLater()

    def select_path(self, path: str) -> None:
        for index in range(self.tab_bar.count()):
            if self.tab_bar.tabData(index) == path:
                self.tab_bar.ensure_tab_visible(index)
                return

    @staticmethod
    def _stylesheet(theme: Theme) -> str:
        close_icon = (_ASSETS_DIR / f"tab-close-{theme.name}.svg").as_posix()
        return f"""
QWidget#documentTabStrip {{
    background: {theme.window};
    border-bottom: 1px solid {theme.border};
}}
QTabBar#documentTabs {{
    background: transparent;
    border: none;
    outline: 0;
}}
QTabBar#documentTabs::tab {{
    background: transparent;
    color: {theme.text_muted};
    border: none;
    border-top: 3px solid transparent;
    border-right: 1px solid {theme.border};
    padding: 0 8px 0 10px;
}}
QTabBar#documentTabs::tab:hover {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QTabBar#documentTabs::tab:selected {{
    background: {theme.surface};
    border-top-color: {theme.accent};
    color: {theme.text};
}}
QTabBar#documentTabs::scroller {{
    width: 52px;
}}
QTabBar#documentTabs::close-button {{
    image: url('{close_icon}');
    width: 14px;
    height: 14px;
}}
QTabBar#documentTabs QToolButton {{
    background: {theme.window};
    border: none;
    border-left: 1px solid {theme.border};
    color: {theme.text_muted};
    width: 25px;
}}
QTabBar#documentTabs QToolButton:hover {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QAbstractButton#documentTabCloseButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px;
}}
QAbstractButton#documentTabCloseButton:hover {{
    background: {theme.surface_hover};
}}
QToolButton#documentTabsOverflowButton {{
    background: {theme.window};
    border: none;
    border-left: 1px solid {theme.border};
    color: {theme.text_muted};
    padding: 0;
}}
QToolButton#documentTabsOverflowButton:hover,
QToolButton#documentTabsOverflowButton:pressed {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QMenu#documentTabsMenu {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    color: {theme.text};
}}
QMenu#documentTabsMenu::item {{
    padding: 7px 22px 7px 12px;
}}
QMenu#documentTabsMenu::item:selected {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QLineEdit#documentTabsSearch {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 30px;
    margin: 6px 8px;
    padding: 2px 8px;
}}
QLineEdit#documentTabsSearch:focus {{
    border-color: {theme.accent};
}}
"""
