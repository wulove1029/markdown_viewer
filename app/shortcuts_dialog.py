"""Scrollable, searchable keyboard-shortcut reference dialog."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .shortcuts import ALL_SHORTCUTS, ShortcutSpec, grouped_shortcuts
from .theme import Theme, svg_icon


class _DialogCloseButton(QPushButton):
    """Activate on Enter only when this button itself has keyboard focus."""

    def keyPressEvent(self, event):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
        ):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class ShortcutDialog(QDialog):
    """Complete shortcut reference that remains usable on smaller screens."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._rows: list[tuple[ShortcutSpec, QWidget]] = []
        self._groups: list[tuple[str, QWidget, list[tuple[ShortcutSpec, QWidget]]]] = []

        self.setObjectName("shortcutDialog")
        self.setWindowTitle("鍵盤快捷鍵")
        self.setModal(True)
        self.setSizeGripEnabled(True)
        self._set_initial_size()

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title = QLabel("鍵盤快捷鍵")
        title.setObjectName("shortcutTitle")
        subtitle = QLabel("快捷鍵會依目前焦點、文件類型與編輯模式生效。")
        subtitle.setObjectName("shortcutSubtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)

        self._count_badge = QLabel()
        self._count_badge.setObjectName("shortcutCountBadge")
        self._count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self._count_badge, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        self._search = QLineEdit()
        self._search.setObjectName("shortcutSearch")
        self._search.setPlaceholderText("搜尋快捷鍵、功能或適用範圍…")
        self._search.setClearButtonEnabled(True)
        self._search.setAccessibleName("搜尋快捷鍵")
        self._search.textChanged.connect(self._filter_rows)
        clear_button = self._search.findChild(QToolButton)
        self._clear_button = clear_button
        if clear_button is not None:
            clear_button.setObjectName("shortcutSearchClearButton")
            clear_button.setIcon(svg_icon("x", theme.text_muted, 14))
            clear_button.setIconSize(QSize(14, 14))
            clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_button.setToolTip("清除搜尋")
            clear_button.setAccessibleName("清除搜尋")
            clear_button.installEventFilter(self)
        root.addWidget(self._search)

        scroll = QScrollArea()
        scroll.setObjectName("shortcutScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("shortcutContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 4, 0)
        content_layout.setSpacing(10)

        for group_name, specs in grouped_shortcuts():
            group = self._build_group(group_name, specs)
            content_layout.addWidget(group)
        self._empty_state = QLabel("找不到符合的快捷鍵")
        self._empty_state.setObjectName("shortcutEmptyState")
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setMinimumHeight(140)
        self._empty_state.hide()
        content_layout.addWidget(self._empty_state)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        footer_line = QFrame()
        footer_line.setObjectName("shortcutFooterLine")
        footer_line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(footer_line)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        note = QLabel(
            "文字欄位同樣支援 Windows 標準編輯鍵："
            "Ctrl+(Z / Y / X / C / V / A)；Alt+字母可開啟上方選單。"
        )
        note.setObjectName("shortcutStandardNote")
        note.setWordWrap(True)
        footer.addWidget(note, 1)
        close_button = _DialogCloseButton("關閉")
        close_button.setObjectName("shortcutCloseButton")
        # Search filters live while typing.  Keeping this button non-default
        # prevents Enter in the search field from unexpectedly closing the
        # reference window.
        close_button.setDefault(False)
        close_button.setAutoDefault(False)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self.setStyleSheet(self._stylesheet())
        self._update_count(len(ALL_SHORTCUTS))
        self._search.setFocus()

    def eventFilter(self, watched, event):
        if watched is self._clear_button:
            if event.type() == QEvent.Type.Enter:
                watched.setIcon(svg_icon("x", self._theme.accent, 14))
            elif event.type() == QEvent.Type.Leave:
                watched.setIcon(svg_icon("x", self._theme.text_muted, 14))
        return super().eventFilter(watched, event)

    def _set_initial_size(self) -> None:
        app = QApplication.instance()
        screen = self.parentWidget().screen() if self.parentWidget() else None
        if screen is None and app is not None:
            screen = app.primaryScreen()
        if screen is None:
            self.setMinimumSize(600, 460)
            self.resize(720, 620)
            return
        available = screen.availableGeometry()
        width = min(720, max(480, available.width() - 64))
        height = min(620, max(400, available.height() - 64))
        self.setMinimumSize(min(600, width), min(460, height))
        self.resize(width, height)

    def _build_group(
        self, group_name: str, specs: tuple[ShortcutSpec, ...]
    ) -> QWidget:
        card = QFrame()
        card.setObjectName("shortcutGroup")
        card.setProperty("shortcutGroup", group_name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(0)

        heading = QLabel(group_name)
        heading.setObjectName("shortcutGroupTitle")
        layout.addWidget(heading)

        group_rows: list[tuple[ShortcutSpec, QWidget]] = []
        for spec in specs:
            row = self._build_row(spec)
            layout.addWidget(row)
            pair = (spec, row)
            self._rows.append(pair)
            group_rows.append(pair)
        self._groups.append((group_name, card, group_rows))
        return card

    def _build_row(self, spec: ShortcutSpec) -> QWidget:
        row = QFrame()
        row.setObjectName("shortcutRow")
        row.setProperty("commandId", spec.command_id)
        row.setMinimumHeight(44)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 7, 12, 7)
        layout.setSpacing(10)

        label = QLabel(spec.label)
        label.setObjectName("shortcutActionLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(label, 1)

        scope = QLabel(spec.scope)
        scope.setObjectName("shortcutScopeLabel")
        scope.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scope.setToolTip(f"適用範圍：{spec.scope}")
        layout.addWidget(scope)

        keys = QWidget()
        keys.setObjectName("shortcutKeys")
        keys.setMinimumWidth(174)
        key_layout = QHBoxLayout(keys)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.setSpacing(5)
        key_layout.addStretch(1)
        for index, sequence in enumerate(spec.sequences):
            if index:
                separator = QLabel("或")
                separator.setObjectName("shortcutKeySeparator")
                key_layout.addWidget(separator)
            keycap = QLabel(sequence)
            keycap.setObjectName("shortcutKeycap")
            keycap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            keycap.setAccessibleName(sequence)
            key_layout.addWidget(keycap)
        layout.addWidget(keys)
        return row

    def _filter_rows(self, text: str) -> None:
        query = text.strip().casefold()
        visible_count = 0
        for group_name, card, rows in self._groups:
            group_matches = bool(query and query in group_name.casefold())
            group_visible = False
            for spec, row in rows:
                haystack = " ".join(
                    (
                        spec.command_id,
                        spec.group,
                        spec.label,
                        spec.scope,
                        *spec.sequences,
                    )
                ).casefold()
                visible = not query or group_matches or query in haystack
                row.setVisible(visible)
                group_visible = group_visible or visible
                visible_count += int(visible)
            card.setVisible(group_visible)
        self._empty_state.setVisible(visible_count == 0)
        self._update_count(visible_count)

    def _update_count(self, count: int) -> None:
        self._count_badge.setText(f"{count} 個操作")

    def _stylesheet(self) -> str:
        t = self._theme
        return f"""
QDialog#shortcutDialog {{
    background: {t.window};
    color: {t.text};
    font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
    font-size: 13px;
}}
QLabel#shortcutTitle {{ color: {t.text}; font-size: 22px; font-weight: 700; }}
QLabel#shortcutSubtitle {{ color: {t.text_muted}; font-size: 12px; }}
QLabel#shortcutCountBadge {{
    background: {t.accent_soft}; color: {t.accent}; border-radius: 11px;
    padding: 4px 10px; font-size: 12px; font-weight: 600;
}}
QLineEdit#shortcutSearch {{
    background: {t.surface}; color: {t.text}; border: 1px solid {t.border};
    border-radius: 8px; min-height: 34px; padding: 2px 10px;
    selection-background-color: {t.accent_soft};
}}
QLineEdit#shortcutSearch:focus {{ border: 1px solid {t.accent}; }}
QToolButton#shortcutSearchClearButton {{
    background: transparent; border: none; border-radius: 4px; padding: 2px;
}}
QToolButton#shortcutSearchClearButton:hover {{ background: {t.surface_hover}; }}
QScrollArea#shortcutScrollArea, QWidget#shortcutContent {{
    background: transparent; border: none;
}}
QFrame#shortcutGroup {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: 9px;
}}
QLabel#shortcutGroupTitle {{
    background: {t.surface_alt}; color: {t.text}; border: none;
    border-top-left-radius: 8px; border-top-right-radius: 8px;
    padding: 9px 14px; font-size: 14px; font-weight: 650;
}}
QFrame#shortcutRow {{
    background: transparent; border: none; border-top: 1px solid {t.border};
}}
QLabel#shortcutActionLabel {{ color: {t.text}; border: none; }}
QLabel#shortcutScopeLabel {{
    background: {t.surface_alt}; color: {t.text_muted}; border: none;
    border-radius: 9px; padding: 2px 7px; font-size: 11px;
}}
QWidget#shortcutKeys {{ background: transparent; border: none; }}
QLabel#shortcutKeycap {{
    background: {t.surface_alt}; color: {t.text};
    border: 1px solid {t.border}; border-bottom: 2px solid {t.border};
    border-radius: 5px; padding: 3px 7px;
    font-family: "Cascadia Mono", Consolas, monospace; font-size: 11px;
}}
QLabel#shortcutKeySeparator {{ color: {t.text_subtle}; border: none; font-size: 10px; }}
QLabel#shortcutEmptyState {{ color: {t.text_muted}; font-size: 13px; }}
QFrame#shortcutFooterLine {{ color: {t.border}; }}
QLabel#shortcutStandardNote {{ color: {t.text_muted}; font-size: 11px; }}
QPushButton#shortcutCloseButton {{
    background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
    border-radius: 7px; min-width: 72px; min-height: 32px; padding: 1px 14px;
}}
QPushButton#shortcutCloseButton:hover {{ background: {t.accent_hover}; }}
QPushButton#shortcutCloseButton:pressed {{ background: {t.accent_hover}; padding-top: 2px; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: {t.text_subtle}; border-radius: 4px; min-height: 28px; margin: 1px 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {t.text_muted}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
