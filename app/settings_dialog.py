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
from .edit_backend import (
    DEFAULT_BACKEND,
    PREVIEW_DOUBLE_CLICK_DEFAULT,
    PREVIEW_DOUBLE_CLICK_INLINE,
    PREVIEW_DOUBLE_CLICK_SETTINGS_KEY as PREVIEW_DOUBLE_CLICK_KEY,
    PREVIEW_DOUBLE_CLICK_WYSIWYG,
    SETTINGS_KEY as EDIT_BACKEND_KEY,
    SPLIT_BACKEND,
    WYSIWYG_BACKEND,
    normalize_backend,
    normalize_preview_double_click,
)
from .note_templates import default_subfolder
from .translate import (
    DEEPL_KEY,
    PROVIDER_KEY,
    PROVIDERS,
    TARGET_KEY,
    TARGETS,
    normalize_provider,
    normalize_target,
    provider_info,
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
        tabs.addTab(self._build_translate_tab(settings), "翻譯")
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

        # Default for creation flows that do not ask explicitly.  The source
        # editor is the safe default; users can still choose Office from the
        # toolbar, Edit menu, shortcut, or new-note dialog.
        current_backend = normalize_backend(
            settings.value(EDIT_BACKEND_KEY, DEFAULT_BACKEND)
        )
        self._edit_backend_combo = QComboBox()
        self._edit_backend_combo.addItem(
            "原始 Markdown（純文字，建議）", SPLIT_BACKEND
        )
        self._edit_backend_combo.addItem(
            "Office 視覺編輯器（WYSIWYG）", WYSIWYG_BACKEND
        )
        self._edit_backend_combo.setCurrentIndex(
            1 if current_backend == WYSIWYG_BACKEND else 0
        )
        form.addRow("新筆記預設編輯器", self._edit_backend_combo)
        backend_hint = QLabel(
            "兩種方式都讀寫標準 .md 純文字；原始 Markdown 直接編輯文字、不經"
            "視覺轉換。Office 視覺編輯可能整理排版，遇到 wiki-links、callouts"
            " 或 front matter 時會先警告。純文字（.txt）一律使用原始編輯器。"
        )
        backend_hint.setWordWrap(True)
        form.addRow("", backend_hint)

        # v2: what a PREVIEW double-click does (VSCode Office Viewer style).
        current_dblclick = normalize_preview_double_click(
            settings.value(PREVIEW_DOUBLE_CLICK_KEY, PREVIEW_DOUBLE_CLICK_DEFAULT)
        )
        self._preview_dblclick_combo = QComboBox()
        self._preview_dblclick_combo.addItem(
            "維持原本行為（雙擊僅選取文字）", PREVIEW_DOUBLE_CLICK_INLINE
        )
        self._preview_dblclick_combo.addItem(
            "直接進入 Office 視覺編輯器", PREVIEW_DOUBLE_CLICK_WYSIWYG
        )
        self._preview_dblclick_combo.setCurrentIndex(
            1 if current_dblclick == PREVIEW_DOUBLE_CLICK_WYSIWYG else 0
        )
        form.addRow("檢視模式雙擊文件", self._preview_dblclick_combo)
        dblclick_hint = QLabel(
            "三擊仍可就地編輯單一區塊；此處只決定「雙擊」的行為；"
            "僅 Markdown 檔生效，.txt 與 PDF 不受影響。"
        )
        dblclick_hint.setWordWrap(True)
        form.addRow("", dblclick_hint)

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

    def _build_translate_tab(self, settings: QSettings) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(16, 16, 16, 16)

        current_provider = normalize_provider(settings.value(PROVIDER_KEY))
        self._translate_provider_combo = QComboBox()
        for info in PROVIDERS:
            self._translate_provider_combo.addItem(info.label, info.key)
        provider_idx = next(
            (
                i
                for i, info in enumerate(PROVIDERS)
                if info.key == current_provider
            ),
            0,
        )
        self._translate_provider_combo.setCurrentIndex(provider_idx)
        form.addRow("翻譯服務", self._translate_provider_combo)

        note = QLabel()
        note.setWordWrap(True)
        form.addRow("", note)

        current_target = normalize_target(settings.value(TARGET_KEY))
        self._translate_target_combo = QComboBox()
        for code, label in TARGETS:
            self._translate_target_combo.addItem(label, code)
        target_idx = next(
            (i for i, (code, _) in enumerate(TARGETS) if code == current_target),
            0,
        )
        self._translate_target_combo.setCurrentIndex(target_idx)
        form.addRow("翻譯成", self._translate_target_combo)

        self._deepl_key_edit = QLineEdit(str(settings.value(DEEPL_KEY, "") or ""))
        self._deepl_key_edit.setPlaceholderText("僅 DeepL 需要，例如 xxxxxxxx:fx")
        self._deepl_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_label = QLabel("DeepL 金鑰")
        form.addRow(key_label, self._deepl_key_edit)

        def _sync_provider():
            info = provider_info(self._translate_provider_combo.currentData())
            note.setText(info.note)
            # The key row stays visible but inert so the layout does not jump.
            key_label.setEnabled(info.needs_api_key)
            self._deepl_key_edit.setEnabled(info.needs_api_key)

        self._translate_provider_combo.currentIndexChanged.connect(_sync_provider)
        _sync_provider()

        hint = QLabel(
            "在預覽、PDF 或編輯器中選取文字後按右鍵，選擇「翻譯選取內容」。\n"
            "單次翻譯上限 3000 字元；免費服務有每日額度，請避免整章翻譯。"
        )
        hint.setWordWrap(True)
        form.addRow("", hint)

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
        edit_backend_default = self._edit_backend_combo.currentData()
        self.results[EDIT_BACKEND_KEY] = edit_backend_default
        settings.setValue(EDIT_BACKEND_KEY, edit_backend_default)
        preview_dblclick = self._preview_dblclick_combo.currentData()
        self.results[PREVIEW_DOUBLE_CLICK_KEY] = preview_dblclick
        settings.setValue(PREVIEW_DOUBLE_CLICK_KEY, preview_dblclick)
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

        # Translation
        provider = self._translate_provider_combo.currentData()
        target = self._translate_target_combo.currentData()
        deepl_key = self._deepl_key_edit.text().strip()
        self.results[PROVIDER_KEY] = provider
        self.results[TARGET_KEY] = target
        self.results[DEEPL_KEY] = deepl_key
        settings.setValue(PROVIDER_KEY, provider)
        settings.setValue(TARGET_KEY, target)
        settings.setValue(DEEPL_KEY, deepl_key)

        super().accept()
