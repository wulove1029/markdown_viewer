"""Compact global toolbar controls for appearance and application updates."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from .theme import LIGHT, Theme, svg_icon

UTILITY_GROUP_WIDTH = 85
UTILITY_GROUP_HEIGHT = 38
UTILITY_BUTTON_WIDTH = 40
UTILITY_BUTTON_HEIGHT = 34
UTILITY_ICON_SIZE = 18

UPDATE_IDLE = "idle"
UPDATE_CHECKING = "checking"
UPDATE_AVAILABLE = "available"
UPDATE_ERROR = "error"
UPDATE_DOWNLOADING = "downloading"
_UPDATE_STATES = {
    UPDATE_IDLE,
    UPDATE_CHECKING,
    UPDATE_AVAILABLE,
    UPDATE_ERROR,
    UPDATE_DOWNLOADING,
}


def _stateful_icon(
    name: str,
    theme: Theme,
    *,
    normal_color: str | None = None,
    disabled_color: str | None = None,
) -> QIcon:
    """Build an icon whose hover, pressed, and disabled modes match the theme."""
    size = QSize(UTILITY_ICON_SIZE, UTILITY_ICON_SIZE)
    icon = QIcon()
    icon.addPixmap(
        svg_icon(name, normal_color or theme.text_muted, UTILITY_ICON_SIZE).pixmap(size),
        QIcon.Mode.Normal,
    )
    icon.addPixmap(
        svg_icon(name, theme.text, UTILITY_ICON_SIZE).pixmap(size),
        QIcon.Mode.Active,
    )
    icon.addPixmap(
        svg_icon(name, theme.accent, UTILITY_ICON_SIZE).pixmap(size),
        QIcon.Mode.Selected,
    )
    icon.addPixmap(
        svg_icon(
            name,
            disabled_color or theme.text_subtle,
            UTILITY_ICON_SIZE,
        ).pixmap(size),
        QIcon.Mode.Disabled,
    )
    return icon


class _UtilityButton(QPushButton):
    """Icon button with an optional, theme-aware availability dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = LIGHT
        self._badge_visible = False
        self.setFixedSize(UTILITY_BUTTON_WIDTH, UTILITY_BUTTON_HEIGHT)
        self.setIconSize(QSize(UTILITY_ICON_SIZE, UTILITY_ICON_SIZE))
        self.setText("")
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_badge(self, visible: bool, theme: Theme) -> None:
        self._badge_visible = bool(visible)
        self._theme = theme
        self.setProperty("badgeVisible", self._badge_visible)
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        super().paintEvent(event)
        if not self._badge_visible:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self._theme.window), 1.5))
        painter.setBrush(QColor(self._theme.accent))
        painter.drawEllipse(QRectF(self.width() - 10.5, 4.5, 7.0, 7.0))
        painter.end()


class ToolbarUtilities(QFrame):
    """A quiet segmented capsule for theme switching and update status."""

    def __init__(
        self,
        theme: Theme = LIGHT,
        *,
        theme_name: str = "light",
        current_version: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("toolbarUtilities")
        self.setAccessibleName("外觀與應用程式更新")
        self.setFixedSize(UTILITY_GROUP_WIDTH, UTILITY_GROUP_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._theme = theme
        self._theme_name = "dark" if theme_name == "dark" else "light"
        self._current_version = current_version
        self._update_state = UPDATE_IDLE
        self._available_version = ""

        layout = QHBoxLayout(self)
        # The frame consumes one pixel on every edge. One additional layout
        # pixel leaves an exact 81x34 content area for 40 + 1 + 40 controls.
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.theme_button = _UtilityButton(self)
        self.theme_button.setObjectName("themeToggleButton")

        divider = QFrame(self)
        divider.setObjectName("toolbarUtilityDivider")
        divider.setFixedSize(1, 20)
        divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.update_button = _UtilityButton(self)
        self.update_button.setObjectName("updateButton")

        layout.addWidget(self.theme_button)
        layout.addWidget(divider)
        layout.addWidget(self.update_button)
        self.setTabOrder(self.theme_button, self.update_button)

        self.apply_theme(theme, theme_name=self._theme_name)

    @property
    def update_state(self) -> str:
        return self._update_state

    def apply_theme(self, theme: Theme, *, theme_name: str | None = None) -> None:
        self._theme = theme
        if theme_name is not None:
            self._theme_name = "dark" if theme_name == "dark" else "light"
        self.setStyleSheet(self._stylesheet(theme))
        self._refresh_theme_button()
        self._refresh_update_button()

    def set_theme_name(self, theme_name: str) -> None:
        self._theme_name = "dark" if theme_name == "dark" else "light"
        self._refresh_theme_button()

    def set_update_state(self, state: str, *, version: str = "") -> None:
        if state not in _UPDATE_STATES:
            raise ValueError(f"Unknown update state: {state}")
        self._update_state = state
        self._available_version = version if state == UPDATE_AVAILABLE else ""
        self._refresh_update_button()

        # Qt does not consistently re-evaluate dynamic-property selectors.
        style = self.update_button.style()
        style.unpolish(self.update_button)
        style.polish(self.update_button)
        self.update_button.update()

    def _refresh_theme_button(self) -> None:
        dark = self._theme_name == "dark"
        icon_name = "sun" if dark else "moon"
        tooltip = "切換為淺色模式" if dark else "切換為深色模式"
        self.theme_button.setProperty("iconName", icon_name)
        self.theme_button.setToolTip(tooltip)
        self.theme_button.setStatusTip(tooltip)
        self.theme_button.setAccessibleName(tooltip)
        self.theme_button.setIcon(_stateful_icon(icon_name, self._theme))

    def _refresh_update_button(self) -> None:
        state = self._update_state
        if state == UPDATE_CHECKING:
            icon_name = "refresh"
        elif state == UPDATE_DOWNLOADING:
            icon_name = "file-down"
        else:
            icon_name = "circle-arrow-up"
        busy = state in (UPDATE_CHECKING, UPDATE_DOWNLOADING)
        available = state == UPDATE_AVAILABLE

        if state == UPDATE_CHECKING:
            tooltip = "正在檢查更新…"
        elif state == UPDATE_DOWNLOADING:
            tooltip = "正在下載更新…"
        elif available:
            tooltip = (
                f"新版本 v{self._available_version} 可用 · 按一下查看"
                if self._available_version
                else "有新版本可用 · 按一下查看"
            )
        elif state == UPDATE_ERROR:
            tooltip = "上次檢查失敗 · 按一下重試"
        else:
            version = f" v{self._current_version}" if self._current_version else ""
            tooltip = f"檢查更新 · 目前版本{version}"

        self.update_button.setProperty("iconName", icon_name)
        self.update_button.setProperty("updateState", state)
        self.update_button.setToolTip(tooltip)
        self.update_button.setStatusTip(tooltip)
        self.update_button.setAccessibleName(tooltip)
        self.update_button.setEnabled(not busy)
        self.update_button.setCursor(
            Qt.CursorShape.ArrowCursor
            if busy
            else Qt.CursorShape.PointingHandCursor
        )
        self.update_button.setIcon(
            _stateful_icon(
                icon_name,
                self._theme,
                normal_color=(self._theme.accent if busy else None),
                disabled_color=(self._theme.accent if busy else None),
            )
        )
        self.update_button.set_badge(available, self._theme)

    @staticmethod
    def _stylesheet(theme: Theme) -> str:
        return f"""
QFrame#toolbarUtilities {{
    background: {theme.window};
    border: 1px solid {theme.border};
    border-radius: 8px;
}}
QFrame#toolbarUtilityDivider {{
    background: {theme.border};
    border: none;
}}
QFrame#toolbarUtilities QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    min-width: {UTILITY_BUTTON_WIDTH - 2}px;
    max-width: {UTILITY_BUTTON_WIDTH - 2}px;
    min-height: {UTILITY_BUTTON_HEIGHT - 2}px;
    max-height: {UTILITY_BUTTON_HEIGHT - 2}px;
    padding: 0;
}}
QFrame#toolbarUtilities QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
}}
QFrame#toolbarUtilities QPushButton:focus {{
    border-color: {theme.accent};
}}
QFrame#toolbarUtilities QPushButton:pressed {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
QFrame#toolbarUtilities QPushButton[updateState="checking"] {{
    background: {theme.accent_soft};
    border-color: transparent;
}}
QFrame#toolbarUtilities QPushButton[updateState="downloading"] {{
    background: {theme.accent_soft};
    border-color: transparent;
}}
"""
