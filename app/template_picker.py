"""Searchable template chooser showing the exact text that will be inserted."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)

from .note_templates import render_template_file
from .theme import LIGHT, Theme


class TemplatePickerDialog(QDialog):
    """Recent templates first, with rendered/source preview and a frozen clock."""

    def __init__(
        self, templates, *, title: str, recent=(), folder=None,
        now: datetime | None = None, theme: Theme = LIGHT, parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("插入範本")
        self.resize(900, 620)
        self.setMinimumSize(580, 420)
        self._title = title
        self._now = now or datetime.now()
        self._folder = Path(folder) if folder else None
        self._rendered: str | None = None
        self._selected_path: Path | None = None
        available = list(dict.fromkeys(Path(path) for path in templates))
        recent_paths = list(dict.fromkeys(Path(path) for path in recent))
        recent_paths = [path for path in recent_paths if path in available][:5]
        ordered = recent_paths + [path for path in available if path not in recent_paths]
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("搜尋範本名稱…")
        self.search.setAccessibleName("搜尋範本")
        layout.addWidget(self.search)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.templates = QListWidget(splitter)
        self.templates.setAccessibleName("可插入的範本")
        tabs = QTabWidget(splitter)
        self.preview = QTextBrowser(tabs)
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.setAccessibleName("範本閱讀預覽")
        self.source = QPlainTextEdit(tabs)
        self.source.setReadOnly(True)
        self.source.setAccessibleName("替換變數後的 Markdown 範本")
        tabs.addTab(self.preview, "閱讀預覽")
        tabs.addTab(self.source, "Markdown 內容")
        splitter.setSizes([260, 640])
        layout.addWidget(splitter, 1)
        self.message = QLabel("選擇範本後，可先檢查日期、時間與文件名稱。", self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.insert_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.insert_button.setText("插入範本")
        self.insert_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for path in ordered:
            try:
                label = path.relative_to(self._folder).as_posix() if self._folder else path.name
            except ValueError:
                label = str(path)
            item = QListWidgetItem(("最近｜" if path in recent_paths else "") + label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(str(path))
            self.templates.addItem(item)
        self.templates.currentItemChanged.connect(self._load_selection)
        self.search.textChanged.connect(self._filter_templates)
        self.setStyleSheet(f"""
QDialog {{ background: {theme.window}; color: {theme.text}; }}
QTextBrowser, QPlainTextEdit, QListWidget, QLineEdit {{
    background: {theme.surface_alt}; color: {theme.text};
    border: 1px solid {theme.border}; border-radius: 4px;
    selection-background-color: {theme.accent_soft};
}}
QLabel {{ color: {theme.text_muted}; }}
""")
        if self.templates.count():
            self.templates.setCurrentRow(0)
        else:
            self.message.setText("目前沒有可用的範本。")

    def selected_template(self) -> Path | None:
        return self._selected_path

    def rendered_text(self) -> str | None:
        return self._rendered

    def _filter_templates(self, text: str) -> None:
        query = text.strip().casefold()
        first = None
        for index in range(self.templates.count()):
            item = self.templates.item(index)
            hidden = query not in item.text().casefold()
            item.setHidden(hidden)
            if not hidden and first is None:
                first = item
        current = self.templates.currentItem()
        if current is None or current.isHidden():
            self.templates.setCurrentItem(first)
        if first is None:
            self._load_selection(None)
            self.message.setText("找不到符合名稱的範本。")

    def _load_selection(self, item, _previous=None) -> None:
        self._selected_path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        self._rendered = None
        self.insert_button.setEnabled(False)
        self.preview.clear()
        self.source.clear()
        if self._selected_path is None:
            return
        try:
            rendered = render_template_file(self._selected_path, self._title, self._now)
        except (OSError, UnicodeError) as exc:
            self.message.setText(f"無法讀取範本：{exc}")
            return
        self._rendered = rendered
        self.preview.document().setBaseUrl(
            QUrl.fromLocalFile(str(self._selected_path.parent) + "/")
        )
        self.preview.setMarkdown(rendered)
        self.source.setPlainText(rendered)
        self.insert_button.setEnabled(bool(rendered))
        self.message.setText(
            "插入內容將與此處的 Markdown 內容一致。" if rendered else "此範本沒有內容。"
        )

    def accept(self) -> None:
        if self._selected_path is None or not self.insert_button.isEnabled():
            return
        try:
            current = render_template_file(self._selected_path, self._title, self._now)
        except (OSError, UnicodeError):
            self._load_selection(self.templates.currentItem())
            return
        if current != self._rendered:
            self._load_selection(self.templates.currentItem())
            self.message.setText("範本已變更，預覽已更新；檢查後再按「插入範本」。")
            return
        super().accept()
