"""Horizontal Markdown formatting toolbar shown above the editor.

Commands are real ``QAction`` objects rather than custom widget actions, so
Qt's overflow menu remains fully operable in a narrow split editor.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolBar, QToolButton

from .format_commands import commands_for
from .theme import Theme


class FormatToolbar(QToolBar):
    """Row of text-labelled QToolButtons emitting format action ids."""

    action_triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("formatToolbar")
        self.setMovable(False)
        self.setFloatable(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

        self._buttons: dict[str, QToolButton] = {}
        self._actions: dict[str, QAction] = {}
        previous_group = ""
        for command in commands_for("toolbar"):
            if previous_group and command.group != previous_group:
                self.addSeparator()
            previous_group = command.group
            action = QAction(command.title, self)
            action.setToolTip(command.tooltip)
            action.setStatusTip(command.description)
            action.setData(command.action_id)
            action.setProperty("formatAction", command.action_id)
            action.triggered.connect(
                lambda _checked=False, aid=command.action_id: (
                    self.action_triggered.emit(aid)
                )
            )
            self.addAction(action)
            button = self.widgetForAction(action)
            if not isinstance(button, QToolButton):
                continue
            button.setText(command.toolbar_label)
            button.setToolTip(command.tooltip)
            button.setAccessibleName(command.tooltip)
            button.setProperty("formatAction", command.action_id)
            button.setProperty("formatActive", False)
            # Keep keyboard focus in the editor while clicking buttons.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            font = button.font()
            if command.action_id == "bold":
                font.setBold(True)
            elif command.action_id == "italic":
                font.setItalic(True)
            elif command.action_id == "strikethrough":
                font.setStrikeOut(True)
            button.setFont(font)
            self._buttons[command.action_id] = button
            self._actions[command.action_id] = action

        # Qt's native overflow chevron is too low-contrast in dark mode and
        # carries most commands in split view.  A text ellipsis remains clear
        # across platform styles and communicates "more" without an icon file.
        self._extension_button = self.findChild(
            QToolButton, "qt_toolbar_ext_button"
        )
        if self._extension_button is not None:
            self._extension_button.setArrowType(Qt.ArrowType.NoArrow)
            self._extension_button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextOnly
            )
            self._extension_button.setText("⋯")
            # The Windows style reserves a 21 px extension slot internally.
            # Staying within it keeps the complete hit target visible instead
            # of clipping the right edge in a narrow split editor.
            self._extension_button.setFixedWidth(18)
            self._extension_button.setToolTip("更多格式工具")
            self._extension_button.setAccessibleName("更多格式工具")

    def button(self, action_id: str) -> QToolButton:
        return self._buttons[action_id]

    def action_ids(self) -> list[str]:
        return list(self._buttons)

    def set_active_actions(self, action_ids) -> None:
        active = set(action_ids)
        for action_id, button in self._buttons.items():
            value = action_id in active
            if button.property("formatActive") == value:
                continue
            button.setProperty("formatActive", value)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def command_action(self, action_id: str) -> QAction:
        return self._actions[action_id]

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"""
QToolBar#formatToolbar {{
    background: {theme.window};
    border: none;
    border-bottom: 1px solid {theme.border};
    padding: 4px 10px;
    spacing: 2px;
}}
QToolBar#formatToolbar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text_muted};
    min-width: 28px;
    min-height: 26px;
    padding: 0 6px;
}}
QToolBar#formatToolbar QToolButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
    color: {theme.text};
}}
QToolBar#formatToolbar QToolButton:pressed {{
    background: {theme.accent_soft};
    color: {theme.text};
}}
QToolBar#formatToolbar QToolButton[formatActive="true"] {{
    background: {theme.accent_soft};
    border-color: {theme.accent};
    color: {theme.accent};
}}
QToolBar#formatToolbar QToolButton#qt_toolbar_ext_button {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    color: {theme.text};
    min-width: 18px;
    max-width: 18px;
    padding: 0;
    font-size: 18px;
    font-weight: 700;
}}
QToolBar#formatToolbar QToolButton#qt_toolbar_ext_button:hover {{
    background: {theme.surface_hover};
    border-color: {theme.accent};
    color: {theme.accent};
}}
QToolBar#formatToolbar::separator {{
    background: {theme.border};
    width: 1px;
    margin: 4px 4px;
}}
"""
        )
