"""Viewport-local overlays used by the Markdown editor."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QVBoxLayout,
)

from .format_commands import FormatCommandSpec, commands_for
from .theme import Theme


class SlashCommandPopup(QFrame):
    """Keyboard-driven command list anchored beside the text caret."""

    command_activated = Signal(str)
    MAX_VISIBLE_ROWS = 7
    ROW_HEIGHT = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("slashCommandPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        header.setContentsMargins(4, 0, 4, 0)
        self._title = QLabel("快速插入")
        self._title.setObjectName("slashCommandTitle")
        self._hint = QLabel("↑↓ 選擇　Enter 插入　Esc 關閉")
        self._hint.setObjectName("slashCommandHint")
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._hint)
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setObjectName("slashCommandList")
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._list.setVerticalScrollMode(
            QListWidget.ScrollMode.ScrollPerPixel
        )
        self._list.itemClicked.connect(self._item_clicked)
        layout.addWidget(self._list)

        self._empty = QLabel("找不到符合的命令")
        self._empty.setObjectName("slashCommandEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        layout.addWidget(self._empty)
        self.hide()

    def set_commands(
        self, commands: tuple[FormatCommandSpec, ...], query: str
    ) -> None:
        current_action = self.current_action()
        self._title.setText(f"快速插入　/{query}" if query else "快速插入")
        self._list.clear()
        selected_row = 0
        for index, command in enumerate(commands):
            item = QListWidgetItem(
                f"{command.title}　—　{command.description}"
            )
            item.setData(Qt.ItemDataRole.UserRole, command.action_id)
            item.setToolTip(command.tooltip)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, command.tooltip)
            item.setSizeHint(QSize(0, self.ROW_HEIGHT))
            self._list.addItem(item)
            if command.action_id == current_action:
                selected_row = index
        has_commands = bool(commands)
        self._list.setVisible(has_commands)
        self._empty.setVisible(not has_commands)
        if has_commands:
            self._list.setCurrentRow(selected_row)

        rows = min(max(len(commands), 1), self.MAX_VISIBLE_ROWS)
        list_height = rows * self.ROW_HEIGHT + 2
        self._list.setFixedHeight(list_height)
        self._empty.setFixedHeight(54)
        self.adjustSize()

    def current_action(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def move_selection(self, delta: int) -> None:
        count = self._list.count()
        if count == 0:
            return
        row = self._list.currentRow()
        self._list.setCurrentRow((row + delta) % count)
        self._list.scrollToItem(self._list.currentItem())

    def activate_current(self) -> bool:
        action = self.current_action()
        if action is None:
            return False
        self.command_activated.emit(action)
        return True

    def _item_clicked(self, item: QListWidgetItem) -> None:
        action = item.data(Qt.ItemDataRole.UserRole)
        if action:
            self.command_activated.emit(action)

    def show_near(self, caret_rect) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(390, max(280, parent.width() - 16))
        self.setFixedWidth(width)
        self.adjustSize()
        height = min(self.sizeHint().height(), max(80, parent.height() - 16))
        self.setFixedHeight(height)
        x = max(8, min(caret_rect.left(), parent.width() - width - 8))
        below = caret_rect.bottom() + 6
        if below + height <= parent.height() - 8:
            y = below
        else:
            y = max(8, caret_rect.top() - height - 6)
        self.move(x, y)
        self.raise_()
        self.show()

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"""
QFrame#slashCommandPopup {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 9px;
}}
QLabel#slashCommandTitle {{
    color: {theme.text}; font-weight: 650; font-size: 12px; border: none;
}}
QLabel#slashCommandHint {{
    color: {theme.text_subtle}; font-size: 10px; border: none;
}}
QListWidget#slashCommandList {{
    background: transparent; color: {theme.text}; border: none;
    outline: none; padding: 0;
}}
QListWidget#slashCommandList::item {{
    border: none; border-radius: 6px; padding: 0 9px;
}}
QListWidget#slashCommandList::item:hover {{
    background: {theme.surface_hover};
}}
QListWidget#slashCommandList::item:selected {{
    background: {theme.accent_soft}; color: {theme.text};
}}
QLabel#slashCommandEmpty {{
    color: {theme.text_muted}; border: none;
}}
QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {theme.text_subtle}; border-radius: 3px; min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""
        )


class SelectionFormatBar(QFrame):
    """Small Word-like toolbar shown after a mouse text selection."""

    action_triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("selectionFormatBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 4, 5, 4)
        layout.setSpacing(2)
        self._buttons: dict[str, QToolButton] = {}
        for command in commands_for("selection"):
            button = QToolButton()
            button.setObjectName("selectionFormatButton")
            button.setText(command.toolbar_label)
            button.setToolTip(command.tooltip)
            button.setAccessibleName(command.tooltip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedWidth(
                40
                if command.action_id in {"inline_code", "link", "highlight"}
                else 30
            )
            button.clicked.connect(
                lambda _checked=False, aid=command.action_id: (
                    self.action_triggered.emit(aid)
                )
            )
            font = button.font()
            if command.action_id == "bold":
                font.setBold(True)
            elif command.action_id == "italic":
                font.setItalic(True)
            elif command.action_id == "strikethrough":
                font.setStrikeOut(True)
            button.setFont(font)
            layout.addWidget(button)
            self._buttons[command.action_id] = button
        self.hide()

    def button(self, action_id: str) -> QToolButton:
        return self._buttons[action_id]

    def show_for_selection(self, editor, start: int, end: int) -> None:
        first = QTextCursor(editor.document())
        first.setPosition(start)
        last = QTextCursor(editor.document())
        last.setPosition(end)
        first_rect = editor.cursorRect(first)
        last_rect = editor.cursorRect(last)
        self.adjustSize()
        width = self.sizeHint().width()
        height = self.sizeHint().height()
        parent = self.parentWidget()
        if parent is None or width > parent.width() - 12:
            self.hide()
            return
        center = (first_rect.left() + last_rect.right()) // 2
        x = max(6, min(center - width // 2, parent.width() - width - 6))
        top = min(first_rect.top(), last_rect.top())
        bottom = max(first_rect.bottom(), last_rect.bottom())
        y = top - height - 6
        if y < 6:
            y = bottom + 6
        y = max(6, min(y, parent.height() - height - 6))
        self.move(x, y)
        self.raise_()
        self.show()

    def apply_theme(self, theme: Theme) -> None:
        self.setStyleSheet(
            f"""
QFrame#selectionFormatBar {{
    background: {theme.surface}; border: 1px solid {theme.border};
    border-radius: 8px;
}}
QFrame#selectionFormatBar QToolButton {{
    background: transparent; color: {theme.text_muted};
    border: 1px solid transparent; border-radius: 5px;
    min-height: 26px; padding: 0 3px;
}}
QFrame#selectionFormatBar QToolButton:hover {{
    background: {theme.surface_hover}; color: {theme.text};
}}
QFrame#selectionFormatBar QToolButton:pressed {{
    background: {theme.accent_soft}; color: {theme.accent};
}}
"""
        )
