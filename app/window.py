"""Main application window with toolbar, side panel, and renderer workspace."""

import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QFileSystemWatcher,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QKeySequence,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QInputDialog,
    QMessageBox,
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
from . import export_actions, session_state, update_flow, view_mode
from . import doc_tags as doc_tags_facade
from .file_types import document_kind, is_markdown, is_pdf, is_supported_document
from .manage_tags_dialog import ManageTagsDialog
from .graph_view import GraphWindow
from .left_panel import LeftPanel
from .links import LinkIndex, collect_markdown_files, read_docs
from .md_converter import (
    body_hashtags,
    front_matter_tags,
    parse_front_matter,
    read_text,
)
from .mermaid_blocks import (
    find_mermaid_blocks,
    insert_mermaid_block,
    replace_mermaid_block,
)
from .mermaid_templates import default_template
from .mermaid_workspace import MermaidWorkspaceDialog
from .note_templates import (
    default_subfolder,
    find_templates,
    open_or_create_daily_note,
    render_template_file,
)
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
from .tag_colors import TagColorStore
from .tag_index import TagIndex
from .wikilink_completion import completion_candidates
from .version import VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
_DETACHED_WINDOWS: set[QMainWindow] = set()


def merged_tag_rows(
    tag_counts: list[tuple[str, int]],
    known_tags: list[str],
) -> list[tuple[str, int]]:
    """Merge indexed tag counts with user-created (known) tags for the panel.

    *known_tags* not present in *tag_counts* are merged in with count 0 so
    freshly created-but-unassigned tags still appear. The result keeps the
    ordering of TagIndex.tag_counts(): descending count, then tag name, so
    count-0 tags sort last, alphabetically.
    """
    counts: dict[str, int] = dict(tag_counts)
    for tag in known_tags:
        counts.setdefault(tag, 0)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


class LinkIndexThread(QThread):
    """Build the wiki-link index off the UI thread (reads many small files)."""

    ready = Signal(object)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self._roots = roots

    def run(self):
        try:
            files = collect_markdown_files(self._roots)
            docs = read_docs(files)
            index = LinkIndex()
            index.build(docs)
            index.completion_candidates = completion_candidates(self._roots, files)
            self.ready.emit(index)
        except Exception:
            self.ready.emit(None)


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
        self._link_refresh_pending = False
        self._graph_window: GraphWindow | None = None

        self._tag_index = TagIndex()
        self._tag_color_store = TagColorStore.load()
        self._active_tag = ""
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
        self._current_body_tags: list[str] = []

        self._panel = LeftPanel(
            on_file_selected=self._open_file,
            on_anchor_clicked=self._scroll_to_anchor,
            annotation_callbacks=annotation_callbacks,
            pdf_note_callbacks=pdf_note_callbacks,
            pdf_highlight_callbacks=pdf_highlight_callbacks,
            on_tag_selected=self._on_tag_selected,
            search_roots_provider=self._link_roots,
            on_search_result=self._open_global_search_result,
            on_manage_tags=self._open_manage_tags,
            tag_color_for=self._tag_color_store.color_for,
            on_create_tag=self._create_tag,
            on_delete_tag=self._delete_tag,
            on_assign_tag_to_paths=self._assign_tag_to_paths,
            on_open_file=self._open_file,
            # File-child context menu in the 標籤 tab reuses the file browser's
            # operations so the tag index and every view stay consistent.
            on_rename_file=self._rename_path,
            on_move_file=self._move_path,
            on_delete_file=self._delete_path,
            on_reveal_file=self._reveal_path,
            # Lazily supply the files carrying a tag as its tree children when
            # the user expands that tag node in the 標籤 tab.
            files_for_tag=lambda tag: sorted(
                (Path(p) for p in self._tag_index.files_with_tag(tag)),
                key=lambda p: p.name.lower(),
            ),
            on_doc_tags_changed=self._on_doc_tags_changed,
            theme=self._theme,
        )
        # File tree CRUD hooks: keep tabs / recents / watcher in sync when the
        # browser creates, renames, moves, or deletes files on disk.
        self._panel.file_browser.on_note_created = self._on_browser_note_created
        self._panel.file_browser.on_paths_migrated = self._on_browser_paths_migrated
        self._panel.file_browser.on_paths_deleted = self._on_browser_paths_deleted
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

        # View mode for Markdown documents: preview / edit / split (editor +
        # live preview). ``_edit_mode`` (bool) is derived from it below.
        self._view_mode = view_mode.PREVIEW
        self._editing_encoding = "utf-8"
        self._editing_newline = "\n"
        self._editor = EditorView()
        self._editor.modified_changed.connect(self._on_editor_modified)

        # Split mode is a split pane: editor on the left, a live preview on
        # the right, kept in sync as you type (debounced) and scroll. Edit
        # mode reuses the same splitter with the preview pane hidden.
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
        self._edit_preview.setVisible(False)

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(400)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._update_preview)
        self._scroll_guard = view_mode.ScrollSyncGuard()
        self._preview_scroll_ratio = 0.0
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
        self._pdf_view.highlight_delete_requested.connect(self._pdf_highlight_delete)

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
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(
            self._open_daily_note
        )
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._toggle_search
        )
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(
            self._open_global_search
        )
        QShortcut(QKeySequence("Escape"), self).activated.connect(
            self._close_search
        )
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(
            self._toggle_edit_mode
        )
        QShortcut(QKeySequence("Ctrl+Shift+E"), self).activated.connect(
            self._toggle_split_mode
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
        QShortcut(QKeySequence("Ctrl+G"), self).activated.connect(
            self._open_graph_view
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
        file_menu.addAction(act("開啟今日筆記\tCtrl+D", self._open_daily_note))
        file_menu.addAction(act("重新載入", self._reload_current))
        file_menu.addSeparator()
        file_menu.addAction(act("匯出 PDF…\tCtrl+Shift+P", self._export_pdf))
        file_menu.addAction(act("匯出 PPT…", self._export_pptx))
        file_menu.addAction(act("匯出 Word…", self._export_docx))
        file_menu.addSeparator()
        file_menu.addAction(act("離開", self.close))

        edit_menu = bar.addMenu("編輯(&E)")
        edit_menu.addAction(act("切換編輯 / 預覽\tCtrl+E", self._toggle_edit_mode))
        edit_menu.addAction(
            act("並排編輯（即時預覽）\tCtrl+Shift+E", self._toggle_split_mode)
        )
        edit_menu.addAction(act("儲存\tCtrl+S", self._save_edits))
        edit_menu.addAction(act("插入範本…", self._insert_template))
        edit_menu.addAction(act("尋找 / 取代\tCtrl+F", self._toggle_search))
        edit_menu.addAction(act("搜尋所有文件庫\tCtrl+Shift+F", self._open_global_search))

        view_menu = bar.addMenu("檢視(&V)")
        view_menu.addAction(act("切換側邊欄", self._toggle_sidebar))
        view_menu.addAction(act("筆記關聯圖\tCtrl+G", self._open_graph_view))
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

        settings_menu = bar.addMenu("設定(&S)")
        settings_menu.addAction(act("偏好設定…", self._open_preferences))

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
                ("Ctrl+D", "開啟今日筆記"),
                ("Ctrl+Shift+P", "匯出 PDF"),
            ]),
            ("分頁", [
                ("Ctrl+Tab", "下一個分頁"),
                ("Ctrl+Shift+Tab", "上一個分頁"),
                ("Ctrl+W", "關閉目前分頁"),
            ]),
            ("編輯", [
                ("Ctrl+E", "切換編輯 / 預覽"),
                ("Ctrl+Shift+E", "並排編輯（左編輯、右即時預覽）"),
                ("Ctrl+S", "儲存"),
                ("Ctrl+F", "在文件 / PDF 中搜尋"),
                ("Ctrl+Shift+F", "搜尋所有文件庫內容"),
            ]),
            ("檢視", [
                ("Ctrl+G", "開啟筆記關聯圖"),
                ("Ctrl++ / Ctrl+- / Ctrl+0", "放大 / 縮小 / 重設縮放"),
            ]),
            ("PDF", [
                ("Ctrl+C", "複製選取的 PDF 文字"),
                ("H", "螢光標記目前 PDF 選取"),
                ("Ctrl+Z", "螢光筆模式下撤銷上一筆標記"),
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

    def _load_user_css(self, reload: bool = False):
        session_state.load_user_css(self, reload=reload)

    def _open_preferences(self):
        session_state.open_preferences(self)

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
            "pencil", "編輯文件 (Ctrl+E)", self._cycle_view_mode
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
        if self._graph_window is not None:
            self._graph_window.apply_theme(self._theme)
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

        # Three-state cycle button: preview -> edit -> split -> preview.
        if self._view_mode == view_mode.SPLIT:
            edit_icon, edit_tip = "eye", "回到預覽 (Ctrl+E)"
        elif self._view_mode == view_mode.EDIT:
            edit_icon, edit_tip = "columns", "並排即時預覽 (Ctrl+Shift+E)"
        else:
            edit_icon, edit_tip = "pencil", "編輯文件 (Ctrl+E)"
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
        session_state.toggle_theme(self)

    def _toggle_annotation_side_notes(self, checked=None):
        session_state.toggle_annotation_side_notes(self, checked=checked)

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

    def _open_global_search(self):
        if not self._sidebar_open:
            self._do_toggle(0)
        self._panel.show_search()

    def _open_global_search_result(
        self, filepath: str, query: str, line_number: int
    ):
        target = str(Path(filepath))
        self._open_file(target)
        if self._active_path != target or self._current_kind != "markdown":
            return
        self._search_bar.show()
        changed = self._search_input.text() != query
        self._search_input.setText(query)
        if not changed:
            self._on_search_text_changed(query)
        self._renderer.find_text_after_load(query)
        self._search_input.setFocus()
        self._search_input.selectAll()
        self.statusBar().showMessage(f"已開啟第 {line_number} 行的搜尋結果", 3000)

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
        if self._current_kind == "markdown":
            self._renderer.cancel_pending_find()
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
        return session_state.pdf_pages_map()

    def _save_pdf_page(self, page0: int):
        session_state.save_pdf_page(self, page0)

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

    # ``_edit_mode`` (bool: the editor owns the buffer) stays as the compat
    # surface for export_actions / session_state; the source of truth is the
    # three-state ``_view_mode`` (preview / edit / split).
    @property
    def _edit_mode(self) -> bool:
        return view_mode.is_editing(self._view_mode)

    @_edit_mode.setter
    def _edit_mode(self, value: bool):
        if bool(value):
            if not view_mode.is_editing(self._view_mode):
                self._view_mode = view_mode.EDIT
        else:
            self._view_mode = view_mode.PREVIEW

    def _toggle_edit_mode(self):
        """Ctrl+E: toggle between preview and the plain editor."""
        self._request_view_mode(view_mode.toggle_edit(self._view_mode))

    def _toggle_split_mode(self):
        """Ctrl+Shift+E: jump straight into split (editor + live preview)."""
        self._request_view_mode(view_mode.toggle_split(self._view_mode))

    def _cycle_view_mode(self):
        """Toolbar button: preview -> edit -> split -> preview."""
        self._request_view_mode(view_mode.cycle_mode(self._view_mode))

    def _request_view_mode(self, mode: str):
        if not self._current_file or not is_markdown(self._current_file):
            return  # PDFs (and no document) stay in plain preview
        self._set_view_mode(mode)

    def _set_view_mode(self, mode: str):
        mode = view_mode.normalize(mode)
        if mode == self._view_mode:
            return
        if not view_mode.is_editing(mode):
            self._exit_edit_mode()  # confirms unsaved changes first
            return
        if not view_mode.is_editing(self._view_mode):
            self._enter_edit_mode(mode)
            return
        # edit <-> split: the editor keeps its buffer, only the preview pane
        # is shown / hidden.
        self._view_mode = mode
        self._apply_split_visibility()
        if mode == view_mode.SPLIT:
            self._update_preview()
        else:
            self._preview_timer.stop()
        self._refresh_icons()

    def _apply_split_visibility(self):
        split = self._view_mode == view_mode.SPLIT
        self._edit_preview.setVisible(split)
        if split:
            sizes = self._editor_split.sizes()
            if len(sizes) == 2 and sizes[1] == 0:
                total = max(sum(sizes), 2)
                self._editor_split.setSizes([total // 2, total - total // 2])

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

    def _enter_edit_mode(self, mode: str = view_mode.EDIT):
        if not view_mode.is_editing(mode):
            mode = view_mode.EDIT
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
        self._editor.set_wikilink_candidates(
            self._link_index.completion_candidates
        )

        self._view_mode = mode
        self._preview_scroll_ratio = 0.0
        self._apply_split_visibility()
        self._close_search()
        self._search_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)
        self._export_btn.setEnabled(False)
        self._stack.setCurrentWidget(self._editor_split)
        if mode == view_mode.SPLIT:
            self._update_preview()
        self._editor.setFocus()
        self._refresh_icons()
        self._update_dirty_ui()

    def _exit_edit_mode(self):
        if not self._confirm_discard_edits():
            return
        self._leave_edit_ui()

    def _leave_edit_ui(self):
        self._view_mode = view_mode.PREVIEW
        self._preview_timer.stop()
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
        # Debounced live re-render; only the split mode shows the preview.
        if self._view_mode == view_mode.SPLIT:
            self._preview_timer.start()

    def _update_preview(self):
        if self._view_mode != view_mode.SPLIT or not self._current_file:
            return
        text = self._editor.toPlainText()
        base = QUrl.fromLocalFile(str(self._current_file.parent) + "/")
        self._edit_preview.render_markdown_text(
            text,
            self._theme_name,
            title=self._current_file.stem,
            base_url=base,
            scroll_ratio=self._preview_scroll_ratio,
        )

    def _sync_preview_scroll(self):
        if self._view_mode != view_mode.SPLIT:
            return
        # Direction lock: while the editor drives the preview, scroll events
        # attributed to the preview side are suppressed (no echo loops).
        if not self._scroll_guard.try_acquire("editor"):
            return
        bar = self._editor.verticalScrollBar()
        ratio = view_mode.editor_scroll_ratio(bar.value(), bar.maximum())
        self._preview_scroll_ratio = ratio
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
        session_state.save_active_view_state(self)

    def _show_empty_state(self):
        self._current_file = None
        self._current_kind = ""
        self._current_front_tags = []
        self._current_body_tags = []
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
        self._current_body_tags = []
        if kind == "markdown":
            self._doc_annotations = AnnotationStore.load(path)
            self._sync_renderer_annotations()
            self._panel.annotations.set_document(self._doc_annotations)
            self._set_pdf_panel_document(None)
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
        if self._graph_window is not None and self._graph_window.isVisible():
            current = str(path) if kind == "markdown" else None
            self._graph_window.set_current_path(current)
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
        # Point the 文件標籤 field at this PDF and surface any tags it already
        # carries so they show up (with a count) in the 標籤 side panel/filters.
        self._set_pdf_panel_document(path)
        self._index_doc_tags(path)
        self._refresh_tags_panel()
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
        if not self._find_pdf_highlight(hid):
            return
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
            if force:
                self._link_refresh_pending = True
            self._refresh_backlinks()
            return
        self._link_thread = LinkIndexThread(roots, self)
        self._link_thread.ready.connect(self._on_link_index_ready)
        self._link_thread.finished.connect(self._on_link_index_finished)
        self._link_thread.start()
        self._refresh_backlinks()

    def _on_link_index_ready(self, index):
        if index is not None:
            self._link_index = index
            self._editor.set_wikilink_candidates(index.completion_candidates)
            if self._graph_window is not None and self._graph_window.isVisible():
                current = (
                    str(self._current_file)
                    if self._current_file and self._current_kind == "markdown"
                    else None
                )
                self._graph_window.set_index(index, current)
        self._refresh_backlinks()

    def _on_link_index_finished(self):
        thread = self._link_thread
        self._link_thread = None
        if thread is not None:
            thread.deleteLater()
        if self._link_refresh_pending:
            self._link_refresh_pending = False
            self._refresh_link_index(force=True)

    def _open_graph_view(self):
        if self._graph_window is None:
            self._graph_window = GraphWindow(self.open_path, self)
        current = (
            str(self._current_file)
            if self._current_file and self._current_kind == "markdown"
            else None
        )
        self._graph_window.apply_theme(self._theme)
        self._graph_window.set_index(self._link_index, current)
        self._graph_window.show()
        self._graph_window.raise_()
        self._graph_window.activateWindow()
        self._refresh_link_index(force=True)

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

    # --- file tree CRUD follow-ups (called by the file browser) ---
    def _on_browser_note_created(self, path: str):
        """A note was created in the file tree: open it in edit mode."""
        self._open_file(path)
        if (
            self._current_file
            and str(self._current_file) == str(Path(path))
            and self._current_kind == "markdown"
            and not self._edit_mode
        ):
            self._enter_edit_mode()
        self._refresh_link_index(force=True)

    def _configured_note_folder(self, key: str, default_name: str) -> Path | None:
        configured = str(QSettings(_ORG, _APP).value(key, "") or "").strip()
        if configured:
            return Path(configured)
        try:
            return default_subfolder(DocumentLibraryStore().load(), default_name)
        except OSError:
            return None

    def _open_daily_note(self, now: datetime | None = None):
        """Create or reopen today's configured daily note, then edit it."""
        if not isinstance(now, datetime):
            now = None
        folder = self._configured_note_folder("daily_notes_folder", "Daily Notes")
        if folder is None:
            QMessageBox.information(
                self,
                "Daily notes",
                "尚未設定 Daily notes 資料夾，且目前沒有文件庫。",
            )
            return

        template = str(
            QSettings(_ORG, _APP).value("daily_note_template", "") or ""
        ).strip()
        try:
            path, created = open_or_create_daily_note(
                folder,
                template or None,
                now,
            )
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(
                self,
                "Daily notes",
                f"無法建立今日筆記：\n{exc}",
            )
            return

        if not (
            self._current_file
            and str(self._current_file) == str(path)
            and self._edit_mode
        ):
            self.open_path(str(path))
            if (
                self._current_file
                and str(self._current_file) == str(path)
                and self._current_kind == "markdown"
                and not self._edit_mode
            ):
                self._enter_edit_mode()
        else:
            self._editor.setFocus()

        if created:
            self._panel.file_browser.refresh_libraries()
            self._refresh_link_index(force=True)
        self.statusBar().showMessage(f"今日筆記：{path.name}", 3000)

    def _insert_template(
        self,
        template_path: str | Path | None = None,
        now: datetime | None = None,
    ):
        """Insert a rendered Markdown template at the editor cursor."""
        if isinstance(template_path, bool):
            template_path = None
        if not isinstance(now, datetime):
            now = None
        if not (
            self._current_file
            and self._current_kind == "markdown"
            and self._edit_mode
        ):
            QMessageBox.information(
                self,
                "插入範本",
                "請先開啟 Markdown 筆記並進入編輯模式。",
            )
            return

        if template_path is None:
            folder = self._configured_note_folder("templates_folder", "Templates")
            templates = find_templates(folder) if folder is not None else []
            if not templates:
                QMessageBox.information(
                    self,
                    "插入範本",
                    "範本資料夾不存在，或資料夾內沒有 Markdown 範本。",
                )
                return
            labels = [
                path.relative_to(folder).as_posix() for path in templates
            ]
            choice, ok = QInputDialog.getItem(
                self,
                "插入範本",
                "選擇範本：",
                labels,
                0,
                False,
            )
            if not ok:
                return
            template_path = templates[labels.index(choice)]

        try:
            rendered = render_template_file(
                template_path,
                self._current_file.stem,
                now,
            )
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(
                self,
                "插入範本",
                f"無法讀取範本：\n{exc}",
            )
            return

        cursor = self._editor.textCursor()
        cursor.insertText(rendered)
        self._editor.setTextCursor(cursor)
        self.statusBar().showMessage(f"已插入範本：{Path(template_path).name}", 3000)

    def _on_browser_paths_migrated(self, mapping: dict):
        """Files were renamed/moved on disk: re-point tabs, recents, state."""
        if not mapping:
            return
        for i in range(self._tab_bar.count()):
            key = self._tab_bar.tabData(i)
            new = mapping.get(key)
            if not new:
                continue
            self._tab_guard = True
            self._tab_bar.setTabData(i, new)
            self._tab_bar.setTabText(i, Path(new).name)
            self._tab_bar.setTabToolTip(i, new)
            self._tab_guard = False
            if key in self._tab_state:
                self._tab_state[new] = self._tab_state.pop(key)
        if self._active_path in mapping:
            self._active_path = mapping[self._active_path]
        if self._current_file and str(self._current_file) in mapping:
            self._current_file = Path(mapping[str(self._current_file)])
            self.setWindowTitle(f"{self._current_file.name} - Markdown Viewer")
            self._toolbar_title.setText(self._current_file.name)
            self._toolbar_subtitle.setText(str(self._current_file.parent))
            self._watch_current_file()
        self._panel.recent.migrate_paths(mapping)
        self._refresh_tags_panel()
        self._refresh_link_index(force=True)

    def _on_browser_paths_deleted(self, paths: list):
        """Files were deleted on disk: close their tabs and drop recents."""
        for path in paths:
            key = str(path)
            idx = self._index_of_path(key)
            if idx >= 0:
                if self._edit_mode and key == self._active_path:
                    # The file is gone; don't offer to "save" it back.
                    self._editor.mark_saved()
                self._on_tab_close(idx)
        self._panel.recent.remove_paths(list(paths))
        self._refresh_tags_panel()
        self._refresh_link_index(force=True)

    # --- file operations reused from other panels (e.g. the 標籤 tab) ---
    # These delegate to the file browser's public wrappers so a file acted on
    # from the tag tree runs the identical rename/move/delete/reveal flow --
    # same dialogs, file_ops, tag-index migration, and refresh. The browser's
    # on_paths_migrated / on_paths_deleted callbacks (wired above to
    # _on_browser_paths_migrated / _on_browser_paths_deleted) already refresh
    # the tag panel, so these must NOT call _refresh_tags_panel() again --
    # doing so would refresh twice; relying on the callbacks keeps every view
    # (file tree, 最近, 標籤 tree) and the tag index consistent.
    def _rename_path(self, path):
        self._panel.file_browser.rename_file(Path(path))

    def _move_path(self, path):
        self._panel.file_browser.move_file(Path(path))

    def _delete_path(self, path):
        self._panel.file_browser.delete_file(Path(path))

    def _reveal_path(self, path):
        self._panel.file_browser.reveal_file(Path(path))

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
        session_state.apply_zoom(self, factor)

    def _zoom_in(self):
        self._apply_zoom(self._content_zoom + 0.1)

    def _zoom_out(self):
        self._apply_zoom(self._content_zoom - 0.1)

    def _zoom_reset(self):
        self._apply_zoom(1.0)

    def restore_last_session(self):
        session_state.restore_last_session(self)

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

    def _index_doc_tags(self, path):
        """Push a file's document-level tags into the shared tag index.

        Type-neutral entry point. For PDF the index entry carries only
        doc_tags (front/body/annotation tags are markdown-only); the tags
        are read through the app.doc_tags facade which dispatches by file
        type. Markdown uses the richer update path elsewhere.
        """
        tags = doc_tags_facade.read_doc_tags(Path(path))
        doc = DocumentAnnotations(doc_tags=list(tags))
        self._tag_index.update(path, doc, front_tags=[], body_tags=[])

    def _persist_annotations(self):
        if not self._current_file:
            return
        # Front/body/annotation tags are markdown-only; keep that computation
        # guarded internally, but let document-level tags flow for PDF too so
        # tagged PDFs still enter the shared index.
        if is_markdown(self._current_file):
            AnnotationStore.save(self._current_file, self._doc_annotations)
            self._tag_index.update(
                self._current_file,
                self._doc_annotations,
                front_tags=self._current_front_tags,
                body_tags=self._current_body_tags,
            )
            self._panel.annotations.set_document(self._doc_annotations)
            self._sync_renderer_annotations()
        elif is_pdf(self._current_file):
            self._index_doc_tags(self._current_file)
        self._refresh_tags_panel()

    def _update_front_tags(self):
        """Read front-matter/body tags from the current Markdown file."""
        self._current_front_tags = []
        self._current_body_tags = []
        if not self._current_file:
            return
        if is_markdown(self._current_file):
            result = read_text(self._current_file)
            if result:
                front, body = parse_front_matter(result[0])
                self._current_front_tags = front_matter_tags(front)
                self._current_body_tags = body_hashtags(body)
            self._tag_index.update(
                self._current_file,
                self._doc_annotations,
                front_tags=self._current_front_tags,
                body_tags=self._current_body_tags,
            )
        elif is_pdf(self._current_file):
            self._index_doc_tags(self._current_file)
        self._refresh_tags_panel()

    def _refresh_tags_panel(self):
        """Single entry point for pushing tag rows into the tag panel.

        The panel data is the union of indexed tag counts (tags actually
        assigned to files) and the color store's known tags (tags the user
        created but may not have assigned yet). Known-but-unassigned tags are
        merged in with count 0 so they appear immediately, EndNote-style.
        """
        merged = merged_tag_rows(
            self._tag_index.tag_counts(),
            self._tag_color_store.known_tags(),
        )
        self._panel.tags.set_tags(merged)

    def _refresh_file_views(self):
        """Re-render the file browser so its per-file tag dots stay current.

        The 最近 tab is intentionally left untouched: tags must never filter or
        hide files in the 檔案 / 最近 tabs — tag browsing lives only in the 標籤
        tab's tree (see _on_tag_selected).
        """
        self._panel.file_browser.refresh_libraries()

    def _set_pdf_panel_document(self, path):
        """Point the PDF markup panel's 文件標籤 field at *path* (or None).

        Accessed defensively so the injected test panel double (which omits
        the PDF markup sub-panel) stays compatible.
        """
        panel = getattr(self._panel, "pdf_markup", None)
        if panel is not None:
            panel.set_pdf_document(path)

    # --- document-level tag management (MD + PDF) ---
    def _open_manage_tags(self, paths):
        """Open the EndNote-style 管理標籤 popup for one or more files."""
        paths = [Path(p) for p in paths]
        if not paths:
            return
        ManageTagsDialog(
            paths,
            self._tag_index,
            self._tag_color_store,
            on_changed=self._on_doc_tags_changed,
            parent=self,
        ).exec()

    def _on_doc_tags_changed(self, paths):
        """Re-index each edited file's doc_tags and refresh the tag views.

        Persistence has already happened (via app.doc_tags) before this is
        called; here we only sync the shared index and the UI.
        """
        for path in paths:
            path = Path(path)
            if is_markdown(path):
                if (
                    self._current_file
                    and Path(self._current_file).resolve() == path.resolve()
                ):
                    # Keep the in-memory markdown model authoritative for the
                    # open file, then persist through the normal markdown path.
                    self._doc_annotations.doc_tags = (
                        doc_tags_facade.read_doc_tags(path)
                    )
                    self._persist_annotations()
                    continue
                doc = AnnotationStore.load(path)
                self._tag_index.update(path, doc, front_tags=[], body_tags=[])
            else:
                self._index_doc_tags(path)
                # If the manage-tags dialog edited the open PDF, refresh its
                # 文件標籤 field so the panel mirrors the new state.
                if (
                    self._current_file
                    and is_pdf(self._current_file)
                    and Path(self._current_file).resolve() == path.resolve()
                ):
                    self._set_pdf_panel_document(self._current_file)
        self._refresh_tags_panel()
        self._refresh_file_views()

    def _create_tag(self):
        """Prompt for a new tag name + palette color and persist the color."""
        name, color = self._prompt_new_tag()
        if not name:
            return
        self._tag_color_store.set_color(name, color)
        self.statusBar().showMessage(f"已建立標籤「{name}」", 2500)
        self._refresh_tags_panel()

    def _delete_tag(self, tag: str) -> None:
        """Delete a tag from the panel: drop its doc-level assignments + color.

        Note: tags can also be *content-derived* (from MD front-matter, body
        #hashtags, or annotations). Deleting only strips the document-level
        assignment and the color registration; it deliberately does NOT edit
        file contents, so a content-derived tag may reappear on the next
        re-index. That is expected behavior.
        """
        answer = QMessageBox.question(
            self,
            "刪除標籤",
            f"確定要刪除標籤「{tag}」嗎？（僅移除標籤，不會刪除檔案）",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        affected = [Path(p) for p in self._tag_index.files_with_tag(tag)]
        for path in affected:
            new = [x for x in doc_tags_facade.read_doc_tags(path) if x != tag]
            doc_tags_facade.write_doc_tags(path, new)
        # Re-index the touched files and refresh the file views.
        self._on_doc_tags_changed(affected)
        # Drop the color registration so the tag stops appearing at count 0.
        self._tag_color_store.remove(tag)
        self._refresh_tags_panel()

    def _prompt_new_tag(self, default_name: str = ""):
        """Small modal: tag name input + 7-swatch palette picker.

        Returns (name, hex_color) on accept, or ("", "") on cancel.
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("新增標籤")
        dialog.setMinimumWidth(340)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        layout.addWidget(QLabel("標籤名稱："))
        name_edit = QLineEdit(default_name)
        # Normalize the field height: the app-wide theme QSS adds large padding /
        # min-height to QLineEdit, which otherwise makes this box look oversized.
        name_edit.setFixedHeight(32)
        name_edit.setStyleSheet("QLineEdit { padding: 4px 8px; min-height: 0; }")
        layout.addWidget(name_edit)

        layout.addWidget(QLabel("顏色："))

        swatch_row = QHBoxLayout()
        swatch_row.setSpacing(8)
        hexes = TagColorStore.palette_hexes()
        selected = {"hex": hexes[0]}
        buttons: list[QPushButton] = []

        def _select(idx: int):
            selected["hex"] = hexes[idx]
            for j, btn in enumerate(buttons):
                btn.setText("✓" if j == idx else "")

        for i, hex_color in enumerate(hexes):
            btn = QPushButton()
            btn.setFixedSize(30, 30)
            # Reset the theme's button box-model (min-width/height, padding, margin)
            # so the fixed 30x30 swatch is honored instead of inheriting the large
            # metrics the app-wide QSS applies to every QPushButton.
            btn.setStyleSheet(
                "QPushButton {"
                f" background-color: {hex_color};"
                " border: 2px solid rgba(0, 0, 0, 0.28); border-radius: 6px;"
                " min-width: 0; min-height: 0; padding: 0; margin: 0;"
                " color: white; font-weight: bold; }"
                " QPushButton:hover { border: 2px solid #444; }"
            )
            btn.clicked.connect(lambda _=False, idx=i: _select(idx))
            buttons.append(btn)
            swatch_row.addWidget(btn)
        swatch_row.addStretch()
        layout.addLayout(swatch_row)
        _select(0)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return "", ""
        return name_edit.text().strip(), selected["hex"]

    def _assign_tag_to_paths(self, tag: str, paths):
        """Add *tag* to each of *paths* (drag-onto-tag / quick assign)."""
        tag = (tag or "").strip()
        if not tag:
            return
        changed = []
        for path in paths:
            path = Path(path)
            if not is_supported_document(path):
                continue
            existing = doc_tags_facade.read_doc_tags(path)
            if tag in existing:
                changed.append(path)
                continue
            doc_tags_facade.write_doc_tags(path, existing + [tag])
            changed.append(path)
        if changed:
            self._on_doc_tags_changed(changed)

    def _on_tag_selected(self, tag: str):
        self._active_tag = tag or ""
        self._panel.tags.set_active(tag)
        # Tag selection is scoped to the 標籤 tab ONLY. The matching files
        # (MD + PDF) appear as the tag node's own children in the tree, loaded
        # lazily when the tag expands (see files_for_tag). We intentionally do
        # NOT filter the 檔案 / 最近 tabs and do NOT switch tabs — selecting a
        # tag must never hide files in the other views.

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
        export_actions.export_pdf(self)

    def _export_pptx(self):
        export_actions.export_pptx(self)

    def _export_docx(self):
        export_actions.export_docx(self)

    def _export_single_page(self, dims):
        export_actions.export_single_page(self, dims)

    def _on_pdf_exported(self, path: str, ok: bool):
        export_actions.on_pdf_exported(self, path, ok)

    def _scroll_to_anchor(self, target):
        # int -> PDF page jump; str -> Markdown heading anchor.
        if isinstance(target, int):
            self._pdf_view.jump_to_page(target)
        else:
            self._renderer.scroll_to(target)

    def _check_updates_silent(self):
        update_flow.check_updates_silent(self)

    def _check_for_updates(self, manual: bool):
        update_flow.check_for_updates(self, manual)

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
        session_state.restore_geometry(self)

    def closeEvent(self, event):
        if session_state.close_event(self, event):
            super().closeEvent(event)
