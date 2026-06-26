"""Main application window with toolbar, side panel, and renderer workspace."""

import json
from pathlib import Path

from PyQt6.QtCore import (
    QMarginsF,
    QProcess,
    QSettings,
    QSize,
    QSizeF,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QPageLayout,
    QPageSize,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .annotations import Annotation, AnnotationStore, DocumentAnnotations
from .editor import EditorView
from .left_panel import LeftPanel
from .md_converter import read_text
from .renderer import RendererView
from .theme import (
    HIT_TARGET,
    PANEL_WIDTH,
    TOOLBAR_HEIGHT,
    ThemeName,
    app_stylesheet,
    get_theme,
    svg_icon,
    toolbar_stylesheet,
)
from .tag_index import TagIndex
from .updater import UpdateInfo, check_for_update, download_installer
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"

# PDF export page sizes (key -> QPageSize id) plus a "single" long-page mode.
_PDF_PAGE_SIZES = {
    "A4": QPageSize.PageSizeId.A4,
    "A3": QPageSize.PageSizeId.A3,
    "Letter": QPageSize.PageSizeId.Letter,
    "Legal": QPageSize.PageSizeId.Legal,
}
_PDF_SIZE_CHOICES = [
    ("A4", "A4"),
    ("A3", "A3"),
    ("Letter", "Letter（美規信紙）"),
    ("Legal", "Legal（美規法律）"),
    ("single", "單一長頁（不分頁）"),
]
# PDF pages cannot exceed ~200 inches; stay safely under that limit (points).
_PDF_MAX_PT = 14000.0
_PT_PER_PX = 72.0 / 96.0


class UpdateCheckThread(QThread):
    finished_check = pyqtSignal(object, object)

    def run(self):
        try:
            self.finished_check.emit(check_for_update(), None)
        except Exception as exc:
            self.finished_check.emit(None, exc)


class UpdateDownloadThread(QThread):
    finished_download = pyqtSignal(object, object)

    def __init__(self, update: UpdateInfo, parent=None):
        super().__init__(parent)
        self._update = update

    def run(self):
        try:
            self.finished_download.emit(download_installer(self._update), None)
        except Exception as exc:
            self.finished_download.emit(None, exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        settings = QSettings(_ORG, _APP)
        self._theme_name: ThemeName = settings.value("theme", "light") or "light"
        if self._theme_name != "dark":
            self._theme_name = "light"
        side_notes_value = settings.value("annotation_side_notes_visible", False)
        self._side_notes_visible = (
            side_notes_value
            if isinstance(side_notes_value, bool)
            else str(side_notes_value).lower() in ("1", "true", "yes", "on")
        )
        self._theme = get_theme(self._theme_name)
        self._current_file: Path | None = None

        self.setWindowTitle("Markdown Viewer")
        self._restore_geometry()
        self._sidebar_open = True
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._pdf_progress = None
        self._pending_pdf_path = None

        self._tag_index = TagIndex()
        self._doc_annotations = DocumentAnnotations()
        annotation_callbacks = {
            "note_changed": self._annot_note_changed,
            "color_changed": self._annot_color_changed,
            "tags_changed": self._annot_tags_changed,
            "deleted": self._annot_deleted,
            "doc_tags_changed": self._annot_doc_tags_changed,
            "selected": self._annot_selected,
            "activated": self._annot_activated,
            "tag_index": self._tag_index,
        }

        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
            annotation_callbacks=annotation_callbacks,
            theme=self._theme,
        )
        self._renderer = RendererView(
            on_headings_ready=self._panel.toc.update_headings
        )
        self._renderer.set_annotation_side_notes_visible(self._side_notes_visible)
        self._renderer.active_anchor_changed.connect(
            self._panel.toc.set_active_anchor
        )
        self._renderer.bridge.added.connect(self._on_bridge_added)
        self._renderer.bridge.changed.connect(self._on_bridge_changed)
        self._renderer.bridge.removed.connect(self._on_bridge_removed)
        self._renderer.bridge.clicked.connect(self._on_bridge_clicked)
        self._renderer.bridge.orphansReported.connect(self._on_bridge_orphans)
        self._panel.close_btn.clicked.connect(self._toggle_sidebar)

        self._edit_mode = False
        self._editing_encoding = "utf-8"
        self._editing_newline = "\n"
        self._editor = EditorView()
        self._editor.modified_changed.connect(self._on_editor_modified)

        self._search_bar = self._build_search_bar()
        self._search_bar.hide()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._renderer)
        self._stack.addWidget(self._editor)

        renderer_wrap = QWidget()
        renderer_wrap.setObjectName("rendererWorkspace")
        renderer_layout = QVBoxLayout(renderer_wrap)
        renderer_layout.setContentsMargins(0, 0, 0, 0)
        renderer_layout.setSpacing(0)
        renderer_layout.addWidget(self._search_bar)
        renderer_layout.addWidget(self._stack)

        self._restore_btn = QPushButton(self._renderer)
        self._restore_btn.setFixedSize(HIT_TARGET, HIT_TARGET)
        self._restore_btn.setIconSize(QSize(20, 20))
        self._restore_btn.setToolTip("展開側邊欄")
        self._restore_btn.setAccessibleName("展開側邊欄")
        self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restore_btn.clicked.connect(self._toggle_sidebar)
        self._restore_btn.move(8, 8)
        self._restore_btn.hide()

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(self._panel)
        self._splitter.addWidget(renderer_wrap)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([PANEL_WIDTH, 960])
        self._splitter.setHandleWidth(4)

        self._toolbar = self._build_toolbar()
        self._reload_btn.setEnabled(False)
        self._edit_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._toolbar)
        root_layout.addWidget(self._splitter, stretch=1)
        self.setCentralWidget(root)
        self.setAcceptDrops(True)

        QShortcut(QKeySequence("Ctrl+O"), self).activated.connect(
            self._panel_open_file
        )
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._toggle_search
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            self._close_search
        )
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(
            self._toggle_edit_mode
        )
        QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(
            self._save_edits
        )
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(
            self._export_pdf
        )

        self._apply_theme()
        QTimer.singleShot(2000, self._check_updates_silent)

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setObjectName("topToolbar")
        toolbar.setFixedHeight(TOOLBAR_HEIGHT)

        layout = QHBoxLayout(toolbar)
        layout.setContentsMargins(8, 0, 10, 0)
        layout.setSpacing(4)

        self._sidebar_btn = self._toolbar_button(
            "panel-left", "收合側邊欄", self._toggle_sidebar
        )
        self._open_btn = self._toolbar_button(
            "file-text", "開啟單一 Markdown 文件", self._panel_open_file
        )
        self._search_btn = self._toolbar_button(
            "search", "搜尋目前文件", self._toggle_search
        )
        self._reload_btn = self._toolbar_button(
            "refresh", "重新載入文件", self._reload_current
        )
        self._edit_btn = self._toolbar_button(
            "pencil", "編輯文件 (Ctrl+E)", self._toggle_edit_mode
        )
        self._export_btn = self._toolbar_button(
            "file-down", "匯出 PDF", self._export_pdf
        )
        self._side_notes_btn = self._toolbar_button(
            "panel-right", "顯示旁註卡片", self._toggle_annotation_side_notes
        )
        self._side_notes_btn.setCheckable(True)
        self._side_notes_btn.setChecked(self._side_notes_visible)
        self._theme_btn = self._toolbar_button(
            "moon", "切換深色模式", self._toggle_theme
        )
        self._update_btn = self._toolbar_button(
            "circle-arrow-up",
            f"檢查更新（目前 v{VERSION}）",
            lambda: self._check_for_updates(manual=True),
        )

        title_wrap = QWidget()
        title_layout = QVBoxLayout(title_wrap)
        title_layout.setContentsMargins(10, 0, 10, 0)
        title_layout.setSpacing(0)

        self._toolbar_title = QLabel("Markdown Viewer")
        self._toolbar_title.setObjectName("toolbarTitle")
        self._toolbar_subtitle = QLabel("尚未載入文件")
        self._toolbar_subtitle.setObjectName("toolbarSubtitle")

        title_layout.addStretch()
        title_layout.addWidget(self._toolbar_title)
        title_layout.addWidget(self._toolbar_subtitle)
        title_layout.addStretch()

        layout.addWidget(self._sidebar_btn)
        layout.addWidget(self._open_btn)
        layout.addWidget(self._search_btn)
        layout.addWidget(self._reload_btn)
        layout.addWidget(self._edit_btn)
        layout.addWidget(self._export_btn)
        layout.addWidget(self._side_notes_btn)
        layout.addWidget(title_wrap, stretch=1)
        layout.addWidget(self._theme_btn)
        layout.addWidget(self._update_btn)
        return toolbar

    def _toolbar_button(self, icon_name: str, tooltip: str, callback) -> QPushButton:
        button = QPushButton()
        button.setProperty("iconName", icon_name)
        button.setFixedSize(HIT_TARGET, HIT_TARGET)
        button.setIconSize(QSize(20, 20))
        button.setIcon(svg_icon(icon_name, self._theme.text_muted))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _apply_theme(self):
        self._theme = get_theme(self._theme_name)
        self.setStyleSheet(app_stylesheet(self._theme))
        self._toolbar.setStyleSheet(
            toolbar_stylesheet(self._theme)
            + f"""
QLabel#toolbarTitle {{
    background: transparent;
    color: {self._theme.text};
    font-size: 14px;
    font-weight: 600;
}}
QLabel#toolbarSubtitle {{
    background: transparent;
    color: {self._theme.text_muted};
    font-size: 12px;
}}
"""
        )
        self._search_bar.setStyleSheet(self._search_style())
        self._panel.apply_theme(self._theme)
        self._splitter.setStyleSheet(
            f"""
QSplitter::handle {{
    background: {self._theme.border};
}}
QSplitter::handle:hover {{
    background: {self._theme.surface_hover};
}}
"""
        )
        self._restore_btn.setStyleSheet(
            toolbar_stylesheet(self._theme)
            + f"""
QPushButton {{
    background: {self._theme.surface};
    border: 1px solid {self._theme.border};
    border-radius: 8px;
}}
QPushButton:hover {{
    background: {self._theme.surface_hover};
    border-color: {self._theme.accent};
}}
QPushButton:pressed {{
    background: {self._theme.surface_active};
}}
"""
        )
        self._editor.apply_theme(self._theme)
        self._refresh_icons()
        self._renderer.set_theme(self._theme_name)

    def _refresh_icons(self):
        icon_color = self._theme.text_muted
        disabled_color = self._theme.text_subtle
        for button in (
            self._sidebar_btn,
            self._open_btn,
            self._search_btn,
            self._reload_btn,
            self._export_btn,
            self._update_btn,
        ):
            icon_name = button.property("iconName")
            color = icon_color if button.isEnabled() else disabled_color
            button.setIcon(svg_icon(icon_name, color, 20))

        side_notes_tip = (
            "隱藏旁註卡片" if self._side_notes_visible else "顯示旁註卡片"
        )
        side_notes_color = (
            self._theme.accent if self._side_notes_visible else icon_color
        )
        self._side_notes_btn.setChecked(self._side_notes_visible)
        self._side_notes_btn.setToolTip(side_notes_tip)
        self._side_notes_btn.setAccessibleName(side_notes_tip)
        self._side_notes_btn.setIcon(svg_icon("panel-right", side_notes_color, 20))

        edit_icon = "eye" if self._edit_mode else "pencil"
        edit_tip = "回到預覽 (Ctrl+E)" if self._edit_mode else "編輯文件 (Ctrl+E)"
        edit_color = icon_color if self._edit_btn.isEnabled() else disabled_color
        self._edit_btn.setProperty("iconName", edit_icon)
        self._edit_btn.setToolTip(edit_tip)
        self._edit_btn.setAccessibleName(edit_tip)
        self._edit_btn.setIcon(svg_icon(edit_icon, edit_color, 20))

        theme_icon = "sun" if self._theme_name == "dark" else "moon"
        theme_tip = "切換淺色模式" if self._theme_name == "dark" else "切換深色模式"
        self._theme_btn.setProperty("iconName", theme_icon)
        self._theme_btn.setToolTip(theme_tip)
        self._theme_btn.setAccessibleName(theme_tip)
        self._theme_btn.setIcon(svg_icon(theme_icon, icon_color, 20))

        restore_icon = "panel-left"
        self._restore_btn.setIcon(svg_icon(restore_icon, icon_color, 20))

        self._search_prev_btn.setIcon(svg_icon("chevron-left", icon_color, 18))
        self._search_next_btn.setIcon(svg_icon("chevron-right", icon_color, 18))
        self._search_close_btn.setIcon(svg_icon("x", icon_color, 18))

    def _toggle_theme(self):
        self._theme_name = "light" if self._theme_name == "dark" else "dark"
        QSettings(_ORG, _APP).setValue("theme", self._theme_name)
        self._apply_theme()

    def _toggle_annotation_side_notes(self, checked=None):
        self._side_notes_visible = (
            bool(checked) if checked is not None else self._side_notes_btn.isChecked()
        )
        QSettings(_ORG, _APP).setValue(
            "annotation_side_notes_visible", self._side_notes_visible
        )
        self._renderer.set_annotation_side_notes_visible(self._side_notes_visible)
        self._refresh_icons()

    def _search_style(self) -> str:
        return f"""
QWidget#searchBar {{
    background: {self._theme.window};
    border-bottom: 1px solid {self._theme.border};
}}
QWidget#searchBar QWidget {{
    background: transparent;
}}
QWidget#searchBar QLineEdit {{
    background: {self._theme.surface};
    border: 1px solid {self._theme.border};
    border-radius: 6px;
    color: {self._theme.text};
    min-height: 30px;
    padding: 2px 10px;
    selection-background-color: {self._theme.accent_soft};
    selection-color: {self._theme.text};
}}
QWidget#searchBar QLineEdit:hover {{
    border-color: {self._theme.accent};
}}
QWidget#searchBar QLineEdit:focus {{
    border-color: {self._theme.accent};
    background: {self._theme.surface};
}}
QWidget#searchBar QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {self._theme.text_muted};
    min-width: 36px;
    min-height: 36px;
    padding: 0;
}}
QWidget#searchBar QPushButton:hover {{
    background: {self._theme.surface_hover};
    border-color: {self._theme.surface_hover};
    color: {self._theme.text};
}}
QWidget#searchBar QPushButton:focus {{
    border-color: {self._theme.accent};
}}
QWidget#searchBar QPushButton:pressed {{
    background: {self._theme.surface_active};
    border-color: {self._theme.accent};
}}
QWidget#searchBar QLabel {{
    background: transparent;
    color: {self._theme.text_muted};
    font-size: 12px;
    padding: 0 4px;
}}
"""

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("searchBar")
        bar.setFixedHeight(48)

        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(4)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜尋目前文件")
        self._search_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)

        self._search_count = QLabel("")

        self._search_prev_btn = self._search_button(
            "上一個結果 (Shift+Enter)", self._search_prev
        )
        self._search_next_btn = self._search_button(
            "下一個結果 (Enter)", self._search_next
        )
        self._search_close_btn = self._search_button(
            "關閉搜尋 (Esc)", self._close_search
        )

        self._search_prev_btn.setIcon(
            svg_icon("chevron-left", self._theme.text_muted, 18)
        )
        self._search_next_btn.setIcon(
            svg_icon("chevron-right", self._theme.text_muted, 18)
        )
        self._search_close_btn.setIcon(svg_icon("x", self._theme.text_muted, 18))

        layout.addWidget(self._search_input)
        layout.addWidget(self._search_count)
        layout.addWidget(self._search_prev_btn)
        layout.addWidget(self._search_next_btn)
        layout.addWidget(self._search_close_btn)
        return bar

    def _search_button(self, tooltip: str, callback) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(36, 36)
        button.setIconSize(QSize(18, 18))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        return button

    def _toggle_search(self):
        if self._edit_mode:
            return
        if self._search_bar.isHidden():
            self._search_bar.show()
            self._search_input.setFocus()
            self._search_input.selectAll()
        else:
            self._close_search()

    def _close_search(self):
        self._search_bar.hide()
        self._search_input.clear()
        self._renderer.find_text("")
        self._renderer.setFocus()

    def _on_search_text_changed(self, text: str):
        if not text:
            self._search_count.setText("")
            self._renderer.find_text("")
            return

        self._search_count.setText("正在搜尋...")
        self._renderer.find_text(
            text,
            lambda found, needle=text: self._on_search_result(needle, found),
        )

    def _on_search_result(self, needle: str, result):
        if needle != self._search_input.text():
            return
        found = (
            result.numberOfMatches() > 0
            if hasattr(result, "numberOfMatches")
            else bool(result)
        )
        self._search_count.setText("" if found else "找不到結果")

    def _search_next(self):
        self._renderer.find_next(self._search_input.text())

    def _search_prev(self):
        self._renderer.find_prev(self._search_input.text())

    def _toggle_sidebar(self):
        self._renderer.page().runJavaScript("window.scrollY", self._do_toggle)

    def _do_toggle(self, scroll_y: float):
        scroll_y = int(scroll_y or 0)
        self._sidebar_open = not self._sidebar_open
        width = max(self._splitter.width(), PANEL_WIDTH)

        if self._sidebar_open:
            self._panel.show()
            self._splitter.setSizes([PANEL_WIDTH, max(width - PANEL_WIDTH, 1)])
            self._restore_btn.hide()
            self._sidebar_btn.setToolTip("收合側邊欄")
            self._sidebar_btn.setAccessibleName("收合側邊欄")
        else:
            self._panel.hide()
            self._splitter.setSizes([0, width])
            self._restore_btn.show()
            self._restore_btn.raise_()
            self._sidebar_btn.setToolTip("展開側邊欄")
            self._sidebar_btn.setAccessibleName("展開側邊欄")

        QTimer.singleShot(
            50,
            lambda: self._renderer.page().runJavaScript(
                f"window.scrollTo(0, {scroll_y})"
            ),
        )

    def _toggle_edit_mode(self):
        if not self._current_file:
            return
        if self._edit_mode:
            self._exit_edit_mode()
        else:
            self._enter_edit_mode()

    def _enter_edit_mode(self):
        try:
            raw = self._current_file.read_bytes()
            result = read_text(self._current_file)
        except OSError as exc:
            QMessageBox.warning(self, "無法編輯", f"無法讀取檔案：\n{exc}")
            return
        if result is None:
            QMessageBox.warning(
                self,
                "無法編輯",
                "無法讀取檔案編碼，請使用 UTF-8、Big5 或 GBK。",
            )
            return

        text, encoding = result
        self._editing_encoding = encoding
        self._editing_newline = "\r\n" if b"\r\n" in raw else "\n"
        self._editor.set_content(text)

        self._edit_mode = True
        self._close_search()
        self._search_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._stack.setCurrentWidget(self._editor)
        self._editor.setFocus()
        self._refresh_icons()
        self._update_dirty_ui()

    def _exit_edit_mode(self):
        if not self._confirm_discard_edits():
            return
        self._leave_edit_ui()

    def _leave_edit_ui(self):
        self._edit_mode = False
        self._search_btn.setEnabled(True)
        self._reload_btn.setEnabled(bool(self._current_file))
        self._export_btn.setEnabled(bool(self._current_file))
        self._stack.setCurrentWidget(self._renderer)
        self._renderer.setFocus()
        self._refresh_icons()
        self._update_dirty_ui()

    def _confirm_discard_edits(self) -> bool:
        """Return True when it is safe to leave the editor."""
        if not (self._edit_mode and self._editor.is_modified()):
            return True
        answer = QMessageBox.question(
            self,
            "未儲存的變更",
            f"{self._current_file.name} 有未儲存的變更，要儲存嗎？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_edits()
        return answer == QMessageBox.StandardButton.Discard

    def _save_edits(self) -> bool:
        if not (self._edit_mode and self._current_file):
            return False

        text = self._editor.toPlainText()
        if self._editing_newline != "\n":
            text = text.replace("\n", self._editing_newline)

        encoding = self._editing_encoding
        try:
            data = text.encode(encoding)
        except UnicodeEncodeError:
            encoding = "utf-8"
            data = text.encode(encoding)

        try:
            self._current_file.write_bytes(data)
        except OSError as exc:
            QMessageBox.warning(self, "儲存失敗", f"無法寫入檔案：\n{exc}")
            return False

        if encoding != self._editing_encoding:
            self._editing_encoding = encoding
            self.statusBar().showMessage(
                "內容含原編碼無法表示的字元，已改用 UTF-8 儲存", 6000
            )
        else:
            self.statusBar().showMessage("已儲存", 3000)

        self._editor.mark_saved()
        self._renderer.load_file(self._current_file)
        self._update_dirty_ui()
        return True

    def _on_editor_modified(self, _modified: bool):
        self._update_dirty_ui()

    def _update_dirty_ui(self):
        if not self._current_file:
            return
        name = self._current_file.name
        dirty = self._edit_mode and self._editor.is_modified()
        marker = "● " if dirty else ""
        self.setWindowTitle(f"{marker}{name} - Markdown Viewer")
        self._toolbar_title.setText(f"{marker}{name}")
        self._toolbar_subtitle.setText(
            "未儲存變更" if dirty else str(self._current_file.parent)
        )

    def _open_file(self, filepath: str):
        path = Path(filepath)
        if self._edit_mode:
            if not self._confirm_discard_edits():
                return
            self._leave_edit_ui()
        self._current_file = path
        self.setWindowTitle(f"{path.name} - Markdown Viewer")
        self._toolbar_title.setText(path.name)
        self._toolbar_subtitle.setText(str(path.parent))
        self._doc_annotations = AnnotationStore.load(path)
        self._sync_renderer_annotations()
        self._panel.annotations.set_document(self._doc_annotations)
        self._renderer.load_file(path)
        self._panel.file_browser.navigate_to(path.parent)
        self._panel.file_browser.select_path(path)
        self._panel.recent.add(str(path))
        self._reload_btn.setEnabled(True)
        self._edit_btn.setEnabled(True)
        self._export_btn.setEnabled(True)
        self._refresh_icons()

    def open_path(self, filepath: str):
        self._open_file(filepath)

    def _panel_open_file(self):
        self._panel.open_file_dialog()

    def _reload_current(self):
        if not self._current_file:
            return
        self._renderer.reload_current()
        self.statusBar().showMessage("已重新載入文件", 3000)

    def _persist_annotations(self):
        if not self._current_file:
            return
        AnnotationStore.save(self._current_file, self._doc_annotations)
        self._tag_index.update(self._current_file, self._doc_annotations)
        self._panel.annotations.set_document(self._doc_annotations)
        self._sync_renderer_annotations()

    def _sync_renderer_annotations(self):
        self._renderer.set_annotations(
            [a.to_dict() for a in self._doc_annotations.annotations]
        )

    def _find_annotation(self, ann_id):
        for a in self._doc_annotations.annotations:
            if a.id == ann_id:
                return a
        return None

    # --- signals from the page (bridge) ---
    def _on_bridge_added(self, payload_json):
        if not self._current_file:
            return
        ann = Annotation.from_dict(json.loads(payload_json))
        self._doc_annotations.annotations.append(ann)
        self._persist_annotations()

    def _on_bridge_changed(self, ann_id, fields_json):
        a = self._find_annotation(ann_id)
        if not a:
            return
        fields = json.loads(fields_json)
        for key, value in fields.items():
            setattr(a, key, value)
        self._persist_annotations()

    def _on_bridge_removed(self, ann_id):
        self._doc_annotations.annotations = [
            a for a in self._doc_annotations.annotations if a.id != ann_id
        ]
        self._renderer.remove_annotation(ann_id)
        self._persist_annotations()

    def _on_bridge_clicked(self, ann_id):
        self._panel.switch_to(3)
        self._panel.annotations.select(ann_id)

    def _on_bridge_orphans(self, ids):
        # Orphans remain listed in the panel; no document marks to show.
        pass

    # --- callbacks from the annotations panel ---
    def _annot_note_changed(self, ann_id, text):
        a = self._find_annotation(ann_id)
        if a and a.note != text:
            a.note = text
            self._persist_annotations()

    def _annot_color_changed(self, ann_id, color):
        a = self._find_annotation(ann_id)
        if a:
            a.color = color
            self._renderer.update_annotation_color(ann_id, color)
            self._persist_annotations()

    def _annot_tags_changed(self, ann_id, tags):
        a = self._find_annotation(ann_id)
        if a and a.tags != tags:
            a.tags = tags
            self._persist_annotations()

    def _annot_deleted(self, ann_id):
        self._on_bridge_removed(ann_id)

    def _annot_doc_tags_changed(self, tags):
        self._doc_annotations.doc_tags = tags
        self._persist_annotations()

    def _annot_selected(self, ann_id):
        self._renderer.select_annotation(ann_id)

    def _annot_activated(self, ann_id):
        self._renderer.scroll_to_annotation(ann_id)

    def _export_pdf(self):
        if not self._current_file or self._edit_mode:
            return
        setup = self._ask_page_setup()
        if setup is None:
            return
        default = str(self._current_file.with_suffix(".pdf"))
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出 PDF", default, "PDF 檔案 (*.pdf)"
        )
        if not path:
            return

        self._export_btn.setEnabled(False)
        if setup["size"] == "single":
            self._pending_pdf_path = path
            self._renderer.content_size(self._export_single_page)
        else:
            layout = self._pdf_layout(setup["size"], setup["orientation"])
            self._show_pdf_progress()
            self._renderer.export_pdf(path, self._on_pdf_exported, layout)

    def _ask_page_setup(self):
        settings = QSettings(_ORG, _APP)
        last_size = settings.value("pdf_page_size", "A4") or "A4"
        last_orient = settings.value("pdf_orientation", "portrait") or "portrait"

        dialog = QDialog(self)
        dialog.setWindowTitle("匯出 PDF 設定")
        form = QFormLayout(dialog)

        size_combo = QComboBox(dialog)
        for key, label in _PDF_SIZE_CHOICES:
            size_combo.addItem(label, key)
        size_index = next(
            (i for i, (k, _) in enumerate(_PDF_SIZE_CHOICES) if k == last_size), 0
        )
        size_combo.setCurrentIndex(size_index)

        orient_combo = QComboBox(dialog)
        orient_combo.addItem("直向", "portrait")
        orient_combo.addItem("橫向", "landscape")
        orient_combo.setCurrentIndex(1 if last_orient == "landscape" else 0)

        def _sync_orientation():
            orient_combo.setEnabled(size_combo.currentData() != "single")

        size_combo.currentIndexChanged.connect(_sync_orientation)
        _sync_orientation()

        form.addRow("紙張大小", size_combo)
        form.addRow("方向", orient_combo)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("匯出")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        size_key = size_combo.currentData()
        orientation = orient_combo.currentData()
        settings.setValue("pdf_page_size", size_key)
        settings.setValue("pdf_orientation", orientation)
        return {"size": size_key, "orientation": orientation}

    def _pdf_layout(self, size_key: str, orientation: str) -> QPageLayout:
        size_id = _PDF_PAGE_SIZES.get(size_key, QPageSize.PageSizeId.A4)
        orient = (
            QPageLayout.Orientation.Landscape
            if orientation == "landscape"
            else QPageLayout.Orientation.Portrait
        )
        return QPageLayout(
            QPageSize(size_id),
            orient,
            QMarginsF(12, 12, 12, 12),
            QPageLayout.Unit.Millimeter,
        )

    def _export_single_page(self, dims):
        try:
            measured_w = float(dims[0])
            h_px = float(dims[1])
        except (TypeError, ValueError, IndexError):
            measured_w, h_px = 0.0, 1123.0

        # Base the page width on the actual viewport so the PDF mirrors the
        # on-screen layout; widen if the content itself overflows (wide tables).
        w_px = max(float(self._renderer.width()), measured_w)
        if w_px < 200:
            w_px = 800.0

        w_pt = w_px * _PT_PER_PX
        h_pt = (h_px + 4) * _PT_PER_PX

        if h_pt > _PDF_MAX_PT:
            reply = QMessageBox.question(
                self,
                "匯出 PDF",
                "文件內容過長，無法放進單一頁面（PDF 頁面高度上限約 508 公分）。\n"
                "要改用 A4 分頁匯出嗎？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._pending_pdf_path = None
                self._export_btn.setEnabled(
                    bool(self._current_file) and not self._edit_mode
                )
                self._refresh_icons()
                return
            layout = self._pdf_layout("A4", "portrait")
        else:
            layout = QPageLayout(
                QPageSize(
                    QSizeF(w_pt, h_pt),
                    QPageSize.Unit.Point,
                    "Continuous",
                    QPageSize.SizeMatchPolicy.ExactMatch,
                ),
                QPageLayout.Orientation.Portrait,
                QMarginsF(0, 0, 0, 0),
                QPageLayout.Unit.Point,
            )

        self._show_pdf_progress()
        self._renderer.export_pdf(self._pending_pdf_path, self._on_pdf_exported, layout)
        self._pending_pdf_path = None

    def _show_pdf_progress(self):
        self._pdf_progress = QProgressDialog("正在匯出 PDF…", None, 0, 0, self)
        self._pdf_progress.setWindowTitle("匯出 PDF")
        self._pdf_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._pdf_progress.setMinimumDuration(0)
        self._pdf_progress.setAutoClose(False)
        self._pdf_progress.setAutoReset(False)
        self._pdf_progress.show()

    def _close_pdf_progress(self):
        if self._pdf_progress is not None:
            self._pdf_progress.close()
            self._pdf_progress = None

    def _on_pdf_exported(self, path: str, ok: bool):
        self._close_pdf_progress()
        self._export_btn.setEnabled(bool(self._current_file) and not self._edit_mode)
        self._refresh_icons()
        if not ok:
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "匯出 PDF", "匯出失敗，請重試。")
            return

        self.statusBar().showMessage(f"已匯出 PDF：{path}", 5000)
        box = QMessageBox(self)
        box.setWindowTitle("匯出 PDF")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"已成功匯出：\n{path}")
        open_btn = box.addButton("開啟 PDF", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("關閉", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _scroll_to_anchor(self, anchor: str):
        self._renderer.scroll_to(anchor)

    def _check_updates_silent(self):
        self._check_for_updates(manual=False)

    def _check_for_updates(self, manual: bool):
        if self._update_check_thread and self._update_check_thread.isRunning():
            return

        if manual:
            self.statusBar().showMessage("正在檢查更新...")

        self._update_check_thread = UpdateCheckThread(self)
        self._update_check_thread.finished_check.connect(
            lambda update, error, is_manual=manual: self._on_update_check_done(
                update, error, is_manual
            )
        )
        self._update_check_thread.start()

    def _on_update_check_done(self, update, error, manual: bool):
        self.statusBar().clearMessage()

        if error:
            if manual:
                QMessageBox.warning(self, "更新檢查失敗", str(error))
            return

        if not update.has_update:
            if manual:
                QMessageBox.information(
                    self,
                    "目前已是最新版本",
                    f"Markdown Viewer 已是最新版本。\n目前版本：{VERSION}",
                )
            return

        answer = QMessageBox.question(
            self,
            "有可用更新",
            f"版本 {update.latest_version} 已可下載。\n\n"
            "是否要立即下載並安裝？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._download_update(update)

    def _download_update(self, update: UpdateInfo):
        if self._update_download_thread and self._update_download_thread.isRunning():
            return

        self._update_progress = QProgressDialog("正在下載更新...", None, 0, 0, self)
        self._update_progress.setWindowTitle("Markdown Viewer 更新")
        self._update_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._update_progress.setMinimumDuration(0)
        self._update_progress.show()

        self._update_download_thread = UpdateDownloadThread(update, self)
        self._update_download_thread.finished_download.connect(
            self._on_update_download_done
        )
        self._update_download_thread.start()

    def _on_update_download_done(self, installer_path, error):
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None

        if error:
            QMessageBox.warning(self, "更新下載失敗", str(error))
            return

        result = QProcess.startDetached(str(installer_path))
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            QMessageBox.warning(self, "更新失敗", "無法啟動安裝程式。")
            return

        QApplication.quit()

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            if any(
                u.toLocalFile().lower().endswith(".md")
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local.lower().endswith(".md"):
                self._open_file(local)
                break

    def _restore_geometry(self):
        settings = QSettings(_ORG, _APP)
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 750)

    def closeEvent(self, event):
        if not self._confirm_discard_edits():
            event.ignore()
            return
        QSettings(_ORG, _APP).setValue("geometry", self.saveGeometry())
        super().closeEvent(event)
