"""Main application window with toolbar, side panel, and renderer workspace."""

import json
import re
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import (
    QFileSystemWatcher,
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
    QAction,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QPageLayout,
    QPageSize,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QInputDialog,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from .annotations import Annotation, AnnotationStore, DocumentAnnotations
from .atomic_io import atomic_write_bytes
from .document_libraries import DocumentLibraryStore
from .editor import EditorView
from .file_types import document_kind, is_markdown, is_supported_document
from .left_panel import LeftPanel
from .links import LinkIndex, collect_markdown_files, read_docs
from .md_converter import (
    convert_text,
    front_matter_tags,
    parse_front_matter,
    read_text,
    set_user_css,
)
from .mermaid_blocks import (
    find_mermaid_blocks,
    insert_mermaid_block,
    replace_mermaid_block,
)
from .mermaid_templates import default_template
from .mermaid_workspace import MermaidWorkspaceDialog
from .pdf_notes import PdfNote, PdfNoteStore
from .pdf_highlights import DEFAULT_COLOR, PdfHighlight, PdfHighlightStore, Rect
from .pdf_view import PdfView
from .quick_open import QuickOpenDialog
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
_DETACHED_WINDOWS: set[QMainWindow] = set()

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


class LinkIndexThread(QThread):
    """Build the wiki-link index off the UI thread (reads many small files)."""

    ready = pyqtSignal(object)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self._roots = roots

    def run(self):
        try:
            docs = read_docs(collect_markdown_files(self._roots))
            index = LinkIndex()
            index.build(docs)
            self.ready.emit(index)
        except Exception:
            self.ready.emit(None)


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
        self._current_kind = ""
        # Open documents shown as tabs. The viewer (renderer / PDF view) is
        # shared and reloaded on switch; per-tab view state (markdown scroll;
        # PDF page already persists in pdf_last_pages) is kept here keyed by
        # path string. _active_path is the path currently loaded in the view.
        self._tab_state: dict[str, dict] = {}
        self._active_path: str | None = None
        self._tab_guard = False  # suppress currentChanged while we mutate tabs
        # Detached (tab moved out) windows must not persist their session on
        # close, or they would clobber the primary window's open_tabs/geometry.
        self._is_detached = False
        self._exporting = False  # reentrancy guard for long-running exports

        self.setWindowTitle("Markdown Viewer")
        self._restore_geometry()
        self._sidebar_open = True
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._pdf_progress = None
        self._pending_pdf_path = None

        # Detect when the open file is changed by another program (common with
        # the Drive/OneDrive/Dropbox folders this app targets) so a background
        # sync can't silently diverge from what's on screen.
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.fileChanged.connect(self._on_file_changed)
        self._loaded_signature: tuple[int, int] | None = None
        self._reload_prompt_open = False

        # Wiki-link index ([[note]] -> file, plus inverted backlinks).
        self._link_index = LinkIndex()
        self._link_thread: LinkIndexThread | None = None
        self._link_roots_key: tuple[str, ...] | None = None

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

        pdf_note_callbacks = {
            "add": self._pdf_add_note,
            "activated": self._pdf_note_activated,
            "edit": self._pdf_edit_note,
            "deleted": self._pdf_delete_note,
        }
        self._pdf_notes: list[PdfNote] = []

        pdf_highlight_callbacks = {
            "activated": self._pdf_highlight_activated,
            "recolor": self._pdf_highlight_recolor,
            "note": self._pdf_highlight_edit_note,
            "deleted": self._pdf_highlight_delete,
        }
        self._pdf_highlights: list[PdfHighlight] = []
        self._pen_mode = False

        self._current_front_tags: list[str] = []

        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
            annotation_callbacks=annotation_callbacks,
            pdf_note_callbacks=pdf_note_callbacks,
            pdf_highlight_callbacks=pdf_highlight_callbacks,
            on_tag_selected=self._on_tag_selected,
            theme=self._theme,
        )
        self._renderer = RendererView(
            on_headings_ready=self._panel.toc.update_headings
        )
        self._renderer.set_annotation_side_notes_visible(self._side_notes_visible)
        self._content_zoom = float(settings.value("content_zoom", 1.0) or 1.0)
        self._renderer.set_zoom(self._content_zoom)
        self._renderer.active_anchor_changed.connect(
            self._panel.toc.set_active_anchor
        )
        self._renderer.bridge.added.connect(self._on_bridge_added)
        self._renderer.bridge.changed.connect(self._on_bridge_changed)
        self._renderer.bridge.removed.connect(self._on_bridge_removed)
        self._renderer.bridge.clicked.connect(self._on_bridge_clicked)
        self._renderer.bridge.orphansReported.connect(self._on_bridge_orphans)
        self._renderer.bridge.taskToggled.connect(self._on_task_toggled)
        self._renderer.wikilink_clicked.connect(self._on_wikilink_clicked)
        self._renderer.local_doc_clicked.connect(self._on_local_doc_clicked)
        self._panel.close_btn.clicked.connect(self._toggle_sidebar)

        self._edit_mode = False
        self._editing_encoding = "utf-8"
        self._editing_newline = "\n"
        self._editor = EditorView()
        self._editor.modified_changed.connect(self._on_editor_modified)

        # Edit mode is a split pane: editor on the left, a live preview on the
        # right, kept in sync as you type (debounced) and scroll.
        self._edit_preview = RendererView()
        self._edit_preview.set_zoom(self._content_zoom)
        self._edit_preview.wikilink_clicked.connect(self._on_wikilink_clicked)
        self._edit_preview.local_doc_clicked.connect(self._on_local_doc_clicked)

        self._editor_search_bar = self._build_editor_search_bar()
        self._editor_search_bar.hide()
        editor_pane = QWidget()
        editor_pane_layout = QVBoxLayout(editor_pane)
        editor_pane_layout.setContentsMargins(0, 0, 0, 0)
        editor_pane_layout.setSpacing(0)
        editor_pane_layout.addWidget(self._editor_search_bar)
        editor_pane_layout.addWidget(self._editor)

        self._editor_split = QSplitter(Qt.Orientation.Horizontal)
        self._editor_split.addWidget(editor_pane)
        self._editor_split.addWidget(self._edit_preview)
        self._editor_split.setStretchFactor(0, 1)
        self._editor_split.setStretchFactor(1, 1)
        self._editor_split.setSizes([480, 480])

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(250)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._editor.textChanged.connect(self._on_editor_text_changed)
        self._editor.verticalScrollBar().valueChanged.connect(
            self._sync_preview_scroll
        )

        self._search_bar = self._build_search_bar()
        self._search_bar.hide()

        # Native PDF viewer (outline + search + remembered page).
        self._pdf_view = PdfView()
        self._pdf_view.page_changed.connect(self._on_pdf_page_changed)
        self._pdf_view.search_count_changed.connect(self._on_pdf_search_count)
        self._pdf_view.highlight_requested.connect(self._on_pdf_highlight_requested)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._renderer)
        self._stack.addWidget(self._editor_split)
        self._stack.addWidget(self._pdf_view)

        # Tab strip for switching between open documents (one shared viewer).
        self._tab_bar = QTabBar()
        self._tab_bar.setObjectName("documentTabs")
        self._tab_bar.setTabsClosable(True)
        self._tab_bar.setMovable(True)
        self._tab_bar.setExpanding(False)
        self._tab_bar.setDrawBase(False)
        self._tab_bar.setUsesScrollButtons(True)
        self._tab_bar.setElideMode(Qt.TextElideMode.ElideRight)
        self._tab_bar.setVisible(False)
        self._tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabCloseRequested.connect(self._on_tab_close)
        self._tab_bar.customContextMenuRequested.connect(
            self._show_tab_context_menu
        )

        renderer_wrap = QWidget()
        renderer_wrap.setObjectName("rendererWorkspace")
        renderer_layout = QVBoxLayout(renderer_wrap)
        renderer_layout.setContentsMargins(0, 0, 0, 0)
        renderer_layout.setSpacing(0)
        renderer_layout.addWidget(self._tab_bar)
        renderer_layout.addWidget(self._search_bar)
        renderer_layout.addWidget(self._stack)
        self._workspace = renderer_wrap

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
        # Ctrl+P opens the fuzzy quick-open palette (VS Code / Obsidian muscle
        # memory); PDF export moves to Ctrl+Shift+P.
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(
            self._quick_open
        )
        QShortcut(QKeySequence("Ctrl+Shift+P"), self).activated.connect(
            self._export_pdf
        )
        QShortcut(QKeySequence("Ctrl+Shift+M"), self).activated.connect(
            self._open_mermaid_workspace
        )
        QShortcut(QKeySequence("Ctrl+="), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl++"), self).activated.connect(self._zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self).activated.connect(self._zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self).activated.connect(self._zoom_reset)
        # Tab navigation (browser / editor muscle memory).
        QShortcut(QKeySequence("Ctrl+W"), self).activated.connect(
            self._close_current_tab
        )
        QShortcut(QKeySequence("Ctrl+Tab"), self).activated.connect(self._next_tab)
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self).activated.connect(
            self._prev_tab
        )

        self._build_menu_bar()
        self._load_user_css()
        self._apply_theme()
        self._refresh_tags_panel()
        QTimer.singleShot(2000, self._check_updates_silent)

    def _build_menu_bar(self):
        # Shortcuts stay on the QShortcuts above; the menu shows them as hints
        # (text after \t) without re-registering, so there's no key conflict.
        bar = self.menuBar()

        def act(text, slot):
            action = QAction(text, self)
            action.triggered.connect(slot)
            return action

        file_menu = bar.addMenu("檔案(&F)")
        file_menu.addAction(act("開啟…\tCtrl+O", self._panel_open_file))
        file_menu.addAction(act("快速開啟…\tCtrl+P", self._quick_open))
        file_menu.addAction(act("重新載入", self._reload_current))
        file_menu.addSeparator()
        file_menu.addAction(act("匯出 PDF…\tCtrl+Shift+P", self._export_pdf))
        file_menu.addAction(act("匯出 PPT…", self._export_pptx))
        file_menu.addAction(act("匯出 Word…", self._export_docx))
        file_menu.addSeparator()
        file_menu.addAction(act("離開", self.close))

        edit_menu = bar.addMenu("編輯(&E)")
        edit_menu.addAction(act("切換編輯 / 預覽\tCtrl+E", self._toggle_edit_mode))
        edit_menu.addAction(act("儲存\tCtrl+S", self._save_edits))
        edit_menu.addAction(act("尋找 / 取代\tCtrl+F", self._toggle_search))

        view_menu = bar.addMenu("檢視(&V)")
        view_menu.addAction(act("切換側邊欄", self._toggle_sidebar))
        view_menu.addSeparator()
        view_menu.addAction(act("放大\tCtrl++", self._zoom_in))
        view_menu.addAction(act("縮小\tCtrl+-", self._zoom_out))
        view_menu.addAction(act("重設縮放\tCtrl+0", self._zoom_reset))
        view_menu.addSeparator()
        view_menu.addAction(act("下一個分頁\tCtrl+Tab", self._next_tab))
        view_menu.addAction(act("上一個分頁\tCtrl+Shift+Tab", self._prev_tab))
        view_menu.addAction(act("關閉分頁\tCtrl+W", self._close_current_tab))
        view_menu.addSeparator()
        view_menu.addAction(act("切換深色模式", self._toggle_theme))
        view_menu.addAction(act("顯示 / 隱藏旁註卡片", self._toggle_annotation_side_notes))

        tools_menu = bar.addMenu("工具(&T)")
        tools_menu.addAction(
            act("Mermaid 工作區...\tCtrl+Shift+M", self._open_mermaid_workspace)
        )
        tools_menu.addAction(act("編輯 Mermaid 圖表...", self._edit_mermaid_diagram))
        tools_menu.addAction(act("插入 Mermaid 圖表...", self._insert_mermaid_diagram))
        tools_menu.addSeparator()
        tools_menu.addAction(act("偏好設定…", self._open_preferences))

        help_menu = bar.addMenu("說明(&H)")
        help_menu.addAction(act("鍵盤快捷鍵…", self._show_shortcuts))
        help_menu.addAction(act("檢查更新…", lambda: self._check_for_updates(manual=True)))
        help_menu.addAction(act("關於 Markdown Viewer", self._show_about))

    def _show_about(self):
        QMessageBox.about(
            self,
            "關於 Markdown Viewer",
            f"<b>Markdown Viewer</b><br>版本 {VERSION}<br><br>"
            "Markdown 筆記閱讀 / 編輯與 PDF 閱讀工具。",
        )

    def _show_shortcuts(self):
        groups = [
            ("檔案", [
                ("Ctrl+O", "開啟文件"),
                ("Ctrl+P", "快速開啟（模糊搜尋檔名）"),
                ("Ctrl+Shift+P", "匯出 PDF"),
            ]),
            ("分頁", [
                ("Ctrl+Tab", "下一個分頁"),
                ("Ctrl+Shift+Tab", "上一個分頁"),
                ("Ctrl+W", "關閉目前分頁"),
            ]),
            ("編輯", [
                ("Ctrl+E", "切換編輯 / 預覽"),
                ("Ctrl+S", "儲存"),
                ("Ctrl+F", "在文件 / PDF 中搜尋"),
            ]),
            ("檢視", [
                ("Ctrl++ / Ctrl+- / Ctrl+0", "放大 / 縮小 / 重設縮放"),
            ]),
            ("PDF", [
                ("Ctrl+C", "複製選取的 PDF 文字"),
                ("H", "螢光標記目前 PDF 選取"),
            ]),
        ]
        parts = ["<table cellspacing='6' cellpadding='2'>"]
        for title, rows in groups:
            parts.append(
                f"<tr><td colspan='2' style='padding-top:10px;'><b>{title}</b></td></tr>"
            )
            for keys, action in rows:
                parts.append(
                    f"<tr><td style='padding-right:20px;'><code>{keys}</code></td>"
                    f"<td>{action}</td></tr>"
                )
        parts.append("</table>")
        box = QMessageBox(self)
        box.setWindowTitle("鍵盤快捷鍵")
        box.setIcon(QMessageBox.Icon.NoIcon)
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("".join(parts))
        box.exec()

    def _update_check_enabled(self) -> bool:
        value = QSettings(_ORG, _APP).value("update_check_enabled", True)
        if isinstance(value, bool):
            return value
        return str(value).lower() not in ("0", "false", "no", "off")

    def _load_user_css(self, reload: bool = False):
        path = QSettings(_ORG, _APP).value("custom_css_path", "") or ""
        css = ""
        if path:
            try:
                css = Path(path).read_text(encoding="utf-8")
            except OSError:
                css = ""
        set_user_css(css)
        if (
            reload
            and self._current_file
            and is_markdown(self._current_file)
            and not self._edit_mode
        ):
            self._renderer.reload_current()

    def _open_preferences(self):
        settings = QSettings(_ORG, _APP)
        dialog = QDialog(self)
        dialog.setWindowTitle("偏好設定")
        form = QFormLayout(dialog)

        zoom_combo = QComboBox(dialog)
        for pct in (80, 90, 100, 110, 125, 150, 175, 200):
            zoom_combo.addItem(f"{pct}%", pct / 100)
        current_pct = round(self._content_zoom * 100)
        zoom_index = next(
            (i for i in range(zoom_combo.count())
             if round(zoom_combo.itemData(i) * 100) == current_pct),
            2,
        )
        zoom_combo.setCurrentIndex(zoom_index)

        update_cb = QCheckBox("啟動時自動檢查更新（每日一次）", dialog)
        update_cb.setChecked(self._update_check_enabled())

        css_edit = QLineEdit(settings.value("custom_css_path", "") or "", dialog)
        css_edit.setPlaceholderText("選用的 .css 檔案路徑")
        browse_btn = QPushButton("瀏覽…", dialog)
        css_row = QWidget(dialog)
        css_layout = QHBoxLayout(css_row)
        css_layout.setContentsMargins(0, 0, 0, 0)
        css_layout.addWidget(css_edit, 1)
        css_layout.addWidget(browse_btn)

        def browse():
            path, _ = QFileDialog.getOpenFileName(
                dialog, "選擇 CSS 檔案", "", "CSS 樣式表 (*.css)"
            )
            if path:
                css_edit.setText(path)

        browse_btn.clicked.connect(browse)

        form.addRow("內容縮放", zoom_combo)
        form.addRow("", update_cb)
        form.addRow("自訂 CSS", css_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("確定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_zoom(zoom_combo.currentData())
        settings.setValue("update_check_enabled", update_cb.isChecked())
        settings.setValue("custom_css_path", css_edit.text().strip())
        self._load_user_css(reload=True)

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
            "file-text", "開啟 Markdown 或 PDF 文件", self._panel_open_file
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
        self._mermaid_btn = self._toolbar_button(
            "workflow", "Mermaid 工作區 (Ctrl+Shift+M)", self._open_mermaid_workspace
        )
        self._export_btn = self._toolbar_button(
            "file-down", "匯出 PDF", self._export_pdf
        )
        self._side_notes_btn = self._toolbar_button(
            "panel-right", "顯示旁註卡片", self._toggle_annotation_side_notes
        )
        self._side_notes_btn.setCheckable(True)
        self._side_notes_btn.setChecked(self._side_notes_visible)
        self._highlight_btn = self._toolbar_button(
            "highlighter", "螢光筆模式（在 PDF 拖曳選取即標記）", self._toggle_pen_mode
        )
        self._highlight_btn.setCheckable(True)
        self._highlight_btn.setEnabled(False)
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
        layout.addWidget(self._mermaid_btn)
        layout.addWidget(self._export_btn)
        layout.addWidget(self._side_notes_btn)
        layout.addWidget(self._highlight_btn)
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
        self._tab_bar.setStyleSheet(
            f"""
QTabBar#documentTabs {{
    background: {self._theme.window};
    border-bottom: 1px solid {self._theme.border};
}}
QTabBar#documentTabs::tab {{
    background: {self._theme.surface};
    color: {self._theme.text_muted};
    border: 1px solid {self._theme.border};
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 12px;
    margin-right: 2px;
    max-width: 240px;
}}
QTabBar#documentTabs::tab:selected {{
    background: {self._theme.surface_active};
    color: {self._theme.text};
}}
QTabBar#documentTabs::tab:hover {{
    background: {self._theme.surface_hover};
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
        self._editor.apply_theme(self._theme)
        self._editor_search_bar.setStyleSheet(self._editor_search_style())
        self._pdf_view.apply_theme(self._theme)
        self._refresh_icons()
        self._renderer.set_theme(self._theme_name)
        # The preview holds a throwaway HTML string (no _current_path), so the
        # renderer's in-place theme swap can't recolor it — re-render instead.
        if getattr(self, "_edit_mode", False):
            self._update_preview()

    def _refresh_icons(self):
        icon_color = self._theme.text_muted
        disabled_color = self._theme.text_subtle
        for button in (
            self._sidebar_btn,
            self._open_btn,
            self._search_btn,
            self._reload_btn,
            self._mermaid_btn,
            self._export_btn,
            self._update_btn,
        ):
            icon_name = button.property("iconName")
            color = icon_color if button.isEnabled() else disabled_color
            button.setIcon(svg_icon(icon_name, color, 20))

        side_notes_tip = (
            "隱藏旁註卡片" if self._side_notes_visible else "顯示旁註卡片"
        )
        if self._side_notes_btn.isEnabled():
            side_notes_color = (
                self._theme.accent if self._side_notes_visible else icon_color
            )
        else:
            side_notes_color = disabled_color
        self._side_notes_btn.setChecked(self._side_notes_visible)
        self._side_notes_btn.setToolTip(side_notes_tip)
        self._side_notes_btn.setAccessibleName(side_notes_tip)
        self._side_notes_btn.setIcon(svg_icon("panel-right", side_notes_color, 20))

        if not self._highlight_btn.isEnabled():
            highlight_color = disabled_color
        elif self._pen_mode:
            highlight_color = self._theme.accent
        else:
            highlight_color = icon_color
        self._highlight_btn.setChecked(self._pen_mode)
        self._highlight_btn.setIcon(svg_icon("highlighter", highlight_color, 20))

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
            self._toggle_editor_search()
            return
        if not self._current_file:
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
        self._search_count.setText("")
        self._renderer.find_text("")
        self._pdf_view.clear_search()
        self._editor_search_bar.hide()
        current = self._stack.currentWidget()
        if current in (self._renderer, self._pdf_view):
            current.setFocus()

    def _on_search_text_changed(self, text: str):
        if not text:
            self._search_count.setText("")
            self._renderer.find_text("")
            self._pdf_view.clear_search()
            return

        self._search_count.setText("正在搜尋...")
        if self._current_kind == "pdf":
            self._pdf_view.search(text)
            return
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

    # --- editor find / replace (edit mode) ---
    def _build_editor_search_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("editorSearchBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(10, 6, 10, 6)
        outer.setSpacing(4)

        find_row = QHBoxLayout()
        find_row.setSpacing(4)
        self._ed_find = QLineEdit()
        self._ed_find.setPlaceholderText("尋找")
        self._ed_find.textChanged.connect(lambda _t: self._update_editor_match_count())
        self._ed_find.returnPressed.connect(self._editor_find_next)
        self._ed_count = QLabel("")
        self._ed_case = QCheckBox("Aa")
        self._ed_case.setToolTip("區分大小寫")
        self._ed_case.stateChanged.connect(lambda _s: self._update_editor_match_count())
        prev_btn = QPushButton("‹")
        prev_btn.setToolTip("上一個")
        prev_btn.clicked.connect(self._editor_find_prev)
        next_btn = QPushButton("›")
        next_btn.setToolTip("下一個")
        next_btn.clicked.connect(self._editor_find_next)
        close_btn = QPushButton("✕")
        close_btn.setToolTip("關閉 (Esc)")
        close_btn.clicked.connect(self._close_editor_search)
        for btn in (prev_btn, next_btn, close_btn):
            btn.setFixedWidth(34)
        find_row.addWidget(self._ed_find, 1)
        find_row.addWidget(self._ed_count)
        find_row.addWidget(self._ed_case)
        find_row.addWidget(prev_btn)
        find_row.addWidget(next_btn)
        find_row.addWidget(close_btn)

        replace_row = QHBoxLayout()
        replace_row.setSpacing(4)
        self._ed_replace = QLineEdit()
        self._ed_replace.setPlaceholderText("取代為")
        self._ed_replace.returnPressed.connect(self._editor_replace_one)
        replace_btn = QPushButton("取代")
        replace_btn.clicked.connect(self._editor_replace_one)
        replace_all_btn = QPushButton("全部取代")
        replace_all_btn.clicked.connect(self._editor_replace_all)
        replace_row.addWidget(self._ed_replace, 1)
        replace_row.addWidget(replace_btn)
        replace_row.addWidget(replace_all_btn)

        outer.addLayout(find_row)
        outer.addLayout(replace_row)
        return bar

    def _editor_search_style(self) -> str:
        t = self._theme
        return f"""
QWidget#editorSearchBar {{ background: {t.window}; border-bottom: 1px solid {t.border}; }}
QWidget#editorSearchBar QLineEdit {{ background: {t.surface}; border: 1px solid {t.border};
    border-radius: 6px; color: {t.text}; min-height: 28px; padding: 2px 8px; }}
QWidget#editorSearchBar QLineEdit:focus {{ border-color: {t.accent}; }}
QWidget#editorSearchBar QPushButton {{ background: {t.surface}; border: 1px solid {t.border};
    border-radius: 6px; color: {t.text}; padding: 4px 10px; min-height: 28px; }}
QWidget#editorSearchBar QPushButton:hover {{ background: {t.surface_hover}; border-color: {t.accent}; }}
QWidget#editorSearchBar QCheckBox {{ color: {t.text_muted}; }}
QWidget#editorSearchBar QLabel {{ color: {t.text_muted}; font-size: 12px; padding: 0 4px; }}
"""

    def _toggle_editor_search(self):
        if self._editor_search_bar.isHidden():
            selected = self._editor.textCursor().selectedText()
            if selected and " " not in selected:
                self._ed_find.setText(selected)
            self._editor_search_bar.show()
            self._ed_find.setFocus()
            self._ed_find.selectAll()
            self._update_editor_match_count()
        else:
            self._close_editor_search()

    def _close_editor_search(self):
        self._editor_search_bar.hide()
        self._editor.setFocus()

    def _editor_find_flags(self):
        flags = QTextDocument.FindFlag(0)
        if self._ed_case.isChecked():
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        return flags

    def _editor_find(self, backward: bool = False) -> bool:
        text = self._ed_find.text()
        if not text:
            self._ed_count.setText("")
            return False
        flags = self._editor_find_flags()
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        found = self._editor.find(text, flags)
        if not found:
            cursor = self._editor.textCursor()
            cursor.movePosition(
                QTextCursor.MoveOperation.End if backward
                else QTextCursor.MoveOperation.Start
            )
            self._editor.setTextCursor(cursor)
            found = self._editor.find(text, flags)
        self._update_editor_match_count(found)
        return found

    def _editor_find_next(self):
        self._editor_find(False)

    def _editor_find_prev(self):
        self._editor_find(True)

    def _update_editor_match_count(self, found: bool | None = None):
        text = self._ed_find.text()
        if not text:
            self._ed_count.setText("")
            return
        doc = self._editor.toPlainText()
        if self._ed_case.isChecked():
            total = doc.count(text)
        else:
            total = doc.lower().count(text.lower())
        if total == 0:
            self._ed_count.setText("找不到")
        else:
            self._ed_count.setText(f"{total} 筆")

    def _editor_replace_one(self):
        find = self._ed_find.text()
        if not find:
            return
        cursor = self._editor.textCursor()
        selected = cursor.selectedText()
        case = self._ed_case.isChecked()
        matches = selected == find if case else selected.lower() == find.lower()
        if cursor.hasSelection() and matches:
            cursor.insertText(self._ed_replace.text())
        self._editor_find(False)

    def _editor_replace_all(self):
        find = self._ed_find.text()
        if not find:
            return
        replace = self._ed_replace.text()
        flags = self._editor_find_flags()
        doc = self._editor.document()
        edit_cursor = QTextCursor(doc)
        edit_cursor.beginEditBlock()
        count = 0
        match = doc.find(find, 0, flags)
        while not match.isNull():
            match.insertText(replace)
            count += 1
            match = doc.find(find, match.position(), flags)
        edit_cursor.endEditBlock()
        self._update_editor_match_count()
        self.statusBar().showMessage(f"已取代 {count} 筆", 2000)

    def _search_next(self):
        if self._current_kind == "pdf":
            self._pdf_view.search_next()
        else:
            self._renderer.find_next(self._search_input.text())

    def _search_prev(self):
        if self._current_kind == "pdf":
            self._pdf_view.search_prev()
        else:
            self._renderer.find_prev(self._search_input.text())

    def _on_pdf_page_changed(self, page0: int):
        if self._current_kind == "pdf":
            self._save_pdf_page(page0)
            self._panel.pdf_notes.set_current_page(page0)

    def _on_pdf_search_count(self, count: int):
        if self._current_kind != "pdf":
            return
        if not self._search_input.text():
            self._search_count.setText("")
        else:
            self._search_count.setText("" if count > 0 else "找不到結果")

    def _pdf_pages_map(self) -> dict:
        raw = QSettings(_ORG, _APP).value("pdf_last_pages")
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_pdf_page(self, page0: int):
        if not self._current_file:
            return
        pages = self._pdf_pages_map()
        pages[str(self._current_file)] = int(page0)
        if len(pages) > 200:
            for key in list(pages)[:-200]:
                del pages[key]
        QSettings(_ORG, _APP).setValue("pdf_last_pages", json.dumps(pages))

    def _toggle_sidebar(self):
        if self._stack.currentWidget() is self._renderer:
            self._renderer.page().runJavaScript("window.scrollY", self._do_toggle)
        else:
            self._do_toggle(0)

    def _do_toggle(self, scroll_y: float):
        scroll_y = int(scroll_y or 0)
        self._sidebar_open = not self._sidebar_open
        width = max(self._splitter.width(), PANEL_WIDTH)

        if self._sidebar_open:
            self._panel.show()
            self._splitter.setSizes([PANEL_WIDTH, max(width - PANEL_WIDTH, 1)])
            self._sidebar_btn.setToolTip("收合側邊欄")
            self._sidebar_btn.setAccessibleName("收合側邊欄")
        else:
            self._panel.hide()
            self._splitter.setSizes([0, width])
            self._sidebar_btn.setToolTip("展開側邊欄")
            self._sidebar_btn.setAccessibleName("展開側邊欄")

        if self._stack.currentWidget() is self._renderer:
            QTimer.singleShot(
                50,
                lambda: self._renderer.page().runJavaScript(
                    f"window.scrollTo(0, {scroll_y})"
                ),
            )

    def _toggle_edit_mode(self):
        if not self._current_file or not is_markdown(self._current_file):
            return
        if self._edit_mode:
            self._exit_edit_mode()
        else:
            self._enter_edit_mode()

    def _open_mermaid_workspace(self):
        dialog = MermaidWorkspaceDialog(theme_name=self._theme_name, parent=self)
        dialog.exec()

    def _edit_mermaid_diagram(self):
        if not self._ensure_markdown_edit_mode():
            return
        text = self._editor.toPlainText()
        blocks = find_mermaid_blocks(text)
        if not blocks:
            answer = QMessageBox.question(
                self,
                "Mermaid",
                "No Mermaid diagrams were found. Insert a new diagram?",
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._insert_mermaid_diagram()
            return

        block = self._choose_mermaid_block(blocks)
        if block is None:
            return

        dialog = MermaidWorkspaceDialog(
            block.source,
            self._theme_name,
            self,
            commit_label="更新 Markdown",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            new_text = replace_mermaid_block(text, block.id, dialog.source())
        except ValueError:
            QMessageBox.warning(
                self,
                "Mermaid",
                "找不到選取的圖表，請再試一次。",
            )
            return
        self._replace_editor_document(new_text, block.start_offset)
        self.statusBar().showMessage("Mermaid 圖表已更新。", 3000)

    def _insert_mermaid_diagram(self):
        if not self._ensure_markdown_edit_mode():
            return
        dialog = MermaidWorkspaceDialog(
            default_template().source,
            self._theme_name,
            self,
            commit_label="插入圖表",
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        pos = self._editor.textCursor().position()
        new_text = insert_mermaid_block(
            self._editor.toPlainText(), dialog.source(), position=pos
        )
        self._replace_editor_document(new_text, pos)
        self.statusBar().showMessage("Mermaid 圖表已插入。", 3000)

    def _ensure_markdown_edit_mode(self) -> bool:
        if not self._current_file or not is_markdown(self._current_file):
            QMessageBox.information(
                self,
                "Mermaid",
                "Open a Markdown file before editing Mermaid diagrams.",
            )
            return False
        if not self._edit_mode:
            self._enter_edit_mode()
        return self._edit_mode

    def _choose_mermaid_block(self, blocks):
        if len(blocks) == 1:
            return blocks[0]
        items = [
            f"{idx + 1}. {block.label} (lines {block.start_line + 1}-{block.end_line + 1})"
            for idx, block in enumerate(blocks)
        ]
        choice, ok = QInputDialog.getItem(
            self,
            "Mermaid",
            "Choose a diagram to edit:",
            items,
            0,
            False,
        )
        if not ok:
            return None
        return blocks[items.index(choice)]

    def _replace_editor_document(self, text: str, cursor_position: int | None = None):
        self._editor.setPlainText(text)
        if cursor_position is not None:
            cursor = self._editor.textCursor()
            cursor.setPosition(max(0, min(len(text), cursor_position)))
            self._editor.setTextCursor(cursor)
        self._editor.document().setModified(True)
        self._update_preview()
        self._update_dirty_ui()

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
        self._stack.setCurrentWidget(self._editor_split)
        self._update_preview()
        self._editor.setFocus()
        self._refresh_icons()
        self._update_dirty_ui()

    def _exit_edit_mode(self):
        if not self._confirm_discard_edits():
            return
        self._leave_edit_ui()

    def _leave_edit_ui(self):
        self._edit_mode = False
        self._editor_search_bar.hide()
        is_md = bool(self._current_file and is_markdown(self._current_file))
        self._search_btn.setEnabled(is_md)
        self._reload_btn.setEnabled(bool(self._current_file))
        self._export_btn.setEnabled(is_md)
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
            atomic_write_bytes(self._current_file, data)
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
        self._loaded_signature = self._file_signature(self._current_file)
        self._rearm_watch()
        self._renderer.reload_current()  # keep scroll position across the save
        self._update_dirty_ui()
        self._refresh_link_index(force=True)
        self._update_front_tags()
        return True

    def _on_editor_modified(self, _modified: bool):
        self._update_dirty_ui()

    def _on_editor_text_changed(self):
        if self._edit_mode:
            self._preview_timer.start()

    def _update_preview(self):
        if not self._edit_mode or not self._current_file:
            return
        text = self._editor.toPlainText()
        html, _ = convert_text(text, self._theme_name, title=self._current_file.stem)
        base = QUrl.fromLocalFile(str(self._current_file.parent) + "/")
        self._edit_preview.render_html(html, base)

    def _sync_preview_scroll(self):
        if not self._edit_mode:
            return
        bar = self._editor.verticalScrollBar()
        maximum = bar.maximum()
        ratio = (bar.value() / maximum) if maximum > 0 else 0.0
        self._edit_preview.scroll_to_ratio(ratio)

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
        idx = self._tab_bar.currentIndex()
        if idx >= 0:
            self._tab_bar.setTabText(idx, f"{marker}{name}")

    def _open_file(self, filepath: str):
        path = Path(filepath)
        kind = document_kind(path)
        if not kind:
            QMessageBox.warning(
                self,
                "不支援的檔案",
                "目前支援 Markdown（.md, .markdown）與 PDF（.pdf）檔案。",
            )
            return
        if self._edit_mode:
            if not self._confirm_discard_edits():
                return
            self._leave_edit_ui()
        key = str(path)
        existing = self._index_of_path(key)
        if existing >= 0:
            # Already open — just bring its tab to the front (load it if it is
            # the current-but-not-yet-loaded tab, e.g. right after a restore).
            if self._tab_bar.currentIndex() == existing:
                self._activate_tab(existing)
            else:
                self._tab_bar.setCurrentIndex(existing)
            return
        idx = self._add_tab(path, kind)
        self._tab_guard = True
        self._tab_bar.setCurrentIndex(idx)
        self._tab_guard = False
        self._activate_tab(idx)

    # ---------------- document tabs ----------------
    def _index_of_path(self, key: str) -> int:
        for i in range(self._tab_bar.count()):
            if self._tab_bar.tabData(i) == key:
                return i
        return -1

    def _add_tab(self, path: Path, kind: str) -> int:
        """Add a tab entry without loading it. Returns the new tab index."""
        key = str(path)
        self._tab_guard = True
        idx = self._tab_bar.addTab(path.name)
        self._tab_bar.setTabData(idx, key)
        self._tab_bar.setTabToolTip(idx, key)
        self._tab_guard = False
        self._tab_state[key] = {"kind": kind, "scroll": None}
        self._tab_bar.setVisible(self._tab_bar.count() > 0)
        return idx

    def _on_tab_changed(self, idx: int):
        if self._tab_guard or idx < 0:
            return
        key = self._tab_bar.tabData(idx)
        if not key or key == self._active_path:
            return
        # Switching documents while editing: confirm like opening a new file.
        if self._edit_mode and not self._confirm_discard_edits():
            self._tab_guard = True
            prev = self._index_of_path(self._active_path) if self._active_path else -1
            if prev >= 0:
                self._tab_bar.setCurrentIndex(prev)
            self._tab_guard = False
            return
        if self._edit_mode:
            self._leave_edit_ui()
        self._activate_tab(idx)

    def _on_tab_close(self, idx: int):
        key = self._tab_bar.tabData(idx)
        closing_active = key == self._active_path
        if closing_active and self._edit_mode:
            if not self._confirm_discard_edits():
                return
            self._leave_edit_ui()
        if closing_active:
            self._active_path = None  # don't save state for a closing document
        self._tab_guard = True
        self._tab_bar.removeTab(idx)
        self._tab_guard = False
        self._tab_state.pop(key, None)
        self._tab_bar.setVisible(self._tab_bar.count() > 0)
        if self._tab_bar.count() == 0:
            self._show_empty_state()
        elif closing_active:
            self._activate_tab(self._tab_bar.currentIndex())

    def _close_current_tab(self):
        if self._tab_bar.count() > 0:
            self._on_tab_close(self._tab_bar.currentIndex())

    def _show_tab_context_menu(self, pos):
        idx = self._tab_bar.tabAt(pos)
        if idx < 0:
            return
        menu = self._build_tab_context_menu(idx)
        menu.exec(self._tab_bar.mapToGlobal(pos))

    def _build_tab_context_menu(self, idx: int) -> QMenu:
        menu = QMenu(self._tab_bar)
        detach_action = menu.addAction("移至新視窗")
        detach_action.setEnabled(self._can_detach_tab(idx))
        detach_action.triggered.connect(
            lambda _checked=False, tab_index=idx: self._detach_tab(tab_index)
        )
        return menu

    def _can_detach_tab(self, idx: int) -> bool:
        return (
            self._tab_bar.count() > 1
            and 0 <= idx < self._tab_bar.count()
            and bool(self._tab_bar.tabData(idx))
        )

    def _detach_tab(self, idx: int):
        if not self._can_detach_tab(idx):
            return
        key = self._tab_bar.tabData(idx)
        path = Path(key)
        if key == self._active_path:
            if self._edit_mode:
                if not self._confirm_discard_edits():
                    return
                self._leave_edit_ui()
            self._save_active_view_state()
        state = dict(self._tab_state.get(key) or {})
        kind = state.get("kind") or document_kind(path)
        if not kind:
            return

        new_window = MainWindow()
        new_window._is_detached = True
        new_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        new_window.setWindowIcon(self.windowIcon())
        _DETACHED_WINDOWS.add(new_window)
        new_window.destroyed.connect(
            lambda _obj=None, window=new_window: _DETACHED_WINDOWS.discard(window)
        )
        new_window.open_path(key)
        if new_window._index_of_path(key) < 0:
            new_window.close()
            return
        new_window._tab_state[key] = {**new_window._tab_state.get(key, {}), **state}
        if new_window._active_path == key:
            new_window._load_document(path, kind)
        new_window.show()
        new_window.raise_()
        new_window.activateWindow()

        self._on_tab_close(idx)

    def _next_tab(self):
        self._step_tab(1)

    def _prev_tab(self):
        self._step_tab(-1)

    def _step_tab(self, delta: int):
        n = self._tab_bar.count()
        if n <= 1:
            return
        self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() + delta) % n)

    def _activate_tab(self, idx: int):
        """Load the document for tab *idx* into the shared viewer."""
        if idx < 0 or idx >= self._tab_bar.count():
            return
        key = self._tab_bar.tabData(idx)
        if not key or key == self._active_path:
            return
        self._save_active_view_state()
        self._active_path = key
        state = self._tab_state.get(key) or {}
        kind = state.get("kind") or document_kind(Path(key))
        self._load_document(Path(key), kind)

    def _save_active_view_state(self):
        """Capture the outgoing tab's view position before switching away."""
        if not self._active_path:
            return
        state = self._tab_state.get(self._active_path)
        if not state:
            return
        if state.get("kind") == "markdown":
            # Last value from the renderer's scroll poll (PDF page persists via
            # pdf_last_pages on page_changed, so nothing to capture for PDFs).
            state["scroll"] = self._renderer.scroll_y()

    def _show_empty_state(self):
        self._current_file = None
        self._current_kind = ""
        self._active_path = None
        self.setWindowTitle("Markdown Viewer")
        self._toolbar_title.setText("Markdown Viewer")
        self._toolbar_subtitle.setText("尚未載入文件")
        self._close_search()
        self._renderer.show_empty()
        self._stack.setCurrentWidget(self._renderer)
        self._panel.toc.update_outline([])
        self._panel.backlinks.clear()
        self._panel.annotations.set_document(None)
        self._panel.show_pdf_notes(False)
        self._reload_btn.setEnabled(False)
        self._search_btn.setEnabled(False)
        self._edit_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._side_notes_btn.setEnabled(False)
        self._highlight_btn.setEnabled(False)
        self._watch_current_file()
        self._refresh_icons()

    def _load_document(self, path: Path, kind: str):
        """Load *path* into the shared viewer, restoring its saved view state."""
        self._current_file = path
        self._current_kind = kind
        self.setWindowTitle(f"{path.name} - Markdown Viewer")
        self._toolbar_title.setText(path.name)
        self._toolbar_subtitle.setText(str(path.parent))
        self._close_search()
        self._current_front_tags = []
        if kind == "markdown":
            self._doc_annotations = AnnotationStore.load(path)
            self._sync_renderer_annotations()
            self._panel.annotations.set_document(self._doc_annotations)
            self._panel.show_pdf_notes(False)
            self._panel.set_annotations_enabled(True)
            self._update_front_tags()
            scroll = (self._tab_state.get(str(path)) or {}).get("scroll")
            self._renderer.load_file(path, scroll_y=scroll)
            self._stack.setCurrentWidget(self._renderer)
        else:
            self._doc_annotations = DocumentAnnotations()
            self._renderer.set_annotations([])
            self._panel.annotations.set_document(None)
            self._open_pdf(path)
        self._watch_current_file()
        self._panel.file_browser.navigate_to(path.parent)
        self._panel.file_browser.select_path(path)
        self._panel.recent.add(str(path))
        self._reload_btn.setEnabled(True)
        # Search now works for both Markdown and PDF.
        self._search_btn.setEnabled(True)
        self._edit_btn.setEnabled(kind == "markdown")
        self._export_btn.setEnabled(kind == "markdown")
        self._side_notes_btn.setEnabled(kind == "markdown")
        self._highlight_btn.setEnabled(kind == "pdf")
        if kind == "markdown":
            self._refresh_link_index()
        else:
            self._panel.backlinks.clear()
        self._refresh_icons()

    def _open_pdf(self, path: Path):
        # Switch first so the password prompt (if any) appears over the PDF view.
        self._stack.setCurrentWidget(self._pdf_view)
        if not self._pdf_view.load(path):
            if self._pdf_view.is_locked():
                self.statusBar().showMessage(
                    "已取消開啟受密碼保護的 PDF；重新開啟可再次輸入密碼。", 6000
                )
            else:
                self.statusBar().showMessage(
                    "無法開啟此 PDF：檔案可能已損毀或無法讀取。", 6000
                )
        # Outline -> sidebar TOC; clicking an entry jumps to its page.
        self._panel.toc.update_outline(self._pdf_view.outline())
        # Page-anchored notes + text highlights live in the "標註" tab.
        self._pdf_notes = PdfNoteStore.load(path)
        self._pdf_highlights = PdfHighlightStore.load(path)
        self._pdf_view.set_highlights(self._pdf_highlights)
        self._panel.show_pdf_notes(True)
        self._panel.set_annotations_enabled(True)
        self._refresh_pdf_notes_panel()
        self._refresh_pdf_highlights_panel()
        # Resume where the reader left off.
        page = self._pdf_pages_map().get(str(path), 0)
        self._pdf_view.restore_page(int(page))

    # --- PDF page notes ---
    def _refresh_pdf_notes_panel(self):
        self._panel.pdf_notes.set_notes(self._pdf_notes)
        self._panel.pdf_notes.set_current_page(self._pdf_view.current_page())

    def _save_pdf_notes(self):
        if self._current_file and self._current_kind == "pdf":
            try:
                PdfNoteStore.save(self._current_file, self._pdf_notes)
            except OSError as exc:
                self.statusBar().showMessage(f"無法儲存 PDF 註記：{exc}", 4000)

    def _pdf_add_note(self):
        if self._current_kind != "pdf" or not self._current_file:
            return
        page = self._pdf_view.current_page()
        text, ok = QInputDialog.getMultiLineText(
            self, "新增頁面註記", f"第 {page + 1} 頁的註記：", ""
        )
        if not ok:
            return
        self._pdf_notes.append(PdfNote.new(page=page, note=text.strip()))
        self._pdf_notes.sort(key=lambda n: (n.page, n.created))
        self._save_pdf_notes()
        self._refresh_pdf_notes_panel()

    def _find_pdf_note(self, note_id):
        return next((n for n in self._pdf_notes if n.id == note_id), None)

    def _pdf_note_activated(self, note_id):
        note = self._find_pdf_note(note_id)
        if note:
            self._pdf_view.jump_to_page(note.page)

    def _pdf_edit_note(self, note_id):
        note = self._find_pdf_note(note_id)
        if not note:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "編輯註記", f"第 {note.page + 1} 頁：", note.note
        )
        if not ok:
            return
        note.note = text.strip()
        note.updated = datetime.now().isoformat(timespec="seconds")
        self._save_pdf_notes()
        self._refresh_pdf_notes_panel()

    def _pdf_delete_note(self, note_id):
        self._pdf_notes = [n for n in self._pdf_notes if n.id != note_id]
        self._save_pdf_notes()
        self._refresh_pdf_notes_panel()

    # --- PDF text highlights ---
    def _toggle_pen_mode(self):
        self._pen_mode = not self._pen_mode
        self._pdf_view.set_pen_mode(self._pen_mode)
        self._refresh_icons()
        if self._pen_mode:
            self.statusBar().showMessage("螢光筆模式：拖曳選取文字即可標記", 3000)

    def _refresh_pdf_highlights_panel(self):
        self._panel.pdf_highlights.set_highlights(self._pdf_highlights)

    def _save_pdf_highlights(self):
        if self._current_file and self._current_kind == "pdf":
            try:
                PdfHighlightStore.save(self._current_file, self._pdf_highlights)
            except OSError as exc:
                self.statusBar().showMessage(f"無法儲存螢光標記：{exc}", 4000)

    def _on_pdf_highlight_requested(self, payload):
        if self._current_kind != "pdf" or not self._current_file:
            return
        rects = [Rect(x=x, y=y, w=w, h=h) for (x, y, w, h) in payload.get("rects", [])]
        if not rects:
            return
        highlight = PdfHighlight.new(
            page=int(payload.get("page", 0)),
            rects=rects,
            text=payload.get("text", ""),
            color=payload.get("color", DEFAULT_COLOR),
        )
        self._pdf_highlights.append(highlight)
        self._pdf_highlights.sort(key=lambda h: (h.page, h.created))
        self._save_pdf_highlights()
        self._pdf_view.set_highlights(self._pdf_highlights)
        self._refresh_pdf_highlights_panel()

    def _find_pdf_highlight(self, hid):
        return next((h for h in self._pdf_highlights if h.id == hid), None)

    def _pdf_highlight_activated(self, hid):
        highlight = self._find_pdf_highlight(hid)
        if not highlight:
            return
        if highlight.rects:
            r = highlight.rects[0]
            self._pdf_view.reveal(highlight.page, r.x, r.y, r.w, r.h)
        else:
            self._pdf_view.jump_to_page(highlight.page)

    def _pdf_highlight_recolor(self, hid, color):
        highlight = self._find_pdf_highlight(hid)
        if not highlight:
            return
        highlight.color = color
        highlight.updated = datetime.now().isoformat(timespec="seconds")
        self._pdf_view.set_pen_color(color)
        self._save_pdf_highlights()
        self._pdf_view.set_highlights(self._pdf_highlights)
        self._refresh_pdf_highlights_panel()

    def _pdf_highlight_edit_note(self, hid):
        highlight = self._find_pdf_highlight(hid)
        if not highlight:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "螢光標記備註", f"第 {highlight.page + 1} 頁：", highlight.note
        )
        if not ok:
            return
        highlight.note = text.strip()
        highlight.updated = datetime.now().isoformat(timespec="seconds")
        self._save_pdf_highlights()
        self._refresh_pdf_highlights_panel()

    def _pdf_highlight_delete(self, hid):
        self._pdf_highlights = [h for h in self._pdf_highlights if h.id != hid]
        self._save_pdf_highlights()
        self._pdf_view.set_highlights(self._pdf_highlights)
        self._refresh_pdf_highlights_panel()

    # --- wiki-links & backlinks ---
    def _link_roots(self) -> list[Path]:
        roots: list[Path] = []
        try:
            for lib in DocumentLibraryStore().load():
                p = Path(lib.path)
                if p.exists():
                    roots.append(p)
        except Exception:
            pass
        if self._current_file:
            roots.append(self._current_file.parent)
        seen: set[str] = set()
        unique: list[Path] = []
        for root in roots:
            key = str(root).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(root)
        return unique

    def _refresh_link_index(self, force: bool = False):
        roots = self._link_roots()
        key = tuple(sorted(str(r).casefold() for r in roots))
        if not force and key == self._link_roots_key:
            self._refresh_backlinks()
            return
        self._link_roots_key = key
        if self._link_thread is not None and self._link_thread.isRunning():
            self._refresh_backlinks()
            return
        self._link_thread = LinkIndexThread(roots, self)
        self._link_thread.ready.connect(self._on_link_index_ready)
        self._link_thread.start()
        self._refresh_backlinks()

    def _on_link_index_ready(self, index):
        if index is not None:
            self._link_index = index
        self._refresh_backlinks()

    def _refresh_backlinks(self):
        if self._current_file and self._current_kind == "markdown":
            self._panel.backlinks.set_backlinks(
                self._link_index.backlinks(self._current_file)
            )
        else:
            self._panel.backlinks.clear()

    def _on_local_doc_clicked(self, path: str):
        if path and Path(path).exists():
            self._open_file(path)

    def _on_wikilink_clicked(self, target: str):
        resolved = self._link_index.resolve(target, self._current_file)
        if not resolved or not Path(resolved).exists():
            resolved = self._resolve_in_current_folder(target)
        if resolved and Path(resolved).exists():
            self._open_file(str(resolved))
            return
        self._offer_create_note(target)

    def _resolve_in_current_folder(self, target: str) -> Path | None:
        if not self._current_file:
            return None
        name = target.split("#", 1)[0].strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower().endswith(".md"):
            name = name[:-3]
        if not name:
            return None
        folder = self._current_file.parent
        for ext in (".md", ".markdown"):
            candidate = folder / f"{name}{ext}"
            if candidate.exists():
                return candidate
        try:
            for entry in folder.iterdir():
                if (
                    entry.is_file()
                    and is_markdown(entry)
                    and entry.stem.casefold() == name.casefold()
                ):
                    return entry
        except OSError:
            pass
        return None

    def _offer_create_note(self, target: str):
        if not self._current_file:
            return
        name = target.split("#", 1)[0].strip().replace("\\", "/").rsplit("/", 1)[-1]
        if name.lower().endswith(".md"):
            name = name[:-3]
        if not name or any(ch in name for ch in '<>:"/\\|?*'):
            QMessageBox.information(self, "找不到筆記", f"找不到筆記「{target}」。")
            return
        answer = QMessageBox.question(
            self,
            "建立筆記",
            f"找不到筆記「{name}」，要在目前資料夾建立嗎？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        new_path = self._current_file.parent / f"{name}.md"
        if not new_path.exists():
            try:
                atomic_write_bytes(
                    new_path, f"# {name}\n".encode("utf-8"), backup=False
                )
            except OSError as exc:
                QMessageBox.warning(self, "建立失敗", f"無法建立檔案：\n{exc}")
                return
        self._open_file(str(new_path))
        self._refresh_link_index(force=True)

    def open_path(self, filepath: str):
        self._open_file(filepath)

    def _panel_open_file(self):
        self._panel.open_file_dialog()

    def _quick_open_candidates(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        candidates: list[tuple[str, str]] = []

        def add(path_str: str):
            path = Path(path_str)
            key = str(path).casefold()
            if key in seen or not is_supported_document(path) or not path.exists():
                return
            seen.add(key)
            candidates.append((path.name, str(path)))

        for path_str in self._panel.recent.paths():
            add(path_str)
        if self._current_file:
            try:
                for entry in sorted(self._current_file.parent.iterdir()):
                    if entry.is_file():
                        add(str(entry))
            except OSError:
                pass
        return candidates

    def _quick_open(self):
        candidates = self._quick_open_candidates()
        if not candidates:
            self.statusBar().showMessage(
                "沒有可快速開啟的檔案（最近清單與目前資料夾皆為空）", 4000
            )
            return
        dialog = QuickOpenDialog(candidates, self._theme, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.selected_path()
            if path:
                self._open_file(path)

    def _apply_zoom(self, factor: float):
        self._content_zoom = self._renderer.set_zoom(factor)
        self._edit_preview.set_zoom(self._content_zoom)
        if self._current_kind == "pdf":
            self._pdf_view.set_zoom_factor(self._content_zoom)
        QSettings(_ORG, _APP).setValue("content_zoom", self._content_zoom)
        self.statusBar().showMessage(f"縮放：{round(self._content_zoom * 100)}%", 2000)

    def _zoom_in(self):
        self._apply_zoom(self._content_zoom + 0.1)

    def _zoom_out(self):
        self._apply_zoom(self._content_zoom - 0.1)

    def _zoom_reset(self):
        self._apply_zoom(1.0)

    def restore_last_session(self):
        settings = QSettings(_ORG, _APP)
        raw = settings.value("open_tabs")
        paths = []
        if raw:
            try:
                paths = json.loads(raw)
            except (ValueError, TypeError):
                paths = []
        paths = [
            p for p in paths if p and is_supported_document(p) and Path(p).exists()
        ]
        if paths:
            # Add every remembered tab but load only the active one (the others
            # load lazily when first selected).
            for p in paths:
                kind = document_kind(Path(p))
                if kind:
                    self._add_tab(Path(p), kind)
            active = settings.value("active_tab", 0)
            try:
                active = int(active)
            except (ValueError, TypeError):
                active = 0
            active = max(0, min(active, self._tab_bar.count() - 1))
            self._tab_guard = True
            self._tab_bar.setCurrentIndex(active)
            self._tab_guard = False
            self._activate_tab(active)
            return
        # Fallback to the single last_file remembered by older versions.
        last = settings.value("last_file")
        if last and is_supported_document(last) and Path(last).exists():
            self._open_file(last)

    def _reload_current(self):
        if not self._current_file:
            return
        if self._current_kind == "pdf":
            page = self._pdf_view.current_page()
            if not self._pdf_view.load(self._current_file):
                self.statusBar().showMessage("已取消或無法重新載入此 PDF。", 4000)
                return
            self._panel.toc.update_outline(self._pdf_view.outline())
            self._pdf_view.set_highlights(self._pdf_highlights)
            self._pdf_view.restore_page(page)
        else:
            self._renderer.reload_current()
        self.statusBar().showMessage("已重新載入文件", 3000)

    # --- external file-change detection ---
    @staticmethod
    def _file_signature(path) -> tuple[int, int] | None:
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _watch_current_file(self):
        watched = self._fs_watcher.files()
        if watched:
            self._fs_watcher.removePaths(watched)
        if self._current_file and self._current_file.exists():
            self._fs_watcher.addPath(str(self._current_file))
            self._loaded_signature = self._file_signature(self._current_file)

    def _rearm_watch(self):
        if not self._current_file or not self._current_file.exists():
            return
        if str(self._current_file) not in self._fs_watcher.files():
            self._fs_watcher.addPath(str(self._current_file))

    def _on_file_changed(self, path: str):
        # An os.replace-style save (ours or another editor's) drops the watch,
        # so always re-arm it shortly after the event settles.
        QTimer.singleShot(150, self._rearm_watch)
        if not self._current_file or str(self._current_file) != path:
            return
        current = self._file_signature(self._current_file)
        if current is None:
            self.statusBar().showMessage(
                f"檔案已不存在或暫時無法存取：{self._current_file.name}", 5000
            )
            return
        if current == self._loaded_signature:
            return  # our own save, or a no-op touch — nothing changed
        self._loaded_signature = current
        self._prompt_external_change()

    def _prompt_external_change(self):
        if self._reload_prompt_open or not self._current_file:
            return
        self._reload_prompt_open = True
        try:
            name = self._current_file.name
            if self._edit_mode and self._editor.is_modified():
                answer = QMessageBox.question(
                    self,
                    "檔案已在外部變更",
                    f"{name} 已被其他程式修改，但你有未儲存的編輯。\n"
                    "要捨棄你的編輯並載入磁碟上的新版本嗎？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._leave_edit_ui()
                    self._renderer.load_file(self._current_file)
            else:
                answer = QMessageBox.question(
                    self,
                    "檔案已在外部變更",
                    f"{name} 已被其他程式修改，要重新載入嗎？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._renderer.reload_current()
        finally:
            self._reload_prompt_open = False

    def _persist_annotations(self):
        if not self._current_file or not is_markdown(self._current_file):
            return
        AnnotationStore.save(self._current_file, self._doc_annotations)
        self._tag_index.update(
            self._current_file, self._doc_annotations, self._current_front_tags
        )
        self._panel.annotations.set_document(self._doc_annotations)
        self._sync_renderer_annotations()
        self._refresh_tags_panel()

    def _update_front_tags(self):
        """Read the current file's front-matter tags and feed the tag index."""
        self._current_front_tags = []
        if not self._current_file or not is_markdown(self._current_file):
            return
        result = read_text(self._current_file)
        if result:
            front, _ = parse_front_matter(result[0])
            self._current_front_tags = front_matter_tags(front)
        self._tag_index.update(
            self._current_file, self._doc_annotations, self._current_front_tags
        )
        self._refresh_tags_panel()

    def _refresh_tags_panel(self):
        self._panel.tags.set_tags(self._tag_index.tag_counts())

    def _on_tag_selected(self, tag: str):
        self._panel.recent.set_tag_filter(tag)
        self._panel.tags.set_active(tag)
        self._panel.switch_to(1)  # jump to the Recent tab to show filtered files

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
        if not self._current_file or not is_markdown(self._current_file):
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

    def _on_task_toggled(self, line_no: int, checked: bool):
        """Persist a preview checkbox toggle back to the source ``- [ ]`` line."""
        if not self._current_file or not is_markdown(self._current_file):
            return
        if self._edit_mode:
            return  # the editor owns the buffer while editing
        try:
            raw = self._current_file.read_bytes()
        except OSError:
            return
        result = read_text(self._current_file)
        if result is None:
            return
        text, encoding = result
        newline = "\r\n" if b"\r\n" in raw else "\n"
        lines = text.split("\n")
        if line_no < 0 or line_no >= len(lines):
            return
        marker = "[x]" if checked else "[ ]"
        new_line = re.sub(r"\[[ xX]\]", marker, lines[line_no], count=1)
        if new_line == lines[line_no]:
            return
        lines[line_no] = new_line
        out = "\n".join(lines)
        if newline != "\n":
            out = out.replace("\n", newline)
        try:
            data = out.encode(encoding)
        except UnicodeEncodeError:
            data = out.encode("utf-8")
        try:
            atomic_write_bytes(self._current_file, data)
        except OSError as exc:
            self.statusBar().showMessage(f"無法更新待辦：{exc}", 4000)
            return
        # The browser already toggled the checkbox visually; just persist so the
        # scroll position is preserved (no reload).
        self._loaded_signature = self._file_signature(self._current_file)
        self._rearm_watch()
        self.statusBar().showMessage("已更新待辦狀態", 1500)

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
        if not self._current_file or self._edit_mode or not is_markdown(self._current_file):
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

    def _export_pptx(self):
        if (
            self._exporting
            or not self._current_file
            or self._edit_mode
            or not is_markdown(self._current_file)
        ):
            return
        result = read_text(self._current_file)
        if result is None:
            QMessageBox.warning(self, "匯出 PPT", "無法讀取檔案內容。")
            return
        text, _enc = result
        default = str(self._current_file.with_suffix(".pptx"))
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出 PPT", default, "PowerPoint 簡報 (*.pptx)"
        )
        if not path:
            return

        self._exporting = True
        renderer = None
        provider = None
        # Render Mermaid / math fragments to images via the web engine; if that
        # module can't be built, export still works with source-code boxes.
        try:
            from .fragment_render import FragmentRenderer

            renderer = FragmentRenderer(parent=self)
            provider = renderer.provide
        except Exception:
            renderer = None
            provider = None

        try:
            from .pptx_export import export_markdown_to_pptx

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                count = export_markdown_to_pptx(
                    text,
                    path,
                    base_dir=self._current_file.parent,
                    image_provider=provider,
                )
            finally:
                QApplication.restoreOverrideCursor()
                if renderer is not None:
                    renderer.cleanup()
        except Exception as exc:
            QMessageBox.warning(self, "匯出 PPT", f"匯出失敗：{exc}")
            return
        finally:
            self._exporting = False
        self.statusBar().showMessage(
            f"已匯出 {count} 張投影片至 {Path(path).name}", 5000
        )

    def _export_docx(self):
        if (
            self._exporting
            or not self._current_file
            or self._edit_mode
            or not is_markdown(self._current_file)
        ):
            return
        result = read_text(self._current_file)
        if result is None:
            QMessageBox.warning(self, "匯出 Word", "無法讀取檔案內容。")
            return
        text, _enc = result
        default = str(self._current_file.with_suffix(".docx"))
        path, _ = QFileDialog.getSaveFileName(
            self, "匯出 Word", default, "Word 文件 (*.docx)"
        )
        if not path:
            return

        self._exporting = True
        renderer = None
        provider = None
        try:
            from .fragment_render import FragmentRenderer

            renderer = FragmentRenderer(parent=self)
            provider = renderer.provide
        except Exception:
            renderer = None
            provider = None

        try:
            from .docx_export import export_markdown_to_docx

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                export_markdown_to_docx(
                    text,
                    path,
                    base_dir=self._current_file.parent,
                    image_provider=provider,
                )
            finally:
                QApplication.restoreOverrideCursor()
                if renderer is not None:
                    renderer.cleanup()
        except Exception as exc:
            QMessageBox.warning(self, "匯出 Word", f"匯出失敗：{exc}")
            return
        finally:
            self._exporting = False
        self.statusBar().showMessage(
            f"已匯出 Word 文件至 {Path(path).name}", 5000
        )

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

    def _scroll_to_anchor(self, target):
        # int -> PDF page jump; str -> Markdown heading anchor.
        if isinstance(target, int):
            self._pdf_view.jump_to_page(target)
        else:
            self._renderer.scroll_to(target)

    def _check_updates_silent(self):
        # Privacy/perf: honour the opt-out and only phone home once a day.
        if not self._update_check_enabled():
            return
        import time

        settings = QSettings(_ORG, _APP)
        try:
            last = float(settings.value("last_update_check", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        now = time.time()
        if now - last < 86400:
            return
        settings.setValue("last_update_check", now)
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
                is_supported_document(u.toLocalFile())
                for u in event.mimeData().urls()
            ):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if is_supported_document(local):
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
        self._save_active_view_state()
        if not self._is_detached:
            settings = QSettings(_ORG, _APP)
            settings.setValue("geometry", self.saveGeometry())
            open_tabs = [
                self._tab_bar.tabData(i) for i in range(self._tab_bar.count())
            ]
            settings.setValue("open_tabs", json.dumps(open_tabs))
            settings.setValue("active_tab", self._tab_bar.currentIndex())
            if self._current_file:
                settings.setValue("last_file", str(self._current_file))
        super().closeEvent(event)
