"""Non-modal panel that shows a selection and its translation.

The window is reused across requests so repeated right-click translations do
not litter the desktop. It also owns the service / language pickers: switching
either re-runs the translation straight away, which is the whole point of
putting them here rather than only in the preferences dialog.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .theme import Theme
from .translate import PROVIDERS, TARGETS, provider_info, target_label


class TranslationDialog(QDialog):
    """Shows the source text on top and the translation below it."""

    # provider key, target key, force (bypass the cache)
    retranslate_requested = Signal(str, str, bool)

    def __init__(self, parent=None, *, theme: Theme | None = None):
        super().__init__(parent)
        self.setWindowTitle("翻譯")
        self.setMinimumSize(480, 420)
        # Non-modal: the user keeps reading the document while it translates.
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._source_text = ""
        # Guards the combos while we set them programmatically, so restoring
        # the stored choice does not look like the user asking for a re-run.
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        root.addWidget(QLabel("原文"))
        self._source = QPlainTextEdit()
        self._source.setReadOnly(True)
        self._source.setMaximumHeight(120)
        root.addWidget(self._source)

        # ── service / language controls ─────────────────────────────────
        controls = QHBoxLayout()
        controls.setSpacing(6)

        self._provider_combo = QComboBox()
        for info in PROVIDERS:
            self._provider_combo.addItem(info.label, info.key)
        self._provider_combo.currentIndexChanged.connect(self._on_choice_changed)
        controls.addWidget(self._provider_combo, 1)

        self._target_combo = QComboBox()
        for code, label in TARGETS:
            self._target_combo.addItem(label, code)
        self._target_combo.currentIndexChanged.connect(self._on_choice_changed)
        controls.addWidget(self._target_combo)

        self._retry_btn = QPushButton("重新翻譯")
        self._retry_btn.setToolTip("忽略快取，重新向服務要一次譯文")
        self._retry_btn.clicked.connect(self._on_retry)
        controls.addWidget(self._retry_btn)
        root.addLayout(controls)

        root.addWidget(QLabel("譯文"))
        self._result = QPlainTextEdit()
        self._result.setReadOnly(True)
        root.addWidget(self._result, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._copy_btn = QPushButton("複製譯文")
        self._copy_btn.clicked.connect(self._copy_result)
        buttons.addWidget(self._copy_btn)
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        self._theme = theme
        if theme is not None:
            self.apply_theme(theme)
        self._set_busy(False)

    # ── state transitions ───────────────────────────────────────────────

    def source_text(self) -> str:
        return self._source_text

    def current_provider(self) -> str:
        return self._provider_combo.currentData()

    def current_target(self) -> str:
        return self._target_combo.currentData()

    def start(self, source_text: str, provider: str, target: str):
        """Reset to the pending state for a freshly issued request."""
        self._source_text = source_text
        self._source.setPlainText(source_text)
        self._result.setPlainText("")
        self._sync_controls(provider, target)
        self._status.setText(f"翻譯中…（{self._describe(provider, target)}）")
        self._set_busy(True)

    def show_result(
        self, translated: str, provider: str, target: str, *, from_cache: bool = False
    ):
        self._result.setPlainText(translated)
        suffix = "・快取" if from_cache else ""
        self._status.setText(f"{self._describe(provider, target)}{suffix}")
        self._sync_controls(provider, target)
        self._set_busy(False)

    def show_error(self, message: str):
        self._result.setPlainText("")
        self._status.setText(f"⚠ {message}")
        self._set_busy(False)

    @staticmethod
    def _describe(provider: str, target: str) -> str:
        return f"{provider_info(provider).label} → {target_label(target)}"

    def _sync_controls(self, provider: str, target: str):
        self._syncing = True
        try:
            p_idx = self._provider_combo.findData(provider)
            if p_idx >= 0:
                self._provider_combo.setCurrentIndex(p_idx)
            t_idx = self._target_combo.findData(target)
            if t_idx >= 0:
                self._target_combo.setCurrentIndex(t_idx)
        finally:
            self._syncing = False

    def _set_busy(self, busy: bool):
        self._provider_combo.setEnabled(not busy)
        self._target_combo.setEnabled(not busy)
        self._retry_btn.setEnabled(not busy and bool(self._source_text))
        self._copy_btn.setEnabled(not busy and bool(self._result.toPlainText()))

    # ── user actions ────────────────────────────────────────────────────

    def _on_choice_changed(self, _index: int):
        if self._syncing or not self._source_text:
            return
        # A different service or language is a different request, so the cache
        # is still valid for it — no need to force.
        self.retranslate_requested.emit(
            self.current_provider(), self.current_target(), False
        )

    def _on_retry(self):
        if not self._source_text:
            return
        self.retranslate_requested.emit(
            self.current_provider(), self.current_target(), True
        )

    def _copy_result(self):
        text = self._result.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)
            self._status.setText("譯文已複製到剪貼簿")

    # ── theming ─────────────────────────────────────────────────────────

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(
            f"""
QDialog {{ background: {theme.window}; color: {theme.text}; }}
QLabel {{ color: {theme.text_muted}; }}
QPlainTextEdit {{
    background: {theme.surface};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: 4px;
    padding: 6px;
}}
QComboBox {{
    background: {theme.surface};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: 4px;
    padding: 3px 8px;
}}
QComboBox:disabled {{ color: {theme.text_subtle}; }}
QComboBox QAbstractItemView {{
    background: {theme.surface};
    color: {theme.text};
    selection-background-color: {theme.surface_active};
}}
QPushButton {{
    background: {theme.surface};
    color: {theme.text};
    border: 1px solid {theme.border};
    border-radius: 4px;
    padding: 4px 12px;
}}
QPushButton:hover {{ background: {theme.surface_hover}; }}
QPushButton:disabled {{ color: {theme.text_subtle}; }}
"""
        )
