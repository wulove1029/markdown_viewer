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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .document_libraries import (
    EXCLUDED_FOLDERS_KEY,
    DocumentLibraryStore,
)
from .note_templates import default_subfolder
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

        try:
            libraries = DocumentLibraryStore().load()
        except OSError:
            libraries = []
        default_daily = default_subfolder(libraries, "Daily Notes")
        default_templates = default_subfolder(libraries, "Templates")

        daily_group = QGroupBox("Daily notes")
        daily_form = QFormLayout(daily_group)
        daily_path = settings.value(
            "daily_notes_folder", str(default_daily or "")
        ) or str(default_daily or "")
        self._daily_notes_edit = QLineEdit(str(daily_path))
        daily_browse = QPushButton("瀏覽…")
        daily_row = QWidget()
        daily_layout = QHBoxLayout(daily_row)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        daily_layout.addWidget(self._daily_notes_edit, 1)
        daily_layout.addWidget(daily_browse)

        def _browse_daily_folder():
            path = QFileDialog.getExistingDirectory(
                self,
                "選擇 Daily notes 資料夾",
                self._daily_notes_edit.text(),
            )
            if path:
                self._daily_notes_edit.setText(path)

        daily_browse.clicked.connect(_browse_daily_folder)
        daily_form.addRow("資料夾", daily_row)
        daily_form.addRow("檔名格式", QLabel("YYYY-MM-DD（固定）"))

        self._daily_template_edit = QLineEdit(
            str(settings.value("daily_note_template", "") or "")
        )
        self._daily_template_edit.setPlaceholderText("選用的 Markdown 範本檔")
        daily_template_browse = QPushButton("瀏覽…")
        daily_template_row = QWidget()
        daily_template_layout = QHBoxLayout(daily_template_row)
        daily_template_layout.setContentsMargins(0, 0, 0, 0)
        daily_template_layout.addWidget(self._daily_template_edit, 1)
        daily_template_layout.addWidget(daily_template_browse)

        def _browse_daily_template():
            initial = self._daily_template_edit.text() or str(
                default_templates or ""
            )
            path, _ = QFileDialog.getOpenFileName(
                self,
                "選擇 Daily note 範本",
                initial,
                "Markdown 範本 (*.md)",
            )
            if path:
                self._daily_template_edit.setText(path)

        daily_template_browse.clicked.connect(_browse_daily_template)
        daily_form.addRow("範本檔", daily_template_row)
        form.addRow(daily_group)

        templates_group = QGroupBox("筆記範本")
        templates_form = QFormLayout(templates_group)
        templates_path = settings.value(
            "templates_folder", str(default_templates or "")
        ) or str(default_templates or "")
        self._templates_folder_edit = QLineEdit(str(templates_path))
        templates_browse = QPushButton("瀏覽…")
        templates_row = QWidget()
        templates_layout = QHBoxLayout(templates_row)
        templates_layout.setContentsMargins(0, 0, 0, 0)
        templates_layout.addWidget(self._templates_folder_edit, 1)
        templates_layout.addWidget(templates_browse)

        def _browse_templates_folder():
            path = QFileDialog.getExistingDirectory(
                self,
                "選擇筆記範本資料夾",
                self._templates_folder_edit.text(),
            )
            if path:
                self._templates_folder_edit.setText(path)

        templates_browse.clicked.connect(_browse_templates_folder)
        templates_form.addRow("資料夾", templates_row)
        form.addRow(templates_group)

        excluded_group = QGroupBox("排除資料夾")
        excluded_layout = QVBoxLayout(excluded_group)
        excluded_help = QLabel(
            "每行一項；可填資料夾名稱（任何層級）或相對路徑（例如 app_flutter/ios）。\n"
            "內建已排除 .git、node_modules 等常見版本控制與生成物資料夾。"
        )
        excluded_help.setWordWrap(True)
        excluded_layout.addWidget(excluded_help)
        self._excluded_folders_edit = QTextEdit()
        self._excluded_folders_edit.setAcceptRichText(False)
        raw_excluded = settings.value(EXCLUDED_FOLDERS_KEY, "") or ""
        if isinstance(raw_excluded, (list, tuple)):
            raw_excluded = "\n".join(str(value) for value in raw_excluded)
        self._excluded_folders_edit.setPlainText(str(raw_excluded))
        self._excluded_folders_edit.setPlaceholderText("ios\napp_flutter/generated")
        self._excluded_folders_edit.setFixedHeight(100)
        excluded_layout.addWidget(self._excluded_folders_edit)
        form.addRow(excluded_group)

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
        daily_notes_folder = self._daily_notes_edit.text().strip()
        daily_note_template = self._daily_template_edit.text().strip()
        templates_folder = self._templates_folder_edit.text().strip()
        excluded_folders = "\n".join(
            line.strip()
            for line in self._excluded_folders_edit.toPlainText().splitlines()
            if line.strip()
        )
        self.results["update_check_enabled"] = update_check
        self.results["custom_css_path"] = css_path
        self.results["daily_notes_folder"] = daily_notes_folder
        self.results["daily_note_template"] = daily_note_template
        self.results["templates_folder"] = templates_folder
        self.results[EXCLUDED_FOLDERS_KEY] = excluded_folders
        settings.setValue("update_check_enabled", update_check)
        settings.setValue("custom_css_path", css_path)
        settings.setValue("daily_notes_folder", daily_notes_folder)
        settings.setValue("daily_note_template", daily_note_template)
        settings.setValue("templates_folder", templates_folder)
        settings.setValue(EXCLUDED_FOLDERS_KEY, excluded_folders)

        super().accept()
