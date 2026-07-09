"""Tabbed preferences dialog that consolidates all user settings.

All QSettings keys used here **must** match the keys already written by the
scattered settings code in ``window.py`` so that older configurations migrate
seamlessly.  Do **not** rename any key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QPageSize
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .version import VERSION

# ── constants (must match window.py originals) ──────────────────────────

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"

_ZOOM_OPTIONS: list[int] = [80, 90, 100, 110, 125, 150, 175, 200]

_PDF_SIZE_CHOICES: list[tuple[str, str]] = [
    ("A4", "A4"),
    ("A3", "A3"),
    ("Letter", "Letter（美規信紙）"),
    ("Legal", "Legal（美規法律）"),
    ("single", "單一長頁（不分頁）"),
]

_ORIENT_CHOICES: list[tuple[str, str]] = [
    ("portrait", "直向"),
    ("landscape", "橫向"),
]


# ── helper ──────────────────────────────────────────────────────────────

def _bool_from_qsettings(value: Any, default: bool = True) -> bool:
    """Interpret a QSettings value that might be a bool or a string."""
    if isinstance(value, bool):
        return value
    return str(value).lower() not in ("0", "false", "no", "off")


# ── dialog ──────────────────────────────────────────────────────────────

class SettingsDialog(QDialog):
    """Modal preferences dialog with four tabs.

    After ``exec()`` returns ``Accepted``, the caller should read back
    :pyattr:`results` – a dict of *changed* settings – and apply them.
    """

    def __init__(self, parent: QWidget | None = None, *,
                 current_theme: str = "light",
                 current_zoom: float = 1.0):
        super().__init__(parent)
        self.setWindowTitle("偏好設定")
        self.setMinimumWidth(480)
        self.results: dict[str, Any] = {}

        self._current_theme = current_theme
        self._current_zoom = current_zoom

        settings = QSettings(_ORG, _APP)

        root = QVBoxLayout(self)

        tabs = QTabWidget(self)
        tabs.addTab(self._build_appearance_tab(settings), "外觀")
        tabs.addTab(self._build_export_tab(settings), "匯出")
        tabs.addTab(self._build_behavior_tab(settings), "行為")
        tabs.addTab(self._build_about_tab(), "關於")
        root.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("確定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ── tab builders ────────────────────────────────────────────────────

    def _build_appearance_tab(self, settings: QSettings) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)

        # Theme
        self._theme_combo = QComboBox()
        self._theme_combo.addItem("淺色", "light")
        self._theme_combo.addItem("深色", "dark")
        self._theme_combo.setCurrentIndex(
            1 if self._current_theme == "dark" else 0,
        )
        form.addRow("主題", self._theme_combo)

        # Default zoom
        self._zoom_combo = QComboBox()
        for pct in _ZOOM_OPTIONS:
            self._zoom_combo.addItem(f"{pct}%", pct / 100)
        current_pct = round(self._current_zoom * 100)
        zoom_idx = next(
            (i for i in range(self._zoom_combo.count())
             if round(self._zoom_combo.itemData(i) * 100) == current_pct),
            2,  # fallback 100%
        )
        self._zoom_combo.setCurrentIndex(zoom_idx)
        form.addRow("內容縮放", self._zoom_combo)

        return page

    def _build_export_tab(self, settings: QSettings) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)

        last_size = settings.value("pdf_page_size", "A4") or "A4"
        last_orient = settings.value("pdf_orientation", "portrait") or "portrait"

        self._pdf_size_combo = QComboBox()
        for key, label in _PDF_SIZE_CHOICES:
            self._pdf_size_combo.addItem(label, key)
        size_idx = next(
            (i for i, (k, _) in enumerate(_PDF_SIZE_CHOICES) if k == last_size),
            0,
        )
        self._pdf_size_combo.setCurrentIndex(size_idx)
        form.addRow("PDF 紙張大小", self._pdf_size_combo)

        self._pdf_orient_combo = QComboBox()
        for key, label in _ORIENT_CHOICES:
            self._pdf_orient_combo.addItem(label, key)
        self._pdf_orient_combo.setCurrentIndex(
            1 if last_orient == "landscape" else 0,
        )

        def _sync_orientation():
            self._pdf_orient_combo.setEnabled(
                self._pdf_size_combo.currentData() != "single",
            )

        self._pdf_size_combo.currentIndexChanged.connect(_sync_orientation)
        _sync_orientation()
        form.addRow("PDF 方向", self._pdf_orient_combo)

        return page

    def _build_behavior_tab(self, settings: QSettings) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)

        # Update check
        self._update_cb = QCheckBox("啟動時自動檢查更新（每日一次）")
        raw = settings.value("update_check_enabled", True)
        self._update_cb.setChecked(_bool_from_qsettings(raw))
        form.addRow("", self._update_cb)

        # Custom CSS
        css_path = settings.value("custom_css_path", "") or ""
        self._css_edit = QLineEdit(css_path)
        self._css_edit.setPlaceholderText("選用的 .css 檔案路徑")
        browse_btn = QPushButton("瀏覽…")
        css_row = QWidget()
        css_layout = QHBoxLayout(css_row)
        css_layout.setContentsMargins(0, 0, 0, 0)
        css_layout.addWidget(self._css_edit, 1)
        css_layout.addWidget(browse_btn)

        def _browse():
            path, _ = QFileDialog.getOpenFileName(
                self, "選擇 CSS 檔案", "", "CSS 樣式表 (*.css)",
            )
            if path:
                self._css_edit.setText(path)

        browse_btn.clicked.connect(_browse)
        form.addRow("自訂 CSS", css_row)

        return page

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = QLabel(f"<h2>Markdown Viewer</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(title)

        ver = QLabel(f"版本　{VERSION}")
        layout.addWidget(ver)

        desc = QLabel("Markdown 筆記閱讀 / 編輯與 PDF 閱讀工具。")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch()
        return page

    # ── accept override ─────────────────────────────────────────────────

    def accept(self):
        """Collect changed values, persist to QSettings, and close."""
        settings = QSettings(_ORG, _APP)

        # Appearance
        theme = self._theme_combo.currentData()
        zoom = self._zoom_combo.currentData()
        self.results["theme"] = theme
        self.results["content_zoom"] = zoom
        settings.setValue("theme", theme)
        settings.setValue("content_zoom", zoom)

        # Export
        pdf_size = self._pdf_size_combo.currentData()
        pdf_orient = self._pdf_orient_combo.currentData()
        self.results["pdf_page_size"] = pdf_size
        self.results["pdf_orientation"] = pdf_orient
        settings.setValue("pdf_page_size", pdf_size)
        settings.setValue("pdf_orientation", pdf_orient)

        # Behavior
        update_check = self._update_cb.isChecked()
        css_path = self._css_edit.text().strip()
        self.results["update_check_enabled"] = update_check
        self.results["custom_css_path"] = css_path
        settings.setValue("update_check_enabled", update_check)
        settings.setValue("custom_css_path", css_path)

        super().accept()
