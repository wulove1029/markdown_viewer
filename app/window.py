"""Main application window with toolbar, side panel, and renderer workspace."""

import json
import math
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    QFileSystemWatcher,
    QPoint,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtGui import (
    QAction,
    QDragEnterEvent,
    QDropEvent,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
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
    QVBoxLayout,
    QWidget,
)

from .annotations import Annotation, AnnotationStore, DocumentAnnotations
from .attachments import import_attachment_file, markdown_attachment_link
from .atomic_io import atomic_write_bytes
from .document_libraries import DocumentLibraryStore
from .document_tabs import DocumentTabStrip, disambiguated_tab_labels
from . import edit_backend
from .editor import EditorView
from .editor_status import EditorStatus
from .format_actions import apply_format_action
from .format_commands import commands_for
from .format_toolbar import FormatToolbar
from . import export_actions, session_state, update_flow, view_mode
from . import doc_tags as doc_tags_facade
from .file_types import (
    document_kind,
    is_markdown,
    is_pdf,
    is_supported_document,
    is_text,
)
from .manage_tags_dialog import ManageTagsDialog
from .graph_view import GraphWindow
from .image_paste import (
    import_image_file,
    markdown_image_link,
    save_clipboard_image,
)
from .inline_edit import extract_source_lines, replace_source_lines
from .left_panel import LeftPanel
from .links import LinkIndex, collect_markdown_files, read_docs
from .md_converter import (
    body_hashtags,
    front_matter_tags,
    parse_front_matter,
    read_text,
    read_text_detailed,
)
from .new_note_dialog import NewNoteDialog
from .md_table import parse_table, serialize_table
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
    prepare_template_insertion,
    render_template_file,
)
from .pdf_notes import PdfNote, PdfNoteStore
from .pdf_highlights import DEFAULT_COLOR, PdfHighlight, PdfHighlightStore, Rect
from .pdf_view import PdfView
from .quick_open import QuickOpenDialog
from .renderer import RendererView
from .wysiwyg_view import WysiwygView
from .recovery import RecoverySnapshot, RecoveryStore
from .recovery_dialog import RecoveryDialog
from .recent_resources import (
    RecentResource,
    decode_recent_resources,
    encode_recent_resources,
    remember_recent_resource,
    resource_from_markdown,
)
from .shortcuts import WINDOW_SHORTCUTS, shortcut_by_id
from .shortcuts_dialog import ShortcutDialog
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
from .text_positions import py_to_qt_position, qt_to_py_position
from .tag_colors import TagColorStore
from .tag_index import TagIndex
from .toolbar_utilities import (
    ToolbarUtilities,
    UPDATE_AVAILABLE,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
)
from .translate import (
    DEEPL_KEY,
    PROVIDER_KEY,
    TARGET_KEY,
    cached_translation,
    normalize_provider,
    normalize_target,
    start_translation,
)
from .translate_dialog import TranslationDialog
from .updater import is_newer_version
from .wikilink_completion import completion_candidates
from .version import RELEASE_NOTES, VERSION

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
_RECENT_RESOURCES_KEY = "recent_editor_resources"
_RECENT_TEMPLATES_KEY = "recent_editor_templates"
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


class _SearchLineEdit(QLineEdit):
    """Search input whose Shift+Enter binding is distinct from Enter."""

    previous_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, *, previous_enabled: bool = True, parent=None):
        super().__init__(parent)
        self._previous_enabled = previous_enabled

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        if (
            event.key() == Qt.Key.Key_Escape
            and modifiers == Qt.KeyboardModifier.NoModifier
        ):
            self.cancel_requested.emit()
            event.accept()
            return
        if (
            self._previous_enabled
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and modifiers & Qt.KeyboardModifier.ShiftModifier
            and not modifiers
            & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
                | Qt.KeyboardModifier.MetaModifier
            )
        ):
            self.previous_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


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
        # Default WYSIWYG-vs-split edit backend (opt-in; per-tab overrides
        # live in ``self._tab_state[key]["edit_backend"]``). See
        # app/edit_backend.py for the pure state logic and its .txt guard.
        self._edit_backend = edit_backend.normalize_backend(
            settings.value(edit_backend.SETTINGS_KEY, edit_backend.DEFAULT_BACKEND)
        )
        # v2 "click to edit": PREVIEW double-click routing preference (see
        # app/edit_backend.py). Independent of ``_edit_backend`` above -- that
        # one only picks the default backend once EDIT mode is already
        # entered some other way.
        self._preview_double_click = edit_backend.normalize_preview_double_click(
            settings.value(
                edit_backend.PREVIEW_DOUBLE_CLICK_SETTINGS_KEY,
                edit_backend.PREVIEW_DOUBLE_CLICK_DEFAULT,
            )
        )
        self._wysiwyg_view: WysiwygView | None = None
        self._wysiwyg_shadow_text: str | None = None
        self._wysiwyg_shadow_qt_length: int | None = None
        self._wysiwyg_shadow_revision = 0
        self._applying_wysiwyg_delta = False
        self._wysiwyg_snapshot_token = 0
        self._wysiwyg_snapshot_busy = False
        self._wysiwyg_close_snapshot_approved = False
        # Effective backend for whatever is currently in the editor stack;
        # kept in sync by _activate_editor_state so text-change handlers can
        # skip work (split-only preview timer) without re-deriving it.
        self._active_edit_backend = edit_backend.SPLIT_BACKEND
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
        self._recovery_store = RecoveryStore()
        self._recovery_checked_paths: set[str] = set()

        self.setWindowTitle("Markdown Viewer")
        self._restore_geometry()
        self._sidebar_open = True
        self._search_escape_counter = 0
        self._active_search_escape_generation = 0
        self._update_check_thread = None
        self._update_download_thread = None
        self._update_progress = None
        self._update_close_pending = False
        self._deferred_update_close_approved = False
        self._available_update = None
        cached_update_version = str(
            settings.value("available_update_version", "") or ""
        ).strip().lstrip("vV")
        if (
            re.fullmatch(r"\d+(?:\.\d+)*", cached_update_version) is None
            or not is_newer_version(cached_update_version, VERSION)
        ):
            settings.remove("available_update_version")
            cached_update_version = ""
        elif settings.value("available_update_version") != cached_update_version:
            settings.setValue("available_update_version", cached_update_version)
        self._cached_update_version = cached_update_version
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
            on_add_tag=self._add_tag_to_paths,
            on_delete_tag=self._delete_tag,
            on_rename_tag=self._rename_tag,
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
        # Inline preview editing answers the page synchronously, so it is wired
        # as handlers rather than signals (see AnnotationBridge).
        self._renderer.bridge.set_inline_edit_handlers(
            fetch=self._inline_edit_fetch,
            commit=self._inline_edit_commit,
            paste_image=self._inline_edit_paste_image,
            commit_table=self._inline_edit_commit_table,
            serialize_table=self._inline_edit_serialize_table,
            reload=self._inline_edit_reload,
        )
        # True while the preview holds an inline editor with unsaved text.
        # The page pushes this (setInlineEditing) instead of the window asking
        # for it: runJavaScript is asynchronous, and every caller here is
        # about to open a modal dialog and needs the answer *now*.
        self._preview_editing = False
        self._renderer.bridge.inlineEditStateChanged.connect(
            self._on_preview_editing_changed
        )
        self._renderer.bridge.unhandledEscape.connect(
            self._on_preview_unhandled_escape
        )
        # v2 "click to edit": only the main preview (never the split-mode
        # preview pane, self._edit_preview) is wired to this -- see
        # RendererView.set_preview_double_click_mode's docstring.
        self._renderer.bridge.wysiwygEditRequested.connect(
            self._on_preview_wysiwyg_edit_requested
        )
        self._renderer.set_preview_double_click_mode(self._preview_double_click)
        self._renderer.wikilink_clicked.connect(self._on_wikilink_clicked)
        self._renderer.local_doc_clicked.connect(self._on_local_doc_clicked)
        self._renderer.translate_requested.connect(self._translate_selection)
        self._panel.close_btn.clicked.connect(self._toggle_sidebar)

        # Selection translation: one reused window, and a request counter so a
        # slow reply for an earlier selection cannot overwrite a newer one.
        self._translate_dialog: TranslationDialog | None = None
        self._translate_request_id = 0

        # View mode for Markdown documents: preview / edit / split (editor +
        # live preview). ``_edit_mode`` (bool) is derived from it below.
        self._view_mode = view_mode.PREVIEW
        self._editing_encoding = "utf-8"
        self._editing_newline = "\n"
        self._editor = EditorView()
        self._editor.translate_requested.connect(self._translate_selection)
        self._editor.modified_changed.connect(self._on_editor_modified)
        self._editor.image_status.connect(
            lambda msg: self.statusBar().showMessage(msg, 4000)
        )
        self._editor.format_action_requested.connect(self._apply_format_action)
        self._editor.resource_inserted.connect(self._record_recent_resource)

        # Split mode is a split pane: editor on the left, a live preview on
        # the right, kept in sync as you type (debounced) and scroll. Edit
        # mode reuses the same splitter with the preview pane hidden.
        self._edit_preview = RendererView()
        self._edit_preview.set_zoom(self._content_zoom)
        self._edit_preview.wikilink_clicked.connect(self._on_wikilink_clicked)
        self._edit_preview.local_doc_clicked.connect(self._on_local_doc_clicked)
        self._edit_preview.translate_requested.connect(self._translate_selection)
        self._edit_preview.bridge.unhandledEscape.connect(
            self._on_preview_unhandled_escape
        )

        self._editor_search_bar = self._build_editor_search_bar()
        self._editor_search_bar.hide()
        # Markdown formatting toolbar: only shown while editing Markdown
        # (never for .txt, never in preview / PDF).
        self._format_toolbar = FormatToolbar()
        self._format_toolbar.hide()
        self._format_toolbar.action_triggered.connect(self._apply_format_action)
        self._editor.format_context_changed.connect(
            self._on_format_context_changed
        )
        editor_pane = QWidget()
        editor_pane_layout = QVBoxLayout(editor_pane)
        editor_pane_layout.setContentsMargins(0, 0, 0, 0)
        editor_pane_layout.setSpacing(0)
        editor_pane_layout.addWidget(self._editor_search_bar)
        editor_pane_layout.addWidget(self._format_toolbar)
        editor_pane_layout.addWidget(self._editor)
        self._editor_status = EditorStatus(self._theme)
        editor_pane_layout.addWidget(self._editor_status)

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
        self._editor_status_timer = QTimer(self)
        self._editor_status_timer.setInterval(180)
        self._editor_status_timer.setSingleShot(True)
        self._editor_status_timer.timeout.connect(self._update_editor_status_document)
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(750)
        self._recovery_timer.setSingleShot(True)
        self._recovery_timer.timeout.connect(self._save_active_recovery_snapshot)
        self._scroll_guard = view_mode.ScrollSyncGuard()
        self._preview_scroll_ratio = 0.0
        self._editor.textChanged.connect(self._on_editor_text_changed)
        self._editor.cursorPositionChanged.connect(self._on_editor_cursor_changed)
        self._editor.verticalScrollBar().valueChanged.connect(
            self._sync_preview_scroll
        )

        self._search_bar = self._build_search_bar()
        self._search_bar.hide()

        # Native PDF viewer (outline + search + remembered page).
        self._pdf_view = PdfView()
        # PdfView is constructed after the shared content zoom is restored.
        # Apply it now so the first PDF does not incorrectly start at 100%.
        self._pdf_view.set_zoom_factor(self._content_zoom)
        self._pdf_view.page_changed.connect(self._on_pdf_page_changed)
        self._pdf_view.search_count_changed.connect(self._on_pdf_search_count)
        self._pdf_view.highlight_requested.connect(self._on_pdf_highlight_requested)
        self._pdf_view.highlight_delete_requested.connect(self._pdf_highlight_delete)
        self._pdf_view.outline_ready.connect(self._on_pdf_outline_ready)
        self._pdf_view.zoom_changed.connect(self._on_pdf_wheel_zoom_changed)
        self._pdf_view.translate_requested.connect(self._translate_selection)
        # Wheel zoom is already applied locally by PdfView. Defer the heavier
        # hidden-renderer/QSettings synchronization until the gesture settles.
        self._pending_pdf_wheel_zoom: float | None = None
        self._pdf_zoom_sync_timer = QTimer(self)
        self._pdf_zoom_sync_timer.setSingleShot(True)
        self._pdf_zoom_sync_timer.setInterval(120)
        self._pdf_zoom_sync_timer.timeout.connect(self._commit_pdf_wheel_zoom)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._renderer)
        self._stack.addWidget(self._editor_split)
        self._stack.addWidget(self._pdf_view)

        # Tab strip for switching between open documents (one shared viewer).
        self._tab_strip = DocumentTabStrip(self._theme)
        self._tab_bar = self._tab_strip.tab_bar
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
        renderer_layout.addWidget(self._tab_strip)
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

        self._install_shortcuts()

        self._build_menu_bar()
        self._load_user_css()
        self._apply_theme()
        self._refresh_tags_panel()
        # Bind the delayed check to this window so closing it during startup
        # cancels the callback instead of invoking a deleted Python wrapper.
        QTimer.singleShot(2000, self, self._check_updates_silent)

    def _build_menu_bar(self):
        # Shortcuts stay on the QShortcuts above; the menu shows them as hints
        # (text after \t) without re-registering, so there's no key conflict.
        bar = self.menuBar()

        def act(text, slot):
            action = QAction(text, self)
            action.triggered.connect(slot)
            return action

        def command_act(command_id, text):
            spec = shortcut_by_id(command_id)
            action = act(f"{text}\t{spec.menu_hint}", getattr(self, spec.handler))
            action.setData(command_id)
            action.setProperty("commandId", command_id)
            return action

        file_menu = bar.addMenu("檔案(&F)")
        file_menu.addAction(command_act("file.new", "新增筆記…"))
        file_menu.addAction(command_act("file.open", "開啟…"))
        file_menu.addAction(command_act("file.quick_open", "快速開啟…"))
        file_menu.addAction(command_act("file.daily_note", "開啟今日筆記"))
        file_menu.addAction(act("重新載入", self._reload_current))
        file_menu.addSeparator()
        file_menu.addAction(command_act("file.export_pdf", "匯出 PDF…"))
        file_menu.addAction(act("匯出 PPT…", self._export_pptx))
        file_menu.addAction(act("匯出 Word…", self._export_docx))
        file_menu.addAction(act("匯出 HTML…", self._export_html))
        file_menu.addSeparator()
        file_menu.addAction(act("離開", self.close))

        self._edit_menu = bar.addMenu("編輯(&E)")
        self._edit_menu.addAction(command_act("edit.toggle", "切換編輯 / 預覽"))
        self._edit_menu.addAction(
            command_act("edit.split", "並排編輯（即時預覽）")
        )
        self._edit_menu.addAction(command_act("edit.save", "儲存"))
        self._edit_menu.addAction(
            command_act("edit.toggle_wysiwyg", "切換所見即所得編輯")
        )
        self._edit_menu.addSeparator()
        self._undo_action = act("復原\tCtrl+Z", self._editor.undo)
        self._redo_action = act("重做\tCtrl+Y", self._editor.redo)
        self._cut_action = act("剪下\tCtrl+X", self._editor.cut)
        self._copy_action = act("複製\tCtrl+C", self._editor.copy)
        self._paste_action = act("貼上\tCtrl+V", self._editor.paste)
        self._select_all_action = act("全選\tCtrl+A", self._editor.selectAll)
        self._edit_menu.addActions((self._undo_action, self._redo_action))
        self._edit_menu.addSeparator()
        self._edit_menu.addActions(
            (
                self._cut_action,
                self._copy_action,
                self._paste_action,
                self._select_all_action,
            )
        )
        self._edit_menu.addSeparator()
        self._edit_menu.addAction(command_act("search.current", "尋找 / 取代"))
        self._edit_menu.addAction(command_act("search.library", "搜尋所有文件庫"))
        self._edit_menu.aboutToShow.connect(self._update_native_edit_actions)

        self._format_menu = bar.addMenu("格式(&O)")
        group_labels = {
            "text": "文字",
            "heading": "標題",
            "structure": "段落與清單",
            "code": "程式碼",
            "insert": "插入",
            "resource": "資源",
            "math": "公式",
            "reference": "參照",
        }
        group_menus = {
            group: self._format_menu.addMenu(label)
            for group, label in group_labels.items()
        }
        self._format_menu_actions: dict[str, QAction] = {}
        shortcut_command_ids = {
            "bold": "edit.bold",
            "italic": "edit.italic",
            "link": "edit.link",
            "ordered_list": "edit.ordered_list",
            "bullet_list": "edit.bullet_list",
        }
        for command in commands_for("toolbar"):
            label = command.title
            if command.shortcut:
                label += f"\t{command.shortcut}"
            action = act(
                label,
                lambda _checked=False, aid=command.action_id: (
                    self._apply_format_action(aid)
                ),
            )
            command_id = shortcut_command_ids.get(
                command.action_id, command.action_id
            )
            action.setData(command_id)
            action.setProperty("commandId", command_id)
            action.setProperty("formatAction", command.action_id)
            action.setCheckable(
                command.action_id
                in {
                    "bold", "italic", "strikethrough", "h1", "h2", "h3",
                    "bullet_list", "ordered_list", "task_list", "quote",
                    "inline_code", "wikilink", "highlight",
                }
            )
            group_menus[command.group].addAction(action)
            self._format_menu_actions[command.action_id] = action
        resource_menu = group_menus["resource"]
        resource_menu.addSeparator()
        resource_menu.addAction(act("插入範本…", self._insert_template))
        resource_menu.addAction(act("加入附件…", self._insert_attachment_via_dialog))
        resource_menu.addAction(act("最近使用的資源…", self._insert_recent_resource))
        self._format_menu.aboutToShow.connect(self._update_format_menu_actions)

        view_menu = bar.addMenu("檢視(&V)")
        view_menu.addAction(act("切換側邊欄", self._toggle_sidebar))
        view_menu.addAction(command_act("view.graph", "筆記關聯圖"))
        view_menu.addSeparator()
        view_menu.addAction(command_act("view.zoom_in", "放大"))
        view_menu.addAction(command_act("view.zoom_out", "縮小"))
        view_menu.addAction(command_act("view.zoom_reset", "重設縮放"))
        view_menu.addSeparator()
        view_menu.addAction(command_act("tabs.next", "下一個分頁"))
        view_menu.addAction(command_act("tabs.previous", "上一個分頁"))
        view_menu.addAction(command_act("tabs.close", "關閉分頁"))
        view_menu.addSeparator()
        self._theme_action = act("切換深色模式", self._toggle_theme)
        view_menu.addAction(self._theme_action)
        view_menu.addAction(act("顯示 / 隱藏旁註卡片", self._toggle_annotation_side_notes))

        tools_menu = bar.addMenu("工具(&T)")
        tools_menu.addAction(
            command_act("tools.mermaid_workspace", "Mermaid 工作區...")
        )
        tools_menu.addAction(act("編輯 Mermaid 圖表...", self._edit_mermaid_diagram))
        tools_menu.addAction(act("插入 Mermaid 圖表...", self._insert_mermaid_diagram))

        settings_menu = bar.addMenu("設定(&S)")
        settings_menu.addAction(act("偏好設定…", self._open_preferences))

        help_menu = bar.addMenu("說明(&H)")
        help_menu.addAction(act("鍵盤快捷鍵…", self._show_shortcuts))
        self._update_action = act("檢查更新…", self._on_update_button_clicked)
        self._update_action.setEnabled(
            self._toolbar_utilities.update_state
            not in (UPDATE_CHECKING, UPDATE_DOWNLOADING)
        )
        help_menu.addAction(self._update_action)
        help_menu.addAction(act("關於 Markdown Viewer", self._show_about))

    def _update_native_edit_actions(self) -> None:
        editing = self._edit_mode
        if (
            editing
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            # These QAction callbacks target the hidden QPlainTextEdit. The
            # visible Office Viewer surface owns its own undo/cut/copy/paste
            # shortcuts and right-click menu, so enabling them here would
            # edit an invisible competing buffer.
            for action in (
                self._undo_action,
                self._redo_action,
                self._cut_action,
                self._copy_action,
                self._paste_action,
                self._select_all_action,
            ):
                action.setEnabled(False)
            return
        document = self._editor.document()
        self._undo_action.setEnabled(editing and document.isUndoAvailable())
        self._redo_action.setEnabled(editing and document.isRedoAvailable())
        has_selection = editing and self._editor.textCursor().hasSelection()
        self._cut_action.setEnabled(has_selection)
        self._copy_action.setEnabled(has_selection)
        self._paste_action.setEnabled(editing and self._editor.canPaste())
        self._select_all_action.setEnabled(editing and not document.isEmpty())

    def _update_format_menu_actions(self) -> None:
        enabled = (
            self._edit_mode
            and self._current_kind == "markdown"
            and self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
        )
        self._format_menu.setEnabled(enabled)
        for action in self._format_menu_actions.values():
            action.setEnabled(enabled)

    def _on_format_context_changed(self, action_ids) -> None:
        active = set(action_ids)
        self._format_toolbar.set_active_actions(active)
        for action_id, action in getattr(
            self, "_format_menu_actions", {}
        ).items():
            action.setChecked(action_id in active)

    def _install_shortcuts(self):
        self._registered_shortcuts: list[QShortcut] = []
        for spec in WINDOW_SHORTCUTS:
            callback = getattr(self, spec.handler)
            for alias_index, sequence in enumerate(spec.sequences):
                owner = self._editor if spec.owner == "editor" else self
                shortcut = QShortcut(QKeySequence(sequence), owner)
                shortcut.setContext(
                    Qt.ShortcutContext.WidgetShortcut
                    if spec.owner == "editor"
                    else Qt.ShortcutContext.WindowShortcut
                )
                shortcut.setObjectName(
                    f"shortcut.{spec.command_id}.{alias_index}"
                )
                shortcut.setProperty("commandId", spec.command_id)
                shortcut.activated.connect(callback)
                self._registered_shortcuts.append(shortcut)

    def _show_about(self):
        notes = "".join(f"<li>{item}</li>" for item in RELEASE_NOTES)
        QMessageBox.about(
            self,
            "關於 Markdown Viewer",
            f"<b>Markdown Viewer</b><br>版本 {VERSION}<br><br>"
            "Markdown 筆記閱讀 / 編輯與 PDF 閱讀工具。<br><br>"
            f"<b>本版更新</b><ul>{notes}</ul>",
        )

    def _show_shortcuts(self):
        ShortcutDialog(self._theme, self).exec()

    def keyPressEvent(self, event):
        """Close search on an otherwise-unhandled Escape key.

        This fallback deliberately lives after focused-child handling instead
        of in a WindowShortcut.  Wiki completion, preview inline editing,
        annotations, and Mermaid canvases therefore get the first chance to
        consume Escape; if they do not, an open search bar still closes from
        anywhere in the main window.
        """
        if (
            event.key() == Qt.Key.Key_Escape
            and event.modifiers() == Qt.KeyboardModifier.NoModifier
            and (
                not self._search_bar.isHidden()
                or not self._editor_search_bar.isHidden()
            )
        ):
            self._close_search()
            event.accept()
            return
        super().keyPressEvent(event)

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
        self._wysiwyg_btn = self._toolbar_button(
            "layers",
            "切換所見即所得編輯 (Ctrl+Shift+W)；非標準語法（wiki 連結、"
            "callout、front matter）建議改用分割檢視編輯",
            self._toggle_edit_backend,
        )
        self._wysiwyg_btn.setCheckable(True)
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
        self._toolbar_utilities = ToolbarUtilities(
            self._theme,
            theme_name=self._theme_name,
            current_version=VERSION,
        )
        self._theme_btn = self._toolbar_utilities.theme_button
        self._update_btn = self._toolbar_utilities.update_button
        self._theme_btn.clicked.connect(self._toggle_theme)
        self._update_btn.clicked.connect(self._on_update_button_clicked)
        if self._cached_update_version:
            self._toolbar_utilities.set_update_state(
                UPDATE_AVAILABLE, version=self._cached_update_version
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
        layout.addWidget(self._wysiwyg_btn)
        layout.addWidget(self._mermaid_btn)
        layout.addWidget(self._export_btn)
        layout.addWidget(self._side_notes_btn)
        layout.addWidget(self._highlight_btn)
        layout.addWidget(title_wrap, stretch=1)
        layout.addWidget(self._toolbar_utilities)
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
        self._tab_strip.apply_theme(self._theme)
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
        self._editor_status.apply_theme(self._theme)
        self._editor_search_bar.setStyleSheet(self._editor_search_style())
        self._format_toolbar.apply_theme(self._theme)
        if self._wysiwyg_view is not None:
            self._wysiwyg_view.apply_theme(self._theme)
        self._pdf_view.apply_theme(self._theme)
        if self._graph_window is not None:
            self._graph_window.apply_theme(self._theme)
        if getattr(self, "_translate_dialog", None) is not None:
            self._translate_dialog.apply_theme(self._theme)
        self._refresh_icons()
        self._renderer.set_theme(self._theme_name)
        # The preview holds a throwaway HTML string (no _current_path), so the
        # renderer's in-place theme swap can't recolor it — re-render instead.
        if getattr(self, "_edit_mode", False):
            self._update_preview()

    # ── selection translation ───────────────────────────────────────────

    def _translate_selection(self, text: str):
        """Translate a right-click selection from any of the content views."""
        selection = (text or "").strip()
        if not selection:
            self.statusBar().showMessage("沒有選取任何文字", 3000)
            return
        settings = QSettings(_ORG, _APP)
        self._run_translation(
            selection,
            normalize_provider(settings.value(PROVIDER_KEY)),
            normalize_target(settings.value(TARGET_KEY)),
        )

    def _ensure_translate_dialog(self) -> TranslationDialog:
        if self._translate_dialog is None:
            dialog = TranslationDialog(self, theme=self._theme)
            dialog.retranslate_requested.connect(self._on_retranslate_requested)
            self._translate_dialog = dialog
        return self._translate_dialog

    def _run_translation(
        self, selection: str, provider: str, target: str, force: bool = False
    ):
        dialog = self._ensure_translate_dialog()
        dialog.start(selection, provider, target)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        # A repeat of the same request costs nothing and returns instantly.
        if not force:
            cached = cached_translation(selection, provider, target)
            if cached is not None:
                self._translate_request_id += 1  # invalidate anything in flight
                dialog.show_result(cached, provider, target, from_cache=True)
                return

        self._translate_request_id += 1
        request_id = self._translate_request_id
        start_translation(
            request_id,
            selection,
            provider=provider,
            target=target,
            api_key=str(QSettings(_ORG, _APP).value(DEEPL_KEY, "") or ""),
            on_finished=lambda rid, result: self._on_translation_done(
                rid, result, provider, target
            ),
            on_failed=self._on_translation_failed,
        )

    def _on_retranslate_requested(self, provider: str, target: str, force: bool):
        """The translation window's own service / language pickers."""
        dialog = self._translate_dialog
        if dialog is None or not dialog.source_text():
            return
        # Remember the choice so the next right-click uses it too.
        settings = QSettings(_ORG, _APP)
        settings.setValue(PROVIDER_KEY, provider)
        settings.setValue(TARGET_KEY, target)
        self._run_translation(dialog.source_text(), provider, target, force=force)

    def _on_translation_done(
        self, request_id: int, result: str, provider: str, target: str
    ):
        if request_id != self._translate_request_id:
            return  # superseded by a newer selection
        if self._translate_dialog is not None:
            self._translate_dialog.show_result(result, provider, target)

    def _on_translation_failed(self, request_id: int, message: str):
        if request_id != self._translate_request_id:
            return
        if self._translate_dialog is not None:
            self._translate_dialog.show_error(message)

    def _refresh_icons(self):
        icon_color = self._theme.text_muted
        disabled_color = self._theme.text_subtle
        self._wysiwyg_btn.setEnabled(
            self._edit_mode and self._current_kind == "markdown"
        )
        self._wysiwyg_btn.setChecked(
            self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        )
        for button in (
            self._sidebar_btn,
            self._open_btn,
            self._search_btn,
            self._reload_btn,
            self._mermaid_btn,
            self._wysiwyg_btn,
            self._export_btn,
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

        self._toolbar_utilities.apply_theme(
            self._theme, theme_name=self._theme_name
        )
        theme_action_text = (
            "切換為淺色模式" if self._theme_name == "dark" else "切換為深色模式"
        )
        self._theme_action.setText(theme_action_text)

        self._search_prev_btn.setIcon(svg_icon("chevron-left", icon_color, 18))
        self._search_next_btn.setIcon(svg_icon("chevron-right", icon_color, 18))
        self._search_close_btn.setIcon(svg_icon("x", icon_color, 18))
        if hasattr(self, "_format_menu"):
            self._update_format_menu_actions()

    def _toggle_theme(self):
        session_state.toggle_theme(self)

    def _set_update_state(self, state: str, *, version: str = "") -> None:
        self._toolbar_utilities.set_update_state(state, version=version)
        if hasattr(self, "_update_action"):
            self._update_action.setEnabled(
                state not in (UPDATE_CHECKING, UPDATE_DOWNLOADING)
            )

    def _on_update_button_clicked(self):
        if self._toolbar_utilities.update_state in (
            UPDATE_CHECKING,
            UPDATE_DOWNLOADING,
        ):
            return
        if self._available_update is not None:
            update_flow.prompt_for_update(self, self._available_update)
            return
        self._check_for_updates(manual=True)

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

        self._search_input = _SearchLineEdit()
        self._search_input.setPlaceholderText("搜尋目前文件")
        self._search_input.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._search_input.textChanged.connect(self._on_search_text_changed)
        self._search_input.returnPressed.connect(self._search_next)
        self._search_input.previous_requested.connect(self._search_prev)
        self._search_input.cancel_requested.connect(self._close_search)

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
            if self._active_edit_backend == edit_backend.WYSIWYG_BACKEND:
                if self._wysiwyg_view is not None:
                    self._wysiwyg_view.open_find()
                return
            self._toggle_editor_search()
            return
        if not self._current_file:
            return
        if self._search_bar.isHidden():
            self._search_bar.show()
            self._set_search_escape_enabled(True)
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
        self._set_search_escape_enabled(True)
        changed = self._search_input.text() != query
        self._search_input.setText(query)
        if not changed:
            self._on_search_text_changed(query)
        self._renderer.find_text_after_load(query)
        self._search_input.setFocus()
        self._search_input.selectAll()
        self.statusBar().showMessage(f"已開啟第 {line_number} 行的搜尋結果", 3000)

    def _close_search(self):
        self._set_search_escape_enabled(False)
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
        self._ed_find = _SearchLineEdit()
        self._ed_find.setPlaceholderText("尋找")
        self._ed_find.textChanged.connect(lambda _t: self._update_editor_match_count())
        self._ed_find.returnPressed.connect(self._editor_find_next)
        self._ed_find.previous_requested.connect(self._editor_find_prev)
        self._ed_find.cancel_requested.connect(self._close_editor_search)
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
        self._ed_replace = _SearchLineEdit(previous_enabled=False)
        self._ed_replace.setPlaceholderText("取代為")
        self._ed_replace.returnPressed.connect(self._editor_replace_one)
        self._ed_replace.cancel_requested.connect(self._close_editor_search)
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
            self._set_search_escape_enabled(True)
            self._ed_find.setFocus()
            self._ed_find.selectAll()
            self._update_editor_match_count()
        else:
            self._close_editor_search()

    def _close_editor_search(self):
        self._set_search_escape_enabled(False)
        self._editor_search_bar.hide()
        self._editor.setFocus()

    def _set_search_escape_enabled(self, enabled: bool):
        """Publish a fresh token so delayed WebEngine keys cannot go stale."""
        self._search_escape_counter += 1
        generation = self._search_escape_counter if enabled else 0
        self._active_search_escape_generation = generation
        self._renderer.set_search_escape_generation(generation)
        self._edit_preview.set_search_escape_generation(generation)

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

    def _on_pdf_outline_ready(self, generation: int, path, entries):
        if (
            self._current_kind != "pdf"
            or self._current_file is None
            or generation != self._pdf_view.load_generation()
            or Path(path) != Path(self._current_file)
        ):
            return
        self._panel.toc.update_outline(entries)

    def _on_pdf_wheel_zoom_changed(self, factor: float):
        if self._current_kind != "pdf":
            return
        self._content_zoom = max(0.5, min(3.0, float(factor)))
        self.statusBar().showMessage(
            f"縮放：{round(self._content_zoom * 100)}%", 2000
        )
        self._pending_pdf_wheel_zoom = self._content_zoom
        self._pdf_zoom_sync_timer.start()

    def _commit_pdf_wheel_zoom(self):
        self._pdf_zoom_sync_timer.stop()
        factor = self._pending_pdf_wheel_zoom
        self._pending_pdf_wheel_zoom = None
        if factor is not None:
            # PdfView already owns the live wheel zoom. Do not send the same
            # value back into it: a newly-arrived frame may be pending while
            # this older idle timer fires, and set_zoom_factor would cancel it.
            session_state.apply_zoom(self, factor, sync_pdf=False)

    def _flush_pdf_zoom_pipeline(self):
        """Persist the last PDF wheel frame before leaving its document."""
        if self._current_kind == "pdf":
            # This emits zoom_changed while the current document is still the
            # PDF, so the guarded handler can retain the final factor.
            self._pdf_view.flush_pending_wheel_zoom()
        if self._pending_pdf_wheel_zoom is not None:
            self._commit_pdf_wheel_zoom()

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
        if not self._current_file:
            return
        if is_text(self._current_file):
            return  # plain text is editor-only; no preview / split to go to
        if not is_markdown(self._current_file):
            return  # PDFs stay in plain preview
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
        state = self._active_editor_state()
        if state is not None:
            state["view_mode"] = mode
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

    # ── Markdown formatting (toolbar, slash menu, and shortcuts) ───────

    def _format_bold(self):
        self._apply_format_action("bold")

    def _format_italic(self):
        self._apply_format_action("italic")

    def _format_link(self):
        self._apply_format_action("link")

    def _format_ordered_list(self):
        self._apply_format_action("ordered_list")

    def _format_bullet_list(self):
        self._apply_format_action("bullet_list")

    def _apply_format_action(self, action: str):
        """Run a format action; no-op outside Markdown edit mode."""
        if not self._edit_mode or self._current_kind != "markdown":
            return
        if self._editor._plain_text_mode:
            return
        if self._active_edit_backend == edit_backend.WYSIWYG_BACKEND:
            # These commands mutate the hidden QPlainTextEdit directly; Vditor
            # has its own toolbar/shortcuts for the same formatting and would
            # never see this edit, silently diverging from what is saved.
            return
        if action == "image":
            self._insert_image_via_dialog()
            return
        if action == "attachment":
            self._insert_attachment_via_dialog()
            return
        if action == "template":
            self._insert_template()
            return
        if action == "recent_resource":
            self._insert_recent_resource()
            return
        apply_format_action(self._editor, action)
        self._editor.setFocus()

    def _insert_image_via_dialog(self):
        """Toolbar 圖片 button: pick an image file, import it, insert a link."""
        doc_path = self._editor.document_path()
        if not doc_path:
            self.statusBar().showMessage("請先儲存文件才能貼入圖片", 4000)
            return
        picked, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "插入圖片",
            str(Path(doc_path).parent),
            "圖片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)",
        )
        if not picked:
            return
        try:
            rel = import_image_file(picked, doc_path)
        except OSError as exc:
            QMessageBox.warning(self, "插入圖片", f"無法匯入圖片：\n{exc}")
            return
        link = markdown_image_link(rel)
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(link)
        cursor.endEditBlock()
        self._editor.setTextCursor(cursor)
        self._record_recent_resource(link)
        self._editor.setFocus()

    @staticmethod
    def _settings_json_list(key: str) -> list[str]:
        raw = QSettings(_ORG, _APP).value(key, "") or ""
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            values = []
        return [str(value) for value in values if str(value).strip()]

    @staticmethod
    def _remember_setting_value(key: str, value: str, limit: int) -> None:
        clean = str(value).strip()
        if not clean:
            return
        values = [
            item for item in MainWindow._settings_json_list(key)
            if item != clean
        ]
        values.insert(0, clean)
        QSettings(_ORG, _APP).setValue(
            key, json.dumps(values[:limit], ensure_ascii=False)
        )

    @staticmethod
    def _recent_resource_entries() -> list[RecentResource]:
        raw = QSettings(_ORG, _APP).value(_RECENT_RESOURCES_KEY, "") or ""
        return decode_recent_resources(raw)

    def _record_recent_resource(self, markdown_link: str) -> None:
        record = resource_from_markdown(
            markdown_link, self._editor.document_path()
        )
        if record is None:
            return
        resources = remember_recent_resource(
            self._recent_resource_entries(), record, 10
        )
        QSettings(_ORG, _APP).setValue(
            _RECENT_RESOURCES_KEY, encode_recent_resources(resources)
        )

    def _insert_attachment_via_dialog(self, _checked=False) -> None:
        if not (
            self._edit_mode
            and self._current_kind == "markdown"
            and self._editor.document_path()
        ):
            self.statusBar().showMessage(
                "請先開啟已儲存的 Markdown 文件", 4000
            )
            return
        picked, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "加入附件",
            str(self._current_file.parent),
            "所有檔案 (*.*)",
        )
        if not picked:
            return
        try:
            relative = import_attachment_file(picked, self._current_file)
            link = markdown_attachment_link(relative)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "加入附件", f"無法加入附件：\n{exc}")
            return
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(link)
        cursor.endEditBlock()
        self._editor.setTextCursor(cursor)
        self._record_recent_resource(link)
        self._editor.setFocus()

    def _insert_recent_resource(self, _checked=False) -> None:
        if not (self._edit_mode and self._current_kind == "markdown"):
            return
        resources = self._recent_resource_entries()
        if not resources:
            QMessageBox.information(
                self, "最近資源", "目前還沒有最近使用的圖片或附件。"
            )
            return
        labels = [resource.display_text for resource in resources]
        choice, ok = QInputDialog.getItem(
            self,
            "最近資源",
            "選擇要再次插入的資源：",
            labels,
            0,
            False,
        )
        if not ok:
            return
        resource = resources[labels.index(choice)]
        link = resource.markdown_link
        if not resource.absolute_path:
            QMessageBox.information(
                self,
                "舊版資源紀錄",
                "這筆舊紀錄沒有來源位置，無法保證跨筆記連結正確。\n"
                "請改用「加入附件」或「圖片」重新選擇檔案。",
            )
            return
        if resource.absolute_path:
            source = Path(resource.absolute_path)
            if not source.is_file():
                QMessageBox.warning(
                    self,
                    "找不到資源",
                    f"原始資源已移動或刪除：\n{source}",
                )
                return
            try:
                if resource.kind == "image":
                    relative = import_image_file(source, self._current_file)
                    link = markdown_image_link(relative, resource.label)
                else:
                    relative = import_attachment_file(source, self._current_file)
                    link = markdown_attachment_link(relative, resource.label)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(
                    self, "插入資源失敗", f"無法匯入資源：\n{exc}"
                )
                return
        cursor = self._editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(link)
        cursor.endEditBlock()
        self._editor.setTextCursor(cursor)
        # Keep the selected record's canonical source instead of recording the
        # freshly copied per-note asset as a second history entry.
        resources = remember_recent_resource(resources, resource, 10)
        QSettings(_ORG, _APP).setValue(
            _RECENT_RESOURCES_KEY, encode_recent_resources(resources)
        )
        self._editor.setFocus()

    def _open_mermaid_workspace(self):
        dialog = MermaidWorkspaceDialog(theme_name=self._theme_name, parent=self)
        dialog.exec()

    def _edit_mermaid_diagram(self, _checked=False, *, _source_ready=False):
        if not _source_ready:
            self._with_source_markdown_editor(
                lambda: self._edit_mermaid_diagram(_source_ready=True),
                purpose="編輯 Mermaid 圖表",
            )
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

    def _insert_mermaid_diagram(self, _checked=False, *, _source_ready=False):
        if not _source_ready:
            self._with_source_markdown_editor(
                lambda: self._insert_mermaid_diagram(_source_ready=True),
                purpose="插入 Mermaid 圖表",
            )
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

    def _with_source_markdown_editor(self, continuation, *, purpose: str) -> None:
        """Run a source-only command after safely switching away from WYSIWYG."""
        if not self._current_file or not is_markdown(self._current_file):
            QMessageBox.information(
                self,
                "Mermaid",
                "Open a Markdown file before editing Mermaid diagrams.",
            )
            return
        if not self._edit_mode:
            if not self._active_path:
                return
            state = self._tab_state.setdefault(self._active_path, {})
            state["edit_backend"] = edit_backend.SPLIT_BACKEND
            if self._enter_edit_mode():
                continuation()
            return
        if self._active_edit_backend != edit_backend.WYSIWYG_BACKEND:
            continuation()
            return
        if not self._active_path:
            return
        state = self._tab_state.get(self._active_path)
        if state is None:
            return

        def _switch_to_source() -> None:
            state["edit_backend"] = edit_backend.SPLIT_BACKEND
            if self._activate_editor_state(state, self._view_mode):
                continuation()

        self._request_live_wysiwyg_snapshot(
            _switch_to_source,
            purpose=purpose,
        )

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

    def _active_editor_state(self) -> dict | None:
        if not self._active_path:
            return None
        return self._tab_state.get(self._active_path)

    def _stash_active_editor_state(
        self, *, snapshot: bool = True, sync_wysiwyg: bool = True
    ) -> bool:
        if not self._edit_mode or not self._active_path:
            return True
        document = self._editor.document()
        if (
            document is self._editor._parking_document
            or document.parent() is not None
        ):
            # Compatibility callers may toggle ``_edit_mode`` directly while
            # no real per-tab editor exists.  A clean parking document carries
            # no state.  If such a caller marked it dirty, promote a clone to
            # a real parentless tab buffer; the permanent parking document
            # itself must never be adopted or destroyed by tab state.
            if not document.isModified():
                return True
            old_cursor = self._editor.textCursor()
            promoted = self._editor.create_buffer_document(
                document.toPlainText()
            )
            promoted.setModified(True)
            self._editor.use_buffer_document(
                promoted,
                plain_text_mode=self._current_kind == "text",
                document_path=self._current_file,
            )
            cursor = self._editor.textCursor()
            maximum = max(0, promoted.characterCount() - 1)
            anchor = max(0, min(old_cursor.anchor(), maximum))
            position = max(0, min(old_cursor.position(), maximum))
            cursor.setPosition(anchor)
            cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
            self._editor.setTextCursor(cursor)
            document = promoted
        state = self._tab_state.setdefault(self._active_path, {})
        cursor = self._editor.textCursor()
        state.update(
            {
                "kind": self._current_kind,
                "view_mode": self._view_mode,
                "editor_document": document,
                "cursor": cursor.position(),
                "anchor": cursor.anchor(),
                "editor_scroll": self._editor.verticalScrollBar().value(),
                "editing_encoding": self._editing_encoding,
                "editing_newline": self._editing_newline,
                "source_signature": (
                    state["source_signature"]
                    if "source_signature" in state
                    else self._file_signature(self._current_file)
                ),
                "preview_scroll_ratio": self._preview_scroll_ratio,
            }
        )
        if snapshot and document.isModified():
            self._save_recovery_for_state(self._active_path, state)
        return True

    def _activate_editor_state(self, state: dict, mode: str) -> bool:
        document = state.get("editor_document")
        if not isinstance(document, QTextDocument):
            return False
        # Any real entry into the editor (Ctrl+E, double-click resume, tab
        # restore, backend toggle) resumes live editing, so the tab is no
        # longer merely "parked" behind a preview.
        state.pop("wysiwyg_parked", None)
        plain_text = self._current_kind == "text"
        if plain_text:
            mode = view_mode.EDIT
        self._editing_encoding = str(
            state.get("editing_encoding") or "utf-8"
        )
        self._editing_newline = str(state.get("editing_newline") or "\n")
        self._editor.use_buffer_document(
            document,
            plain_text_mode=plain_text,
            document_path=self._current_file,
        )
        self._editor.set_wikilink_candidates(
            [] if plain_text else self._link_index.completion_candidates
        )
        max_position = max(0, document.characterCount() - 1)
        cursor = self._editor.textCursor()
        anchor = max(0, min(int(state.get("anchor", 0)), max_position))
        position = max(0, min(int(state.get("cursor", anchor)), max_position))
        cursor.setPosition(anchor)
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        self._editor.setTextCursor(cursor)

        self._view_mode = mode
        state["view_mode"] = mode

        backend = edit_backend.backend_allows(
            state.get("edit_backend", self._edit_backend),
            self._current_file.suffix if self._current_file else None,
            is_plain_text=plain_text,
        )
        state["edit_backend"] = backend
        self._active_edit_backend = backend
        # Vditor owns undo/redo while visible. Keeping Qt's shadow undo stack
        # disabled avoids rebuilding/clearing a large QTextDocument on every
        # debounced delta; it is re-enabled once source editing resumes.
        was_modified = document.isModified()
        document.setUndoRedoEnabled(
            backend != edit_backend.WYSIWYG_BACKEND
        )
        document.setModified(was_modified)
        self._editor.set_markdown_services_suspended(
            backend == edit_backend.WYSIWYG_BACKEND
        )
        document.setModified(was_modified)

        self._renderer.set_inline_edit_enabled(False)
        self._preview_scroll_ratio = float(
            state.get("preview_scroll_ratio", 0.0) or 0.0
        )
        self._apply_split_visibility()
        self._close_search()
        self._search_btn.setEnabled(plain_text)
        self._reload_btn.setEnabled(plain_text)
        self._export_btn.setEnabled(False)

        if backend == edit_backend.WYSIWYG_BACKEND:
            self._format_toolbar.hide()
            self._editor_status.hide()
            view = self._ensure_wysiwyg_view()
            self._stack.setCurrentWidget(view)
            markdown = document.toPlainText()
            self._wysiwyg_shadow_text = markdown
            self._wysiwyg_shadow_qt_length = max(
                0, document.characterCount() - 1
            )
            self._wysiwyg_shadow_revision = 0
            set_document_path = getattr(view, "set_document_path", None)
            if callable(set_document_path):
                set_document_path(self._current_file)
            view.load_markdown(markdown)
            view.page().setZoomFactor(self._content_zoom)
            view.setFocus()
        else:
            self._format_toolbar.setVisible(not plain_text)
            self._editor_status.show()
            self._stack.setCurrentWidget(self._editor_split)
            if mode == view_mode.SPLIT:
                self._update_preview()
            scroll = max(0, int(state.get("editor_scroll", 0) or 0))
            QTimer.singleShot(
                0,
                lambda value=scroll: self._editor.verticalScrollBar().setValue(
                    value
                ),
            )
            self._editor.setFocus()

        self._update_editor_status_document()
        self._update_format_menu_actions()
        self._refresh_icons()
        self._update_dirty_ui()
        return True

    def _ensure_wysiwyg_view(self) -> WysiwygView:
        """Lazily build the WYSIWYG stack page (opt-in: most sessions never do)."""
        if self._wysiwyg_view is None:
            self._wysiwyg_view = WysiwygView()
            content_detailed = getattr(
                self._wysiwyg_view, "content_changed_detailed", None
            )
            if content_detailed is not None:
                content_detailed.connect(self._on_wysiwyg_content_delta)
            else:
                self._wysiwyg_view.content_changed.connect(
                    self._on_wysiwyg_content_changed
                )
            self._wysiwyg_view.save_requested.connect(self._save_edits)
            save_detailed = getattr(
                self._wysiwyg_view, "save_with_content_detailed", None
            )
            if save_detailed is not None:
                save_detailed.connect(self._on_wysiwyg_save_with_content_delta)
            else:
                save_with_content = getattr(
                    self._wysiwyg_view, "save_with_content_requested", None
                )
                if save_with_content is not None:
                    save_with_content.connect(self._on_wysiwyg_save_with_content)
            self._wysiwyg_view.esc_requested.connect(self._on_wysiwyg_esc)
            self._wysiwyg_view.toolbar_action.connect(
                self._on_wysiwyg_toolbar_action
            )
            self._wysiwyg_view.context_menu_requested.connect(
                self._on_wysiwyg_context_menu
            )
            self._wysiwyg_view.apply_theme(self._theme)
            self._wysiwyg_view.page().setZoomFactor(self._content_zoom)
            self._stack.addWidget(self._wysiwyg_view)
        return self._wysiwyg_view

    # ── v4: custom Vditor toolbar buttons + right-click menu ────────────

    def _on_wysiwyg_toolbar_action(self, name: str) -> None:
        handlers = {
            "save": self._save_edits,
            "export_pdf": self._export_pdf,
            "export_docx": self._export_docx,
            "export_html": self._export_html,
            "insert_image": self._wysiwyg_insert_image,
            "insert_attachment": self._wysiwyg_insert_attachment,
            "toggle_theme": self._toggle_theme,
            "toggle_source": self._toggle_edit_backend,
            "open_graph": self._open_graph_view,
            "show_export_menu": self._show_wysiwyg_export_menu,
        }
        handler = handlers.get(name)
        if handler is not None:
            handler()

    def _show_wysiwyg_export_menu(self) -> None:
        view = self._wysiwyg_view
        if view is None:
            return
        menu = QMenu(self)
        menu.addAction("匯出 PDF…", self._export_pdf)
        menu.addAction("匯出 PowerPoint…", self._export_pptx)
        menu.addAction("匯出 Word…", self._export_docx)
        menu.addAction("匯出 HTML…", self._export_html)

        generation = getattr(view, "_generation", None)
        anchor_script = """
            (function () {
              var toolbar = document.querySelector('.vditor-toolbar');
              if (!toolbar) return null;
              var button = toolbar.querySelector('[data-type="export"]');
              if (!button) return null;
              var rect = button.getBoundingClientRect();
              return JSON.stringify({x: rect.left, y: rect.bottom});
            })();
        """

        def _show_at_anchor(result=None) -> None:
            if (
                self._wysiwyg_view is not view
                or self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
                or (
                    generation is not None
                    and getattr(view, "_generation", None) != generation
                )
            ):
                return
            anchor = result
            if isinstance(result, str):
                try:
                    anchor = json.loads(result)
                except (TypeError, ValueError):
                    anchor = None
            local_pos = None
            if isinstance(anchor, dict):
                try:
                    local_pos = self._wysiwyg_client_point_to_local(
                        view, float(anchor["x"]), float(anchor["y"])
                    )
                except (KeyError, TypeError, ValueError, OverflowError):
                    local_pos = None
            if local_pos is None:
                # Compatibility fallback for an older/custom Vditor DOM: the
                # toolbar action originates under the pointer, which is a much
                # better anchor than the old fixed centre-of-view coordinate.
                cursor_pos = view.mapFromGlobal(QCursor.pos())
                if view.rect().contains(cursor_pos):
                    local_pos = cursor_pos
                else:
                    toolbar_bottom = self._wysiwyg_client_point_to_local(
                        view, 0, 38
                    ).y()
                    local_pos = QPoint(
                        max(0, view.width() - menu.sizeHint().width()),
                        toolbar_bottom,
                    )
            menu.exec(view.mapToGlobal(local_pos))

        page = view.page()
        run_javascript = getattr(page, "runJavaScript", None)
        if not callable(run_javascript):
            _show_at_anchor()
            return
        try:
            run_javascript(anchor_script, _show_at_anchor)
        except TypeError:
            # Lightweight/older page doubles may expose only the one-argument
            # overload. Keep the menu usable without changing the bridge API.
            _show_at_anchor()

    def _wysiwyg_client_point_to_local(
        self, view: QWidget, x: float, y: float
    ) -> QPoint:
        """Map JavaScript client coordinates into QWebEngineView coordinates.

        ``clientX/clientY`` and ``getBoundingClientRect`` are expressed in CSS
        pixels. QWebEngine's page zoom scales those pixels inside the widget;
        Qt's local coordinates are already device-independent, so the screen
        device-pixel ratio must not be applied a second time.
        """
        factor = self._content_zoom
        try:
            zoom_factor = getattr(view.page(), "zoomFactor", None)
            if callable(zoom_factor):
                factor = float(zoom_factor())
            else:
                factor = float(factor)
        except (TypeError, ValueError, RuntimeError):
            factor = 1.0
        if not math.isfinite(factor) or factor <= 0:
            factor = 1.0
        try:
            local = QPoint(round(float(x) * factor), round(float(y) * factor))
        except (TypeError, ValueError, OverflowError):
            local = QPoint()
        bounds = view.rect()
        if not bounds.isEmpty():
            local.setX(max(bounds.left(), min(local.x(), bounds.right())))
            local.setY(max(bounds.top(), min(local.y(), bounds.bottom())))
        return local

    def _on_wysiwyg_context_menu(self, x: int, y: int) -> None:
        view = self._wysiwyg_view
        if view is None:
            return
        page = view.page()
        menu = QMenu(self)

        copy_act = page.action(QWebEnginePage.WebAction.Copy)
        copy_act.setText("複製")
        menu.addAction(copy_act)
        paste_act = page.action(QWebEnginePage.WebAction.Paste)
        paste_act.setText("貼上")
        menu.addAction(paste_act)
        paste_plain_act = page.action(QWebEnginePage.WebAction.PasteAndMatchStyle)
        paste_plain_act.setText("貼上為純文字")
        menu.addAction(paste_plain_act)

        menu.addSeparator()
        export_pdf_act = menu.addAction("匯出 PDF…")
        export_pdf_act.triggered.connect(self._export_pdf)
        export_docx_act = menu.addAction("匯出 Word…")
        export_docx_act.triggered.connect(self._export_docx)
        export_html_act = menu.addAction("匯出 HTML…")
        export_html_act.triggered.connect(self._export_html)

        menu.addSeparator()
        insert_image_act = menu.addAction("插入圖片…")
        insert_image_act.triggered.connect(self._wysiwyg_insert_image)
        reveal_act = menu.addAction("在資料夾中顯示")
        reveal_act.setEnabled(bool(self._current_file))
        reveal_act.triggered.connect(
            lambda _checked=False: self._reveal_path(self._current_file)
        )
        menu.addAction(reveal_act)

        local_pos = self._wysiwyg_client_point_to_local(view, x, y)
        menu.exec(view.mapToGlobal(local_pos))

    def _wysiwyg_insert_image(self, _checked=False) -> None:
        if (
            self._wysiwyg_view is None
            or self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
        ):
            return
        doc_path = self._editor.document_path()
        if not doc_path:
            self.statusBar().showMessage("請先儲存文件才能貼入圖片", 4000)
            return
        picked, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "插入圖片",
            str(Path(doc_path).parent),
            "圖片 (*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg)",
        )
        if not picked:
            return
        try:
            rel = import_image_file(picked, doc_path)
        except OSError as exc:
            QMessageBox.warning(self, "插入圖片", f"無法匯入圖片：\n{exc}")
            return
        link = markdown_image_link(rel)
        self._wysiwyg_view.insert_value(link)
        self._record_recent_resource(link)

    def _wysiwyg_insert_attachment(self, _checked=False) -> None:
        if (
            self._wysiwyg_view is None
            or self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
            or not self._current_file
        ):
            return
        picked, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "加入附件",
            str(self._current_file.parent),
            "所有檔案 (*.*)",
        )
        if not picked:
            return
        try:
            relative = import_attachment_file(picked, self._current_file)
            link = markdown_attachment_link(relative)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "加入附件", f"無法加入附件：\n{exc}")
            return
        self._wysiwyg_view.insert_value(link)
        self._record_recent_resource(link)

    # ── v2 "click to edit": PREVIEW double-click -> straight into WYSIWYG ──

    def _on_preview_wysiwyg_edit_requested(self, start_line: int) -> None:
        """A rendered block was double-clicked with preview_double_click=="wysiwyg"."""
        if self._edit_mode or not self._active_path:
            return
        if not self._current_file or not is_markdown(self._current_file):
            return  # .txt/PDF never reach here (renderer skips the script), belt & suspenders
        if not edit_backend.preview_double_click_enters_wysiwyg(
            self._preview_double_click, is_markdown=True
        ):
            return
        state = self._tab_state.setdefault(self._active_path, {})
        # Force WYSIWYG for *this* entry regardless of the tab's remembered
        # backend -- the whole point of double-clicking is landing straight
        # in the WYSIWYG editor, Office-Viewer style.
        state["edit_backend"] = edit_backend.WYSIWYG_BACKEND
        snippet = self._source_line_snippet(max(0, int(start_line)))
        self._request_view_mode(view_mode.EDIT)
        if (
            snippet
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
            and self._wysiwyg_view is not None
        ):
            # Best-effort cursor placement (v2 spec accepts this may fail
            # silently); give Vditor's own render a moment to settle first.
            QTimer.singleShot(
                60, lambda v=self._wysiwyg_view, s=snippet: v.focus_near_text(s)
            )

    def _source_line_snippet(self, line: int) -> str:
        """Best-effort text near *line* of the active document, for cursor placement."""
        state = self._tab_state.get(self._active_path) if self._active_path else None
        document = state.get("editor_document") if state else None
        text = (
            document.toPlainText()
            if isinstance(document, QTextDocument)
            else (self._current_file.read_text(encoding="utf-8", errors="ignore")
                  if self._current_file and self._current_file.exists() else "")
        )
        lines = text.split("\n")
        if 0 <= line < len(lines):
            snippet = lines[line].strip()
            if snippet:
                return snippet[:80]
        return ""

    def _on_wysiwyg_esc(self) -> None:
        """Handle a legacy page's Esc request without losing its dirty buffer.

        Office Viewer 4.2 glue no longer sends this request for a clean Esc;
        the slot remains as a fail-safe for a stale/older page. Deliberately
        NOT ``_exit_edit_mode()``/``_confirm_discard_edits()``:
        that pair pops a Save/Discard/Cancel dialog and then truly discards
        the buffer on "Discard" -- the v2 spec calls for a silent round-trip
        instead, matching what already happens when you switch to another
        tab mid-edit (see ``_stash_active_editor_state``): the document stays
        parked in ``_tab_state`` exactly as dirty as it was.
        """
        if self._active_edit_backend != edit_backend.WYSIWYG_BACKEND:
            return
        if self._wysiwyg_view is None or not self._active_path:
            return
        active_state = self._tab_state.get(self._active_path)
        if active_state is None:
            return
        self._request_live_wysiwyg_snapshot(
            lambda state=active_state: self._leave_wysiwyg_ui_keeping_buffer(
                state
            ),
            purpose="返回預覽",
        )

    def _leave_wysiwyg_ui_keeping_buffer(self, state: dict) -> None:
        """Swap the UI from WYSIWYG back to PREVIEW without touching the buffer.

        Mirrors ``_leave_edit_ui`` minus the ``_discard_tab_buffer`` call: the
        tab's ``editor_document`` (and its dirty flag) is left exactly as-is
        so re-entering the editor shows the same unsaved text.

        Deliberately does NOT set ``state["view_mode"]`` to PREVIEW: that
        field means "is there a live editing session to resume" to
        ``_enter_edit_mode``/``_load_document`` (``restore_editor``), not
        "what is currently on screen" -- exactly the same distinction
        already at play for a *different* tab parked mid-edit while this one
        is active. Only the window's own ``_view_mode`` (what this tab is
        showing *right now*) changes to PREVIEW.
        """
        # Mark the tab as "parked": an editing session is still live in
        # ``editor_document`` (state["view_mode"] stays EDIT/SPLIT, per the
        # docstring above), but the *screen* is showing PREVIEW.  Every path
        # that must not treat this tab as a plain, editor-owned-nothing tab
        # -- restoring it on a tab round-trip, inline preview edits, the
        # task-checkbox write-back, the dirty-title marker -- keys off this
        # flag instead of state["view_mode"] or self._view_mode alone.
        state["wysiwyg_parked"] = True
        self._editor.release_buffer_document()
        self._editor.set_markdown_services_suspended(False)
        self._view_mode = view_mode.PREVIEW
        self._renderer.set_inline_edit_enabled(True)
        self._renderer.set_preview_double_click_mode(self._preview_double_click)
        self._preview_timer.stop()
        self._editor_status_timer.stop()
        self._recovery_timer.stop()
        self._editor_search_bar.hide()
        self._format_toolbar.hide()
        self._editor_status.show()
        self._active_edit_backend = edit_backend.SPLIT_BACKEND
        self._set_search_escape_enabled(False)
        is_md = bool(self._current_file and is_markdown(self._current_file))
        self._search_btn.setEnabled(is_md)
        self._reload_btn.setEnabled(bool(self._current_file))
        self._export_btn.setEnabled(is_md)
        self._stack.setCurrentWidget(self._renderer)
        self._renderer.setFocus()
        self._update_editor_status_document()
        self._update_dirty_ui()
        self._refresh_icons()

    def _request_live_wysiwyg_snapshot(
        self, continuation, *, purpose: str
    ) -> bool:
        """Freeze the visible editor, apply one live snapshot, then continue.

        QWebEngine JavaScript is asynchronous. Transition paths must therefore
        be continuations rather than nested QEventLoops: the latter allow a
        tab/close signal to re-enter while the old document is still waiting
        for its callback.
        """
        view = self._wysiwyg_view
        if (
            self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
            or view is None
        ):
            continuation()
            return True

        request = getattr(view, "request_markdown_snapshot_envelope", None)
        if not callable(request):
            # Compatibility for lightweight test doubles. Their helper emits
            # content_changed synchronously, so a flush is sufficient.
            flush = getattr(view, "flush_pending_edits", None)
            if callable(flush):
                flush()
            continuation()
            return True
        if getattr(view, "_ready", True) is False:
            # Before ready the user cannot have edited the page; the queued
            # Python document is already the complete save-worthy value.
            continuation()
            return True
        if self._wysiwyg_snapshot_busy:
            self.statusBar().showMessage(
                "編輯器正在完成上一個操作，請稍候。", 2500
            )
            return False

        self._wysiwyg_snapshot_busy = True
        self._wysiwyg_snapshot_token += 1
        operation_token = self._wysiwyg_snapshot_token
        path = self._active_path
        state = self._tab_state.get(path) if path else None
        generation = getattr(view, "_generation", None)
        snapshot_token = 0
        completed = False
        self._tab_bar.setEnabled(False)
        view.setEnabled(False)

        timer = QTimer(self)
        timer.setSingleShot(True)

        def _context_is_current() -> bool:
            return (
                operation_token == self._wysiwyg_snapshot_token
                and path == self._active_path
                and self._tab_state.get(path) is state
                and self._wysiwyg_view is view
                and self._active_edit_backend
                == edit_backend.WYSIWYG_BACKEND
                and (
                    generation is None
                    or getattr(view, "_generation", None) == generation
                )
            )

        def _unlock() -> None:
            self._wysiwyg_snapshot_busy = False
            self._tab_bar.setEnabled(True)
            if self._wysiwyg_view is view:
                view.setEnabled(True)

        def _abort(message: str) -> None:
            nonlocal completed
            if completed:
                return
            completed = True
            timer.stop()
            timer.deleteLater()
            cancel = getattr(view, "cancel_snapshot", None)
            if callable(cancel):
                cancel(snapshot_token)
            _unlock()
            self.statusBar().showMessage(message, 5000)

        def _continue_after_ack(result=False) -> None:
            nonlocal completed
            if completed:
                return
            if result is not True or not _context_is_current():
                _abort(
                    "文件狀態已變更，已取消%s以避免套用到錯誤分頁。"
                    % purpose
                )
                return
            completed = True
            timer.stop()
            timer.deleteLater()
            _unlock()
            continuation()

        def _received(envelope) -> None:
            nonlocal snapshot_token
            if completed:
                return
            if (
                not _context_is_current()
                or not isinstance(envelope, dict)
                or not isinstance(envelope.get("markdown"), str)
            ):
                _abort("文件狀態已變更，已取消%s以避免套用到錯誤分頁。" % purpose)
                return

            markdown = envelope["markdown"]
            snapshot_token = int(envelope.get("token", 0) or 0)
            self._on_wysiwyg_content_delta(
                markdown,
                int(envelope.get("start", 0)),
                int(envelope.get("deleteCount", 0)),
                str(envelope.get("inserted", "")),
                int(envelope.get("baseRevision", 0)),
                int(envelope.get("length", 0)),
            )
            acknowledge = getattr(view, "acknowledge_markdown", None)
            if callable(acknowledge) and snapshot_token:
                acknowledge(
                    markdown,
                    snapshot_token,
                    int(envelope.get("revision", 0)),
                    _continue_after_ack,
                )
            else:
                _continue_after_ack(True)

        timer.timeout.connect(
            lambda: _abort("編輯器尚未回應，已取消%s以避免遺失內容。" % purpose)
        )
        timer.start(2500)
        request(_received)
        return True

    def _on_wysiwyg_save_with_content(self, markdown: str) -> None:
        """Save button/Ctrl+S path carrying the live snapshot in one message."""
        if (
            self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
            or not self._current_file
            or not self._active_path
        ):
            return
        self._on_wysiwyg_content_changed(markdown)
        saved_markdown = self._editor.document().toPlainText()
        if self._save_tab_buffer(self._active_path) and self._wysiwyg_view is not None:
            mark_saved = getattr(self._wysiwyg_view, "mark_saved", None)
            if callable(mark_saved):
                mark_saved(saved_markdown)

    def _on_wysiwyg_save_with_content_delta(
        self,
        markdown: str,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        """Save a live snapshot while applying its small UTF-16 delta."""
        if (
            self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
            or not self._current_file
            or not self._active_path
        ):
            return
        self._on_wysiwyg_content_delta(
            markdown,
            start,
            delete_count,
            inserted,
            base_revision,
            final_length,
        )
        saved_markdown = self._editor.document().toPlainText()
        if self._save_tab_buffer(self._active_path) and self._wysiwyg_view is not None:
            mark_saved = getattr(self._wysiwyg_view, "mark_saved", None)
            if callable(mark_saved):
                mark_saved(saved_markdown)

    def _on_wysiwyg_content_delta(
        self,
        markdown: str,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int | None = None,
        final_length: int | None = None,
    ) -> None:
        """Apply Vditor's UTF-16 delta without copying/scanning the document."""
        if (
            self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
            or not self._active_path
        ):
            return
        document = self._editor.document()
        if document is self._editor._parking_document:
            return

        start = int(start)
        delete_count = int(delete_count)
        revision = (
            self._wysiwyg_shadow_revision
            if base_revision is None
            else int(base_revision)
        )
        if revision < self._wysiwyg_shadow_revision:
            # A QWebChannel push that was already in flight can arrive after a
            # newer transition snapshot has been accepted. It is stale, even
            # though the editor generation is unchanged, and must never roll
            # the durable draft back to its older full payload.
            return
        old_length = self._wysiwyg_shadow_qt_length
        document_length = max(0, document.characterCount() - 1)
        if (
            old_length is None
            or document_length != old_length
            or revision != self._wysiwyg_shadow_revision
            or start < 0
            or delete_count < 0
            or start + delete_count > old_length
        ):
            self._on_wysiwyg_content_changed(markdown)
            self._wysiwyg_shadow_revision = revision + 1
            return

        if (
            delete_count == 0
            and not inserted
            and markdown == self._wysiwyg_shadow_text
        ):
            self._wysiwyg_shadow_revision = revision + 1
            return

        undo_enabled = document.isUndoRedoEnabled()
        self._applying_wysiwyg_delta = True
        try:
            if undo_enabled:
                document.setUndoRedoEnabled(False)
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(
                start + delete_count, QTextCursor.MoveMode.KeepAnchor
            )
            cursor.insertText(inserted)
            if undo_enabled:
                document.setUndoRedoEnabled(True)
            self._wysiwyg_shadow_text = markdown
            self._wysiwyg_shadow_qt_length = (
                old_length
                - delete_count
                + py_to_qt_position(inserted, len(inserted))
            )
            if (
                final_length is not None
                and self._wysiwyg_shadow_qt_length != int(final_length)
            ):
                self._on_wysiwyg_content_changed(markdown)
            self._wysiwyg_shadow_revision = revision + 1
            document.setModified(True)
        finally:
            if undo_enabled and not document.isUndoRedoEnabled():
                document.setUndoRedoEnabled(True)
            self._applying_wysiwyg_delta = False

    def _replace_wysiwyg_shadow_text(self, markdown: str) -> None:
        """Patch only the changed range of the hidden QTextDocument.

        Qt cursor offsets are UTF-16 while Python indexes Unicode code points,
        so both ends pass through ``py_to_qt_position``. Undo is intentionally
        cleared: Vditor owns the visible undo history while this backend is
        active, and replaying shadow-sync entries after switching to source
        would be surprising.
        """
        document = self._editor.document()
        previous = document.toPlainText()
        if previous == markdown:
            self._wysiwyg_shadow_qt_length = max(
                0, document.characterCount() - 1
            )
            return

        prefix = 0
        shared = min(len(previous), len(markdown))
        while prefix < shared and previous[prefix] == markdown[prefix]:
            prefix += 1

        suffix = 0
        previous_remaining = len(previous) - prefix
        markdown_remaining = len(markdown) - prefix
        while (
            suffix < previous_remaining
            and suffix < markdown_remaining
            and previous[len(previous) - 1 - suffix]
            == markdown[len(markdown) - 1 - suffix]
        ):
            suffix += 1

        old_end = len(previous) - suffix
        new_end = len(markdown) - suffix
        undo_enabled = document.isUndoRedoEnabled()
        self._applying_wysiwyg_delta = True
        try:
            if undo_enabled:
                document.setUndoRedoEnabled(False)
            cursor = QTextCursor(document)
            cursor.setPosition(py_to_qt_position(previous, prefix))
            cursor.setPosition(
                py_to_qt_position(previous, old_end),
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.insertText(markdown[prefix:new_end])
            if undo_enabled:
                document.setUndoRedoEnabled(True)
        finally:
            if undo_enabled and not document.isUndoRedoEnabled():
                document.setUndoRedoEnabled(True)
            self._applying_wysiwyg_delta = False
        self._wysiwyg_shadow_qt_length = max(
            0, document.characterCount() - 1
        )
        document.setModified(True)

    def _on_wysiwyg_content_changed(self, markdown: str) -> None:
        """Apply a full Vditor value when incremental synchronization cannot."""
        if self._active_edit_backend != edit_backend.WYSIWYG_BACKEND:
            return
        document = self._editor.document()
        if document is self._editor._parking_document:
            return
        self._wysiwyg_shadow_text = markdown
        self._replace_wysiwyg_shadow_text(markdown)

    def _discard_tab_buffer(self, key: str, *, preserve_recovery: bool = False) -> None:
        state = self._tab_state.get(key)
        if state is not None:
            document = state.get("editor_document")
            if document is self._editor._parking_document:
                # The parking document is permanent EditorView infrastructure,
                # never a tab-owned buffer.  Older compatibility paths may
                # have stored it before this invariant was enforced.
                document = None
            if (
                isinstance(document, QTextDocument)
                and self._editor.document() is document
            ):
                self._editor.release_buffer_document()
            state["editor_document"] = None
            if isinstance(document, QTextDocument):
                # QPlainTextEdit keeps wrapper-side references to documents it
                # has displayed even after a swap. Queue explicit QObject
                # destruction once a tab has permanently released its buffer.
                try:
                    document.deleteLater()
                except RuntimeError:
                    # Qt owns and may already have destroyed its original
                    # built-in document when a compatibility caller toggles
                    # edit state without creating a per-tab buffer first.
                    pass
            state["view_mode"] = view_mode.PREVIEW
            state.pop("wysiwyg_parked", None)
            for field in (
                "cursor", "anchor", "editor_scroll", "editing_encoding",
                "editing_newline", "source_signature", "preview_scroll_ratio",
            ):
                state.pop(field, None)
        if not preserve_recovery:
            self._recovery_store.discard(key)

    def _dirty_tab_keys(self) -> list[str]:
        dirty: list[str] = []
        if self._edit_mode:
            if not self._stash_active_editor_state(snapshot=False):
                # Fail closed: a non-responsive WebEngine must never make a
                # possibly dirty tab look clean to the window-close path.
                document = self._editor.document()
                if document is not self._editor._parking_document:
                    document.setModified(True)
        for key, state in self._tab_state.items():
            document = state.get("editor_document")
            if isinstance(document, QTextDocument) and document.isModified():
                dirty.append(key)
        return dirty

    def _save_recovery_for_state(self, key: str, state: dict) -> None:
        document = state.get("editor_document")
        if not isinstance(document, QTextDocument) or not document.isModified():
            self._recovery_store.clear_after_save(key)
            return
        cursor = int(state.get("cursor", 0) or 0)
        anchor = int(state.get("anchor", cursor) or cursor)
        scroll = int(state.get("editor_scroll", 0) or 0)
        try:
            self._recovery_store.save(
                key,
                document.toPlainText(),
                encoding=str(state.get("editing_encoding") or "utf-8"),
                newline=str(state.get("editing_newline") or "\n"),
                cursor=max(0, cursor),
                anchor=max(0, anchor),
                scroll=max(0, scroll),
                source_signature=state.get("source_signature"),
            )
        except (OSError, TypeError, ValueError) as exc:
            self.statusBar().showMessage(f"無法建立復原草稿：{exc}", 5000)

    def _save_active_recovery_snapshot(self) -> None:
        if not self._edit_mode or not self._active_path:
            return
        self._stash_active_editor_state(
            snapshot=False, sync_wysiwyg=False
        )
        state = self._tab_state.get(self._active_path)
        if state is not None:
            self._save_recovery_for_state(self._active_path, state)

    def _prepare_recovery_state(self, path: Path, kind: str) -> None:
        key = str(path)
        if kind not in {"markdown", "text"} or key in self._recovery_checked_paths:
            return
        self._recovery_checked_paths.add(key)
        existing_state = self._tab_state.get(key) or {}
        if isinstance(existing_state.get("editor_document"), QTextDocument):
            # A live per-tab document is newer and already owns its undo stack;
            # never replace it with an older persisted recovery snapshot.
            return
        snapshot: RecoverySnapshot | None = self._recovery_store.load(path)
        if snapshot is None:
            return
        try:
            result = read_text_detailed(path)
        except OSError:
            result = None
        if result is None:
            disk_text, disk_encoding, disk_newline = "", "utf-8", "\n"
        else:
            disk_text, disk_encoding, disk_newline = result
        if snapshot.draft == disk_text:
            self._recovery_store.discard(path)
            return
        dialog = RecoveryDialog(
            path,
            disk_text,
            snapshot.draft,
            snapshot.updated_at,
            self,
        )
        dialog.exec()
        if dialog.choice == RecoveryDialog.DISCARD:
            self._recovery_store.discard(path)
            (self._tab_state.get(key) or {}).pop("pending_recovery", None)
            return
        if dialog.choice != RecoveryDialog.RESTORE:
            self._tab_state.setdefault(key, {})["pending_recovery"] = True
            return
        document = self._editor.create_buffer_document(snapshot.draft)
        document.setModified(True)
        state = self._tab_state.setdefault(key, {})
        state.update(
            {
                "kind": kind,
                "view_mode": view_mode.EDIT,
                "editor_document": document,
                "cursor": snapshot.cursor,
                "anchor": snapshot.anchor,
                "editor_scroll": snapshot.scroll,
                "editing_encoding": snapshot.encoding or disk_encoding,
                "editing_newline": snapshot.newline or disk_newline,
                "source_signature": snapshot.signature_pair,
                "preview_scroll_ratio": 0.0,
            }
        )
        state.pop("pending_recovery", None)

    def _confirm_tab_buffer(self, key: str) -> bool:
        state = self._tab_state.get(key)
        document = state.get("editor_document") if state else None
        if not isinstance(document, QTextDocument) or not document.isModified():
            return True
        answer = QMessageBox.question(
            self,
            "未儲存的變更",
            f"{Path(key).name} 有未儲存的變更，要儲存嗎？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_tab_buffer(key)
        if answer == QMessageBox.StandardButton.Discard:
            self._discard_tab_buffer(key)
            return True
        return False

    def _confirm_close_all_edits(self) -> bool:
        preview_will_be_discarded = self._preview_editing
        if not self._confirm_discard_preview_edit(commit=False):
            return False
        dirty = self._dirty_tab_keys()
        if not dirty:
            if preview_will_be_discarded:
                self._preview_editing = False
            return True
        if len(dirty) == 1:
            confirmed = self._confirm_tab_buffer(dirty[0])
            if confirmed and preview_will_be_discarded:
                self._preview_editing = False
            return confirmed
        names = "\n".join(f"• {Path(key).name}" for key in dirty)
        answer = QMessageBox.question(
            self,
            "多個分頁尚未儲存",
            f"以下 {len(dirty)} 份文件有未儲存的變更：\n{names}\n\n"
            "要全部儲存嗎？",
            QMessageBox.StandardButton.SaveAll
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.SaveAll:
            confirmed = all(self._save_tab_buffer(key) for key in dirty)
        elif answer == QMessageBox.StandardButton.Discard:
            for key in dirty:
                self._discard_tab_buffer(key)
            confirmed = True
        else:
            confirmed = False
        if confirmed and preview_will_be_discarded:
            self._preview_editing = False
        return confirmed

    def _enter_edit_mode(self, mode: str = view_mode.EDIT) -> bool:
        if not view_mode.is_editing(mode):
            mode = view_mode.EDIT
        plain_text = bool(self._current_file and is_text(self._current_file))
        if plain_text:
            mode = view_mode.EDIT  # .txt has no Markdown preview to split with
        state = self._active_editor_state()
        if (
            state is not None
            and view_mode.is_editing(str(state.get("view_mode", "")))
            and isinstance(state.get("editor_document"), QTextDocument)
        ):
            return self._activate_editor_state(state, mode)
        # Entering the editor hides the preview page and turns inline editing
        # off, which destroys any open inline editor along with it.
        if not self._confirm_discard_preview_edit():
            return False
        try:
            result = read_text_detailed(self._current_file)
        except OSError as exc:
            QMessageBox.warning(self, "無法編輯", f"無法讀取檔案：\n{exc}")
            return False
        if result is None:
            QMessageBox.warning(
                self,
                "無法編輯",
                "無法讀取檔案編碼，請使用 UTF-8、UTF-16、Big5 或 GBK。",
            )
            return False

        text, encoding, newline = result
        document = self._editor.create_buffer_document(text)
        state = state if state is not None else {}
        state.update(
            {
                "kind": self._current_kind,
                "view_mode": mode,
                "editor_document": document,
                "cursor": 0,
                "anchor": 0,
                "editor_scroll": 0,
                "editing_encoding": encoding,
                "editing_newline": newline,
                "source_signature": self._file_signature(self._current_file),
                "preview_scroll_ratio": 0.0,
            }
        )
        if self._active_path:
            self._tab_state[self._active_path] = state
        return self._activate_editor_state(state, mode)

    def _exit_edit_mode(self):
        self._request_live_wysiwyg_snapshot(
            self._exit_edit_mode_after_snapshot,
            purpose="離開編輯",
        )

    def _exit_edit_mode_after_snapshot(self):
        if not self._confirm_discard_edits(_snapshot_ready=True):
            return
        self._leave_edit_ui()

    def _toggle_edit_backend(self):
        """Toolbar button / Ctrl+Shift+W: split editor <-> WYSIWYG (Vditor)."""
        if not (self._edit_mode and self._current_kind == "markdown"):
            return
        if not self._active_path:
            return
        state = self._tab_state.get(self._active_path)
        if state is None or not isinstance(
            state.get("editor_document"), QTextDocument
        ):
            return
        target = edit_backend.backend_allows(
            edit_backend.toggle_backend(self._active_edit_backend),
            self._current_file.suffix if self._current_file else None,
            is_plain_text=False,  # guarded by the markdown check above
        )
        if target == self._active_edit_backend:
            return  # .txt/plain-text forced back to split: nothing changed
        if (
            self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
            and self._wysiwyg_view is not None
        ):
            def _switch_away_from_wysiwyg(state=state):
                state["edit_backend"] = target
                self._activate_editor_state(state, self._view_mode)

            self._request_live_wysiwyg_snapshot(
                _switch_away_from_wysiwyg,
                purpose="切換原始碼編輯",
            )
            return
        state["edit_backend"] = target
        self._activate_editor_state(state, self._view_mode)

    def _leave_edit_ui(self, *, persist_state: bool = True):
        if persist_state and self._active_path:
            self._discard_tab_buffer(self._active_path)
        # The editor widget is shared by every tab.  Even while hidden it
        # retains its QTextDocument, so park it whenever edit UI is left.
        # Otherwise closing that now-background tab can delete a document
        # which QPlainTextEdit still owns and crash inside Qt.
        self._editor.release_buffer_document()
        self._editor.set_markdown_services_suspended(False)
        self._view_mode = view_mode.PREVIEW
        self._renderer.set_inline_edit_enabled(True)
        self._renderer.set_preview_double_click_mode(self._preview_double_click)
        self._preview_timer.stop()
        self._editor_status_timer.stop()
        self._recovery_timer.stop()
        self._editor_search_bar.hide()
        self._format_toolbar.hide()
        self._editor_status.show()
        self._active_edit_backend = edit_backend.SPLIT_BACKEND
        self._set_search_escape_enabled(False)
        is_md = bool(self._current_file and is_markdown(self._current_file))
        self._search_btn.setEnabled(is_md)
        self._reload_btn.setEnabled(bool(self._current_file))
        self._export_btn.setEnabled(is_md)
        self._stack.setCurrentWidget(self._renderer)
        self._renderer.setFocus()
        self._update_format_menu_actions()
        self._refresh_icons()
        self._update_dirty_ui()

    def _confirm_discard_edits(self, *, _snapshot_ready: bool = False) -> bool:
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
            return (
                self._save_edits_after_snapshot()
                if _snapshot_ready
                else self._save_edits()
            )
        if answer == QMessageBox.StandardButton.Discard:
            if self._active_path:
                self._discard_tab_buffer(self._active_path)
            return True
        return False

    def _save_tab_buffer(self, key: str) -> bool:
        state = self._tab_state.get(key)
        if state is None:
            return False
        document = state.get("editor_document")
        if not isinstance(document, QTextDocument):
            return False
        path = Path(key)
        source_signature = state.get("source_signature")
        current_signature = self._file_signature(path)
        if (
            not document.isModified()
            and source_signature != current_signature
        ):
            # A clean editor has nothing to contribute.  In particular, do
            # not let Ctrl+S overwrite a newer disk version before the file
            # watcher has had time to reload it.  We intentionally continue
            # for an unchanged signature: programmatic editor operations can
            # replace text while Qt resets the modified flag to False.
            if key == self._active_path:
                self.statusBar().showMessage(
                    "磁碟版本已變更；本機沒有標記為未儲存的內容，因此未覆寫。",
                    5000,
                )
            return True
        if (
            source_signature != current_signature
            and not (
                state.get("source_deleted") and current_signature is None
            )
        ):
            if source_signature is None:
                conflict_detail = (
                    "編輯開始時這個路徑沒有檔案，但現在磁碟上已有同名檔案。"
                )
            elif current_signature is None:
                conflict_detail = "檔案已從磁碟刪除。"
            else:
                conflict_detail = "檔案已在磁碟上被其他程式修改。"
            answer = QMessageBox.question(
                self,
                "檔案已在外部變更",
                f"{path.name}：{conflict_detail}\n"
                "仍要用目前草稿覆寫磁碟版本嗎？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False

        text = document.toPlainText()
        newline = str(state.get("editing_newline") or "\n")
        if newline != "\n":
            text = text.replace("\n", newline)
        original_encoding = str(state.get("editing_encoding") or "utf-8")
        encoding = original_encoding
        try:
            data = text.encode(encoding)
        except UnicodeEncodeError:
            encoding = "utf-8"
            data = text.encode(encoding)
        try:
            atomic_write_bytes(path, data)
        except OSError as exc:
            QMessageBox.warning(self, "儲存失敗", f"無法寫入檔案：\n{exc}")
            return False

        document.setModified(False)
        signature = self._file_signature(path)
        state["editing_encoding"] = encoding
        state["source_signature"] = signature
        state.pop("source_deleted", None)
        self._recovery_store.clear_after_save(path)
        if key == self._active_path:
            self._editing_encoding = encoding
            self._loaded_signature = signature
            self._rearm_watch()
            self._update_editor_status_document()
            if encoding != original_encoding:
                self.statusBar().showMessage(
                    "內容含原編碼無法表示的字元，已改用 UTF-8 儲存", 6000
                )
            else:
                self.statusBar().showMessage("已儲存", 3000)
            if is_markdown(path):
                self._reload_preview()
                self._refresh_link_index(force=True)
                self._update_front_tags()
        else:
            self._refresh_link_index(force=True)
        self._update_dirty_ui()
        return True

    def _save_edits(self) -> bool:
        if not (self._edit_mode and self._current_file):
            return False
        result: list[bool] = []

        def _continue() -> None:
            result.append(self._save_edits_after_snapshot())

        started = self._request_live_wysiwyg_snapshot(
            _continue, purpose="儲存"
        )
        return result[0] if result else started

    def _save_edits_after_snapshot(self) -> bool:
        if not self._stash_active_editor_state(snapshot=False):
            return False
        markdown = self._editor.document().toPlainText()
        saved = self._save_tab_buffer(str(self._current_file))
        if saved and self._wysiwyg_view is not None:
            mark_saved = getattr(self._wysiwyg_view, "mark_saved", None)
            if callable(mark_saved):
                mark_saved(markdown)
        return saved

    def _on_editor_modified(self, _modified: bool):
        self._update_dirty_ui()
        if self._editor.is_modified():
            self._recovery_timer.start()

    def _on_editor_text_changed(self):
        # Debounced live re-render; only the split mode shows the preview,
        # and the split-mode preview pane is not on screen at all while the
        # WYSIWYG backend owns the stack (Vditor already shows a live
        # WYSIWYG render, so a second hidden re-render would be wasted work).
        if (
            self._view_mode == view_mode.SPLIT
            and self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
        ):
            self._preview_timer.start()
        if self._edit_mode:
            if (
                self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
                and not self._applying_wysiwyg_delta
            ):
                # No host command should edit the hidden QPlainTextEdit while
                # Office Viewer is visible. If one nevertheless does, discard
                # the delta cache so the next visible-editor message performs
                # a full authoritative resync instead of accepting a same-size
                # no-op against diverged hidden text.
                self._wysiwyg_shadow_text = None
                self._wysiwyg_shadow_qt_length = None
            if (
                not self._applying_wysiwyg_delta
                and (
                    self._active_edit_backend != edit_backend.WYSIWYG_BACKEND
                    or self._editor.toPlainText() != self._wysiwyg_shadow_text
                )
            ):
                self._editor_status_timer.start()
            if self._editor.is_modified():
                self._recovery_timer.start()

    def _on_editor_cursor_changed(self):
        if not self._edit_mode:
            return
        cursor = self._editor.textCursor()
        column = qt_to_py_position(
            cursor.block().text(), cursor.positionInBlock()
        ) + 1
        self._editor_status.set_cursor_position(
            line=cursor.blockNumber() + 1,
            column=column,
        )

    def _update_editor_status_document(self) -> None:
        if not self._edit_mode or self._current_kind not in {"markdown", "text"}:
            return
        cursor = self._editor.textCursor()
        column = qt_to_py_position(
            cursor.block().text(), cursor.positionInBlock()
        ) + 1
        self._editor_status.set_document_state(
            line=cursor.blockNumber() + 1,
            column=column,
            text=self._editor.toPlainText(),
            document_kind=self._current_kind,
            encoding=self._editing_encoding,
            newline=self._editing_newline,
        )

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
        dirty = (
            self._edit_mode and self._editor.is_modified()
        ) or self._active_tab_parked_dirty()
        marker = "● " if dirty else ""
        self.setWindowTitle(f"{marker}{name} - Markdown Viewer")
        self._toolbar_title.setText(f"{marker}{name}")
        self._toolbar_subtitle.setText(
            "未儲存變更" if dirty else str(self._current_file.parent)
        )
        self._refresh_tab_labels()

    def _open_file(self, filepath: str):
        path = Path(filepath)
        kind = document_kind(path)
        if not kind:
            QMessageBox.warning(
                self,
                "不支援的檔案",
                "目前支援 Markdown（.md, .markdown）、純文字（.txt）與 PDF（.pdf）檔案。",
            )
            return
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
    def _refresh_tab_labels(self):
        """Rebuild concise labels, disambiguating duplicate filenames only."""
        paths = [
            str(self._tab_bar.tabData(index) or "")
            for index in range(self._tab_bar.count())
        ]
        labels = disambiguated_tab_labels(paths)
        dirty_paths = {
            path
            for path, state in self._tab_state.items()
            if isinstance(state.get("editor_document"), QTextDocument)
            and state["editor_document"].isModified()
        }
        # Keep the compatibility surface correct even when callers toggle
        # ``_edit_mode`` directly before a tab buffer has been stashed.
        if self._edit_mode and self._active_path and self._editor.is_modified():
            dirty_paths.add(self._active_path)
        for index, (path, label) in enumerate(zip(paths, labels)):
            marker = "● " if path and path in dirty_paths else ""
            deleted = bool(
                path and (self._tab_state.get(path) or {}).get("source_deleted")
            )
            suffix = "（原檔已刪除）" if deleted else ""
            self._tab_bar.setTabText(index, f"{marker}{label}{suffix}")
        self._tab_bar.refresh_close_buttons()

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
        self._tab_state[key] = {
            "kind": kind,
            "scroll": None,
            "view_mode": view_mode.PREVIEW,
            "editor_document": None,
        }
        self._refresh_tab_labels()
        return idx

    def _on_tab_changed(self, idx: int):
        if self._tab_guard or idx < 0:
            return
        key = self._tab_bar.tabData(idx)
        if not key or key == self._active_path:
            return
        self._activate_tab(idx)

    def _on_tab_close(self, idx: int, *, _snapshot_ready: bool = False) -> bool:
        if idx < 0 or idx >= self._tab_bar.count():
            return False
        key = self._tab_bar.tabData(idx)
        closing_active = key == self._active_path
        if (
            closing_active
            and not _snapshot_ready
            and self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            result: list[bool] = []

            def _continue(path=str(key)) -> None:
                index = self._index_of_path(path)
                result.append(
                    self._on_tab_close(index, _snapshot_ready=True)
                    if index >= 0
                    else False
                )

            self._request_live_wysiwyg_snapshot(
                _continue, purpose="關閉分頁"
            )
            return result[0] if result else False
        state = self._tab_state.get(key) or {}
        preserve_recovery = False
        if state.get("pending_recovery"):
            answer = QMessageBox.question(
                self,
                "尚未處理的復原草稿",
                f"{Path(key).name} 的復原草稿尚未還原。\n"
                "選「是」會保留草稿供下次開啟；選「否」會永久捨棄草稿。",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            preserve_recovery = answer == QMessageBox.StandardButton.Yes
            if preserve_recovery:
                self._recovery_checked_paths.discard(key)
            else:
                self._recovery_store.discard(key)
        if closing_active and self._edit_mode:
            if not self._stash_active_editor_state(snapshot=False):
                return False
        if not self._confirm_tab_buffer(key):
            return False
        if closing_active and not self._confirm_discard_preview_edit():
            return False
        if closing_active and self._edit_mode:
            self._leave_edit_ui(persist_state=False)
        # Confirmation above guarantees the buffer is either saved or
        # explicitly discarded. Detach it before removing the final tab-state
        # reference so parentless QTextDocuments can be released immediately.
        self._discard_tab_buffer(key, preserve_recovery=preserve_recovery)
        if closing_active:
            self._active_path = None  # don't save state for a closing document
        self._tab_guard = True
        self._tab_bar.removeTab(idx)
        self._tab_guard = False
        self._tab_state.pop(key, None)
        self._refresh_tab_labels()
        if self._tab_bar.count() == 0:
            self._show_empty_state()
        elif closing_active:
            self._activate_tab(self._tab_bar.currentIndex())
        return True

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
        if idx < 0 or idx >= self._tab_bar.count():
            return menu
        key = str(self._tab_bar.tabData(idx) or "")

        close_action = menu.addAction("關閉分頁")
        close_action.triggered.connect(
            lambda _checked=False, path=key: self._close_tab_by_path(path)
        )

        close_others_action = menu.addAction("關閉其他分頁")
        close_others_action.setEnabled(self._tab_bar.count() > 1)
        close_others_action.triggered.connect(
            lambda _checked=False, path=key: self._close_other_tabs(path)
        )

        close_right_action = menu.addAction("關閉右側分頁")
        close_right_action.setEnabled(idx < self._tab_bar.count() - 1)
        close_right_action.triggered.connect(
            lambda _checked=False, path=key: self._close_tabs_to_right(path)
        )

        menu.addSeparator()
        detach_action = menu.addAction("移至新視窗")
        detach_action.setEnabled(self._can_detach_tab(idx))
        detach_action.triggered.connect(
            lambda _checked=False, path=key: self._detach_tab_by_path(path)
        )
        return menu

    def _close_tab_by_path(
        self, key: str, *, _snapshot_ready: bool = False
    ) -> bool:
        index = self._index_of_path(key)
        return (
            self._on_tab_close(index, _snapshot_ready=_snapshot_ready)
            if index >= 0
            else False
        )

    def _close_tab_paths(self, paths, *, _snapshot_ready: bool = False) -> None:
        pending = list(dict.fromkeys(str(path) for path in paths if path))
        if (
            not _snapshot_ready
            and self._active_path in pending
            and self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda items=pending: self._close_tab_paths(
                    items, _snapshot_ready=True
                ),
                purpose="關閉分頁",
            )
            return
        if self._active_path in pending:
            pending.remove(self._active_path)
            if not self._close_tab_by_path(
                self._active_path, _snapshot_ready=_snapshot_ready
            ):
                return
        for key in pending:
            self._close_tab_by_path(key)

    def _close_other_tabs(self, keep_key: str) -> None:
        if self._index_of_path(keep_key) < 0:
            return
        self._close_tab_paths(
            self._tab_bar.tabData(index)
            for index in range(self._tab_bar.count())
            if self._tab_bar.tabData(index) != keep_key
        )

    def _close_tabs_to_right(self, key: str) -> None:
        index = self._index_of_path(key)
        if index < 0:
            return
        self._close_tab_paths(
            self._tab_bar.tabData(tab_index)
            for tab_index in range(index + 1, self._tab_bar.count())
        )

    def _can_detach_tab(self, idx: int) -> bool:
        return (
            self._tab_bar.count() > 1
            and 0 <= idx < self._tab_bar.count()
            and bool(self._tab_bar.tabData(idx))
        )

    def _detach_tab_by_path(self, key: str) -> None:
        index = self._index_of_path(key)
        if index >= 0:
            self._detach_tab(index)

    def _detach_tab(self, idx: int, *, _snapshot_ready: bool = False):
        if not self._can_detach_tab(idx):
            return
        key = self._tab_bar.tabData(idx)
        path = Path(key)
        if (
            key == self._active_path
            and not _snapshot_ready
            and self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda tab_path=str(key): self._detach_tab_after_wysiwyg_snapshot(
                    tab_path
                ),
                purpose="移至新視窗",
            )
            return
        if key == self._active_path and self._edit_mode:
            if not self._stash_active_editor_state(snapshot=False):
                return
        if not self._confirm_tab_buffer(key):
            return
        if key == self._active_path:
            if self._edit_mode:
                self._leave_edit_ui(persist_state=False)
            self._save_active_view_state()
            # The detached window restores shared zoom from QSettings during
            # construction, so commit the active PDF's last wheel frame first.
            self._flush_pdf_zoom_pipeline()
        state = dict(self._tab_state.get(key) or {})
        state["editor_document"] = None
        state["view_mode"] = view_mode.PREVIEW
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

    def _detach_tab_after_wysiwyg_snapshot(self, key: str) -> None:
        index = self._index_of_path(key)
        if index >= 0:
            self._detach_tab(index, _snapshot_ready=True)

    def _next_tab(self):
        self._step_tab(1)

    def _prev_tab(self):
        self._step_tab(-1)

    def _step_tab(self, delta: int):
        n = self._tab_bar.count()
        if n <= 1:
            return
        self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() + delta) % n)

    def _activate_tab(self, idx: int, *, _snapshot_ready: bool = False):
        """Load the document for tab *idx* into the shared viewer."""
        if idx < 0 or idx >= self._tab_bar.count():
            return
        key = self._tab_bar.tabData(idx)
        if not key or key == self._active_path:
            return
        if (
            not _snapshot_ready
            and self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            current = self._index_of_path(self._active_path)
            if current >= 0:
                self._tab_guard = True
                self._tab_bar.setCurrentIndex(current)
                self._tab_guard = False
            self._request_live_wysiwyg_snapshot(
                lambda path=str(key): self._activate_tab_after_wysiwyg_snapshot(
                    path
                ),
                purpose="切換分頁",
            )
            return
        if self._edit_mode:
            if not self._stash_active_editor_state():
                current = self._index_of_path(self._active_path)
                if current >= 0:
                    self._tab_guard = True
                    self._tab_bar.setCurrentIndex(current)
                    self._tab_guard = False
                return
            self._leave_edit_ui(persist_state=False)
        else:
            self._save_active_view_state()
        self._active_path = key
        state = self._tab_state.get(key) or {}
        kind = state.get("kind") or document_kind(Path(key))
        self._load_document(Path(key), kind)

    def _activate_tab_after_wysiwyg_snapshot(self, key: str) -> None:
        index = self._index_of_path(key)
        if index < 0:
            return
        self._tab_guard = True
        self._tab_bar.setCurrentIndex(index)
        self._tab_guard = False
        self._activate_tab(index, _snapshot_ready=True)

    def _save_active_view_state(self):
        session_state.save_active_view_state(self)

    def _show_empty_state(self):
        self._flush_pdf_zoom_pipeline()
        self._editor.release_buffer_document()
        self._editor.set_document_path(None)
        self._editor.set_plain_text_mode(False)
        self._current_file = None
        self._current_kind = ""
        self._current_front_tags = []
        self._current_body_tags = []
        self._active_path = None
        self.setWindowTitle("Markdown Viewer")
        self._toolbar_title.setText("Markdown Viewer")
        self._toolbar_subtitle.setText("尚未載入文件")
        self._close_search()
        self._preview_editing = False
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

    def _refresh_stale_clean_editor_state(self, path: Path, state: dict) -> None:
        """Refresh an inactive clean buffer whose source changed on disk."""

        document = state.get("editor_document")
        if not isinstance(document, QTextDocument) or document.isModified():
            return
        current_signature = self._file_signature(path)
        if state.get("source_signature") == current_signature:
            return
        if current_signature is None:
            # The clean buffer is now the only remaining copy. Promote it to a
            # draft rather than dropping it merely because deletion happened
            # while another tab was active.
            document.setModified(True)
            state["source_deleted"] = True
            self._save_recovery_for_state(str(path), state)
            self.statusBar().showMessage(
                "原始檔在背景中被刪除；分頁內容已保留為未儲存草稿。",
                8000,
            )
            return
        try:
            result = read_text_detailed(path)
        except OSError:
            result = None
        if result is None:
            # Preserve the readable in-memory version and force the normal
            # conflict prompt on a future Save.
            document.setModified(True)
            self._save_recovery_for_state(str(path), state)
            return
        text, encoding, newline = result
        replacement = self._editor.create_buffer_document(text)
        state.update(
            {
                "editor_document": replacement,
                "cursor": 0,
                "anchor": 0,
                "editor_scroll": 0,
                "editing_encoding": encoding,
                "editing_newline": newline,
                "source_signature": current_signature,
            }
        )
        state.pop("source_deleted", None)
        try:
            document.deleteLater()
        except RuntimeError:
            pass

    def _load_document(self, path: Path, kind: str):
        """Load *path* into the shared viewer, restoring its saved view state."""
        self._flush_pdf_zoom_pipeline()
        self._current_file = path
        self._current_kind = kind
        self._prepare_recovery_state(path, kind)
        tab_state = self._tab_state.get(str(path)) or {}
        if kind in {"markdown", "text"}:
            self._refresh_stale_clean_editor_state(path, tab_state)
        restore_editor = (
            kind in {"markdown", "text"}
            and view_mode.is_editing(str(tab_state.get("view_mode", "")))
            and isinstance(tab_state.get("editor_document"), QTextDocument)
            # A tab Esc'd out of WYSIWYG keeps its dirty buffer and its
            # tab_state["view_mode"] at EDIT/SPLIT (so Ctrl+E/double-click
            # can resume it), but the user explicitly asked to see PREVIEW
            # last -- a tab round-trip must not silently re-open the editor.
            and not tab_state.get("wysiwyg_parked")
        )
        self.setWindowTitle(f"{path.name} - Markdown Viewer")
        self._toolbar_title.setText(path.name)
        self._toolbar_subtitle.setText(str(path.parent))
        self._close_search()
        self._current_front_tags = []
        self._current_body_tags = []
        if kind == "markdown":
            self._doc_annotations = (
                AnnotationStore.load(path) if path.exists()
                else DocumentAnnotations()
            )
            self._sync_renderer_annotations()
            self._panel.annotations.set_document(self._doc_annotations)
            self._set_pdf_panel_document(None)
            self._panel.show_pdf_notes(False)
            self._panel.set_annotations_enabled(True)
            if path.exists():
                self._update_front_tags()
            scroll = (self._tab_state.get(str(path)) or {}).get("scroll")
            # A fresh page boots with no inline editor, whatever the last one
            # was doing when it was replaced.
            self._preview_editing = False
            if path.exists():
                self._renderer.load_file(path, scroll_y=scroll)
            else:
                self._renderer.show_empty()
            self._stack.setCurrentWidget(self._renderer)
        elif kind == "text":
            self._doc_annotations = DocumentAnnotations()
            self._renderer.set_annotations([])
            self._panel.annotations.set_document(None)
            self._set_pdf_panel_document(None)
            self._panel.show_pdf_notes(False)
            self._panel.set_annotations_enabled(False)
            self._panel.toc.update_outline([])
            self._preview_editing = False
            # Plain text has no preview: the editor IS the document view.
            if not restore_editor and not self._enter_edit_mode(view_mode.EDIT):
                self._renderer.show_empty()
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
        if restore_editor:
            self._activate_editor_state(
                tab_state,
                str(tab_state.get("view_mode") or view_mode.EDIT),
            )
        self._refresh_icons()

    def _open_pdf(self, path: Path):
        # Switch first so the password prompt (if any) appears over the PDF view.
        self._stack.setCurrentWidget(self._pdf_view)
        # Drop the previous file's bookmarks immediately. PdfView starts the
        # current outline in the background only after visible content paints.
        self._panel.toc.update_outline([])
        if not self._pdf_view.load(path):
            if self._pdf_view.is_locked():
                self.statusBar().showMessage(
                    "已取消開啟受密碼保護的 PDF；重新開啟可再次輸入密碼。", 6000
                )
            else:
                self.statusBar().showMessage(
                    "無法開啟此 PDF：檔案可能已損毀或無法讀取。", 6000
                )
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

    def _new_note(self):
        """Ctrl+N: create an empty Markdown / plain-text note and edit it."""
        browser = self._panel.file_browser
        folder = browser.selected_directory()
        if folder is None:
            roots = browser.library_roots() or []
            folder = roots[0] if roots else None
        if folder is None:
            picked = QFileDialog.getExistingDirectory(
                self, "選擇新筆記的資料夾"
            )
            if not picked:
                return
            folder = Path(picked)
        dialog = NewNoteDialog(folder, self._theme, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path = dialog.created_path()
        if path is None:
            return
        browser.reveal_created_note(path)
        self._on_browser_note_created(str(path))

    # --- file tree CRUD follow-ups (called by the file browser) ---
    def _on_browser_note_created(self, path: str):
        """A note was created in the file tree: open it for editing.

        A fresh Markdown note starts in split view (editor + live preview);
        plain text has no preview so it opens in the plain editor. This runs
        once at creation only -- switching modes or tabs afterwards follows
        the normal rules.
        """
        self._open_file(path)
        if (
            self._current_file
            and str(self._current_file) == str(Path(path))
            and self._current_kind in ("markdown", "text")
            and not self._edit_mode
        ):
            mode = (
                view_mode.SPLIT
                if self._current_kind == "markdown"
                else view_mode.EDIT
            )
            self._enter_edit_mode(mode)
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
            recent = [
                Path(value) for value in self._settings_json_list(
                    _RECENT_TEMPLATES_KEY
                )
            ]
            recent = [path for path in recent if path in templates][:5]
            ordered = recent + [path for path in templates if path not in recent]
            labels = []
            for path in ordered:
                relative = path.relative_to(folder).as_posix()
                labels.append(
                    f"最近｜{relative}" if path in recent else relative
                )
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
            template_path = ordered[labels.index(choice)]

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
        editor_text = self._editor.toPlainText()
        start = qt_to_py_position(editor_text, cursor.selectionStart())
        end = qt_to_py_position(editor_text, cursor.selectionEnd())
        rendered = prepare_template_insertion(
            rendered, editor_text[:start], editor_text[end:]
        )
        cursor.insertText(rendered)
        self._editor.setTextCursor(cursor)
        self._remember_setting_value(
            _RECENT_TEMPLATES_KEY, str(Path(template_path)), 5
        )
        self.statusBar().showMessage(f"已插入範本：{Path(template_path).name}", 3000)

    def _on_browser_paths_migrated(
        self, mapping: dict, *, _snapshot_ready: bool = False
    ):
        """Files were renamed/moved on disk: re-point tabs, recents, state."""
        if not mapping:
            return
        if (
            not _snapshot_ready
            and self._active_path in mapping
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            stable_mapping = dict(mapping)
            self._request_live_wysiwyg_snapshot(
                lambda: self._on_browser_paths_migrated(
                    stable_mapping, _snapshot_ready=True
                ),
                purpose="重新定位文件",
            )
            return
        for i in range(self._tab_bar.count()):
            key = self._tab_bar.tabData(i)
            new = mapping.get(key)
            if not new:
                continue
            self._tab_guard = True
            self._tab_bar.setTabData(i, new)
            self._tab_bar.setTabToolTip(i, new)
            self._tab_guard = False
            if key in self._tab_state:
                state = self._tab_state.pop(key)
                self._tab_state[new] = state
                self._recovery_store.discard(key)
                document = state.get("editor_document")
                if isinstance(document, QTextDocument) and document.isModified():
                    self._save_recovery_for_state(new, state)
            if key in self._recovery_checked_paths:
                self._recovery_checked_paths.discard(key)
                self._recovery_checked_paths.add(new)
        if self._active_path in mapping:
            self._active_path = mapping[self._active_path]
        if self._current_file and str(self._current_file) in mapping:
            self._current_file = Path(mapping[str(self._current_file)])
            self.setWindowTitle(f"{self._current_file.name} - Markdown Viewer")
            self._toolbar_title.setText(self._current_file.name)
            self._toolbar_subtitle.setText(str(self._current_file.parent))
            self._editor.set_document_path(self._current_file)
            if self._wysiwyg_view is not None:
                set_document_path = getattr(
                    self._wysiwyg_view, "set_document_path", None
                )
                if callable(set_document_path):
                    set_document_path(self._current_file)
            self._watch_current_file()
        self._refresh_tab_labels()
        self._panel.recent.migrate_paths(mapping)
        self._refresh_tags_panel()
        self._refresh_link_index(force=True)

    def _on_browser_paths_deleted(
        self, paths: list, *, _snapshot_ready: bool = False
    ):
        """Close clean deleted files while preserving every dirty draft."""
        stable_paths = list(paths)
        deleted_keys = {str(path) for path in stable_paths}
        if (
            not _snapshot_ready
            and self._active_path in deleted_keys
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda: self._on_browser_paths_deleted(
                    stable_paths, _snapshot_ready=True
                ),
                purpose="處理已刪除文件",
            )
            return
        for path in stable_paths:
            key = str(path)
            idx = self._index_of_path(key)
            if idx >= 0:
                if key == self._active_path and self._edit_mode:
                    self._stash_active_editor_state(snapshot=False)
                state = self._tab_state.get(key) or {}
                document = state.get("editor_document")
                if isinstance(document, QTextDocument) and document.isModified():
                    # Deletion has already happened in the file browser. Keep
                    # the buffer and its snapshot so Save can recreate the file
                    # instead of silently throwing the user's newer text away.
                    state["source_deleted"] = True
                    self._save_recovery_for_state(key, state)
                    if key == self._active_path:
                        self.statusBar().showMessage(
                            "原始檔已刪除；未儲存草稿仍保留在此分頁，儲存可重新建立檔案。",
                            8000,
                        )
                    self._refresh_tab_labels()
                    continue
                self._recovery_store.discard(key)
                self._on_tab_close(
                    idx,
                    _snapshot_ready=_snapshot_ready and key == self._active_path,
                )
        self._panel.recent.remove_paths(stable_paths)
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
        self._pdf_zoom_sync_timer.stop()
        self._pending_pdf_wheel_zoom = None
        session_state.apply_zoom(self, factor)

    def _zoom_in(self):
        self._apply_zoom(self._content_zoom + 0.1)

    def _zoom_out(self):
        self._apply_zoom(self._content_zoom - 0.1)

    def _zoom_reset(self):
        self._apply_zoom(1.0)

    def restore_last_session(self):
        session_state.restore_last_session(self)

    def _reload_preview(self):
        """Re-render the Markdown preview, dropping any inline editor with it.

        Every reload replaces the page, so the flag that says "the preview is
        holding unsaved text" has to fall with it; leaving it set would make
        the app prompt about an editor that no longer exists.
        """
        self._preview_editing = False
        self._renderer.reload_current()

    def _reload_current(self):
        if not self._current_file:
            return
        if not self._confirm_discard_preview_edit():
            return
        if self._current_kind == "pdf":
            self._flush_pdf_zoom_pipeline()
            page = self._pdf_view.current_page()
            self._panel.toc.update_outline([])
            if not self._pdf_view.load(self._current_file):
                self.statusBar().showMessage("已取消或無法重新載入此 PDF。", 4000)
                return
            self._pdf_view.set_highlights(self._pdf_highlights)
            self._pdf_view.restore_page(page)
        elif self._current_kind == "text":
            # Reload = re-read the file into the (always-on) editor.
            if not self._confirm_discard_edits():
                return
            if self._active_path:
                self._discard_tab_buffer(self._active_path)
            if not self._enter_edit_mode(view_mode.EDIT):
                return
        else:
            self._reload_preview()
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

    def _on_file_changed(self, path: str, *, _snapshot_ready: bool = False):
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
        if (
            not _snapshot_ready
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda: self._on_file_changed(path, _snapshot_ready=True),
                purpose="處理檔案的外部變更",
            )
            return
        self._loaded_signature = current
        self._prompt_external_change()

    def _on_preview_editing_changed(self, editing: bool):
        self._preview_editing = bool(editing)

    def _on_preview_unhandled_escape(self, generation: int):
        if generation != self._active_search_escape_generation:
            return
        if (
            generation > 0
            and (
                not self._search_bar.isHidden()
                or not self._editor_search_bar.isHidden()
            )
        ):
            self._close_search()

    def _confirm_discard_preview_edit(self, *, commit: bool = True) -> bool:
        """Ask before an action that would tear the preview's editor down.

        The inline editor lives entirely inside the rendered page, so any
        re-render throws away whatever is in it with no undo anywhere. The
        text editor gets exactly this courtesy through
        ``_confirm_discard_edits``; the preview used to get none.
        """
        if not self._preview_editing:
            return True
        answer = QMessageBox.question(
            self,
            "預覽中有未儲存的編輯",
            "預覽中有尚未儲存的編輯，重新載入會捨棄它。\n要捨棄嗎？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        # Whatever the caller does next replaces the page, and with it the
        # editor that set this flag.
        if commit:
            self._preview_editing = False
        return True

    def _reload_editor_from_disk(self) -> bool:
        """Replace the active editor buffer only after disk text is readable."""

        if not self._current_file or not self._active_path:
            return False
        try:
            result = read_text_detailed(self._current_file)
        except OSError as exc:
            QMessageBox.warning(
                self, "重新載入失敗", f"無法讀取磁碟版本：\n{exc}"
            )
            return False
        if result is None:
            QMessageBox.warning(
                self,
                "重新載入失敗",
                "無法辨識磁碟版本的文字編碼。",
            )
            return False
        text, encoding, newline = result
        mode = self._view_mode if self._edit_mode else view_mode.EDIT
        if self._current_kind == "text":
            mode = view_mode.EDIT
        document = self._editor.create_buffer_document(text)
        previous_state = self._tab_state.get(self._active_path) or {}
        previous_document = previous_state.get("editor_document")
        state = {
            "kind": self._current_kind,
            "scroll": None,
            "view_mode": mode,
            "editor_document": document,
            "cursor": 0,
            "anchor": 0,
            "editor_scroll": 0,
            "editing_encoding": encoding,
            "editing_newline": newline,
            "source_signature": self._file_signature(self._current_file),
            "preview_scroll_ratio": 0.0,
            # Carry the tab's backend choice across an external-change reload
            # (a fresh state dict would otherwise silently fall back to the
            # global default). _activate_editor_state pushes the reloaded
            # text into Vditor itself when this is WYSIWYG_BACKEND.
            "edit_backend": previous_state.get("edit_backend", self._edit_backend),
        }
        self._tab_state[self._active_path] = state
        if not self._activate_editor_state(state, mode):
            return False
        if (
            isinstance(previous_document, QTextDocument)
            and previous_document is not document
        ):
            try:
                previous_document.deleteLater()
            except RuntimeError:
                pass
        self._recovery_store.discard(self._active_path)
        return True

    def _prompt_external_change(self):
        if self._reload_prompt_open or not self._current_file:
            return
        self._reload_prompt_open = True
        try:
            name = self._current_file.name
            if self._preview_editing:
                # Same shape as the editor branch below, and for the same
                # reason: a reload here silently eats an edit the user is
                # still typing, and the preview editor has no undo at all.
                answer = QMessageBox.question(
                    self,
                    "檔案已在外部變更",
                    f"{name} 已被其他程式修改，但預覽中有尚未儲存的編輯。\n"
                    "要捨棄預覽中的編輯並載入磁碟上的新版本嗎？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._reload_preview()
            elif self._edit_mode and self._editor.is_modified():
                answer = QMessageBox.question(
                    self,
                    "檔案已在外部變更",
                    f"{name} 已被其他程式修改，但你有未儲存的編輯。\n"
                    "要捨棄你的編輯並載入磁碟上的新版本嗎？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._reload_editor_from_disk()
            else:
                answer = QMessageBox.question(
                    self,
                    "檔案已在外部變更",
                    f"{name} 已被其他程式修改，要重新載入嗎？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    if self._edit_mode and self._current_kind in {
                        "markdown", "text"
                    }:
                        self._reload_editor_from_disk()
                    else:
                        self._reload_preview()
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
            theme=self._theme,
            on_delete_tag=self._delete_tag,
            on_rename_tag=self._rename_tag,
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
        # Tags never change the folder structure, so update only the affected
        # file rows' pills incrementally instead of a full disk rescan (which
        # scaled with library size and caused the tag-edit stutter).
        self._panel.file_browser.update_file_tags(paths)

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

    def _rename_tag(self, old: str, new: str | None = None) -> None:
        """Rename a document-level tag everywhere it is assigned.

        Prompts for *new* when not given. Rewrites every affected file's
        doc-level tags (old -> new, deduped, order preserved), migrates the
        explicit color registration from *old* to *new*, then re-indexes and
        refreshes the tag views via ``_on_doc_tags_changed`` (which already
        refreshes the panel + file views, so we do not refresh again here).

        Merge semantics: if *new* already exists, *old*'s files simply gain
        *new* (deduped) -- i.e. *old* is folded into *new*, which is acceptable.

        Note (same caveat as ``_delete_tag``): only document-level tags are
        touched. Content-derived tags (MD front-matter, body #hashtags,
        annotations) are not rewritten, so such a tag may reappear on re-index.
        """
        old = (old or "").strip()
        if not old:
            return
        if new is None:
            new, ok = QInputDialog.getText(
                self, "重新命名標籤", "新標籤名稱：", text=old
            )
            if not ok:
                return
        new = (new or "").strip()
        if not new or new == old:
            return

        affected = [Path(p) for p in self._tag_index.files_with_tag(old)]
        for path in affected:
            renamed: list[str] = []
            for t in doc_tags_facade.read_doc_tags(path):
                repl = new if t == old else t
                if repl not in renamed:  # dedupe (folds old into an existing new)
                    renamed.append(repl)
            doc_tags_facade.write_doc_tags(path, renamed)

        # Migrate the explicit color old -> new (keep new's own color if it
        # already has one), then drop old's registration. Done before the
        # refresh below so the single _on_doc_tags_changed pass reflects it.
        col = self._tag_color_store.explicit_color(old)
        if col and not self._tag_color_store.explicit_color(new):
            self._tag_color_store.set_color(new, col)
        self._tag_color_store.remove(old)

        # Re-index touched files + refresh the tag panel and file views. This
        # runs even when *affected* is empty (old was known-but-unassigned), so
        # a rename of a colored-but-unused tag is still reflected in the panel.
        self._on_doc_tags_changed(affected)

    def _add_tag_to_paths(self, paths):
        """Quick-assign one tag to files from the 檔案 tab's "加入標籤…".

        Filters to supported documents, then shows a small editable combo that
        lists every known tag (indexed + colored-but-unassigned) yet still lets
        the user type a brand-new tag. A new tag auto-gets a deterministic color
        via the color store. The write itself reuses ``_assign_tag_to_paths`` so
        the tag index and every view refresh exactly as elsewhere.
        """
        paths = [Path(p) for p in paths if is_supported_document(Path(p))]
        if not paths:
            return
        items = sorted(
            set(self._tag_index.all_tags()) | set(self._tag_color_store.known_tags())
        )
        tag, ok = QInputDialog.getItem(
            self, "加入標籤", "選擇或輸入標籤：", items, 0, True
        )
        if not ok:
            return
        tag = tag.strip()
        if not tag:
            return
        self._assign_tag_to_paths(tag, paths)

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
        if self._active_tab_parked_dirty():
            self.statusBar().showMessage(
                "有未儲存的編輯，請先進入編輯模式", 4000
            )
            return
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

    # --- inline editing of a block in the preview (see assets/inline_edit.js) ---
    def _active_tab_parked_dirty(self) -> bool:
        """True when the active tab has a parked, unsaved WYSIWYG buffer.

        Esc'ing out of WYSIWYG (``_leave_wysiwyg_ui_keeping_buffer``) swaps
        the screen back to PREVIEW while leaving the dirty editor buffer
        parked in ``tab_state`` (``wysiwyg_parked``). ``self._view_mode``
        alone can no longer be trusted to mean "no editor owns this tab's
        buffer" -- callers that write straight to disk from the preview must
        also check this.
        """
        if not self._active_path:
            return False
        state = self._tab_state.get(self._active_path)
        if not state or not state.get("wysiwyg_parked"):
            return False
        document = state.get("editor_document")
        return isinstance(document, QTextDocument) and document.isModified()

    def _inline_edit_context(self):
        """Return (text, encoding, newline) for the file, or None if off-limits.

        Off-limits means: no Markdown document open, the text editor owns the
        buffer, or (see ``_active_tab_parked_dirty``) an Esc'd WYSIWYG buffer
        is parked with unsaved edits -- the same guard the task-checkbox
        write-back uses, so the preview can never write behind the editor's
        back.
        """
        if not self._current_file or not is_markdown(self._current_file):
            return None
        if view_mode.is_editing(self._view_mode):
            return None
        if self._active_tab_parked_dirty():
            self.statusBar().showMessage(
                "有未儲存的編輯，請先進入編輯模式", 4000
            )
            return None
        try:
            raw = self._current_file.read_bytes()
        except OSError:
            return None
        result = read_text(self._current_file)
        if result is None:
            return None
        text, encoding = result
        return text, encoding, "\r\n" if b"\r\n" in raw else "\n"

    def _inline_edit_signature(self) -> str:
        """The open file's revision, as a string the page can hand back.

        Comparing the block's text alone is not a sufficient optimistic lock:
        a document with two byte-identical tables makes ``original`` match
        either of them, so an external insert that shifts the line numbers by
        exactly one block's height lets a write sail through the text check
        and land on the *other* table. Pinning the file revision the line
        numbers were read from closes that hole -- any foreign write at all
        invalidates them, whatever it changed.

        Empty means "no signature available"; the commit path then skips the
        check rather than refusing everything.
        """
        if not self._current_file:
            return ""
        signature = self._file_signature(self._current_file)
        if signature is None:
            return ""
        return f"{signature[0]}:{signature[1]}"

    def _inline_edit_stale(self) -> dict:
        """Refuse a write whose line numbers can no longer be trusted.

        Deliberately does *not* reload the preview. The page is holding text
        the user typed and nothing else has a copy of it, so re-rendering to
        "fix" the stale line numbers destroys exactly what needs saving. The
        page keeps its stale numbers, which is safe: every later write is
        stopped by the signature check above before it can reach the file.
        The page puts up a warning strip with a "reload preview" button, so
        the user reloads once their text is somewhere safe.
        """
        self.statusBar().showMessage(
            "檔案已在外部變更，這次編輯沒有存進去；請先複製內容再重新載入預覽",
            8000,
        )
        return {"ok": False, "error": "stale"}

    def _inline_edit_reload(self) -> dict:
        """Re-render the preview, on the page's request (the stale strip)."""
        self._reload_preview()
        return {"ok": True}

    def _inline_edit_fetch(self, start: int, end: int) -> dict:
        """Hand the preview the raw Markdown behind one rendered block."""
        context = self._inline_edit_context()
        if context is None:
            return {"ok": False, "error": "unavailable"}
        source = extract_source_lines(context[0], start, end)
        if source is None:
            return {"ok": False, "error": "out-of-range"}
        reply = {"ok": True, "text": source, "sig": self._inline_edit_signature()}
        # Only pipe tables carry a model; every other block keeps the plain
        # reply the raw textarea path has always seen.
        model = parse_table(source)
        if model is not None:
            reply["table"] = model
        return reply

    def _inline_edit_commit(
        self,
        start: int,
        end: int,
        original: str,
        new: str,
        sig: str = "",
        done_message: str = "已更新段落",
    ) -> dict:
        """Write an inline preview edit back to the file it came from."""
        # Before anything else, and before the text comparison in particular:
        # a mismatched signature means the line numbers this write is aimed
        # at were read from a different revision of the file, so what the
        # text says about them proves nothing. An empty *sig* means the page
        # never got one (an older page, or a document that vanished), and the
        # check is skipped so injection-timing gaps cannot lock the user out.
        if sig and sig != self._inline_edit_signature():
            return self._inline_edit_stale()
        context = self._inline_edit_context()
        if context is None:
            # The page keeps the textarea open on a failure, so say why rather
            # than letting the typed text sit there unexplained.
            self.statusBar().showMessage("目前無法從預覽編輯這份文件", 6000)
            return {"ok": False, "error": "unavailable"}
        text, encoding, newline = context
        out = replace_source_lines(text, start, end, original, new, newline)
        if out is None:
            # The file moved under the preview, so its line numbers are
            # fiction now. Refuse the write and leave the page alone -- see
            # _inline_edit_stale for why re-rendering here is the wrong move.
            return self._inline_edit_stale()
        downgraded = False
        try:
            data = out.encode(encoding)
        except UnicodeEncodeError:
            downgraded = True
            data = out.encode("utf-8")
        try:
            atomic_write_bytes(self._current_file, data)
        except OSError as exc:
            self.statusBar().showMessage(f"無法儲存編輯：{exc}", 6000)
            return {"ok": False, "error": str(exc)}
        self._loaded_signature = self._file_signature(self._current_file)
        self._rearm_watch()
        self._reload_preview()  # keeps the scroll position
        if downgraded:
            self._editing_encoding = "utf-8"
            self.statusBar().showMessage(
                "內容含原編碼無法表示的字元，已改用 UTF-8 儲存", 6000
            )
        else:
            self.statusBar().showMessage(done_message, 3000)
        # An inline edit can add a wiki-link or a #tag just as a full save can,
        # so the same indexes have to catch up (see _save_file).
        self._refresh_link_index(force=True)
        self._update_front_tags()
        return {"ok": True}

    def _inline_edit_table_markdown(self, model_json: str) -> str | None:
        """Validate a grid model and render it, or None with the reason shown.

        Every rejection reaches the status bar. A silent ``bad-model`` leaves
        the grid sitting there looking editable while nothing is ever saved,
        which is the worst of both worlds -- the sibling ``stale`` and
        ``unavailable`` refusals have always said something.
        """
        try:
            model = json.loads(model_json)
        except (TypeError, ValueError):
            self.statusBar().showMessage(
                "表格內容無法解析，這次編輯沒有存進去", 6000
            )
            return None
        if not isinstance(model, dict) or not isinstance(model.get("headers"), list):
            self.statusBar().showMessage(
                "表格內容格式不正確，這次編輯沒有存進去", 6000
            )
            return None
        # An empty header list is refused on purpose: serialize_table would
        # return "", and replace_source_lines writing "" over the block would
        # delete the whole table. Deleting every column in the grid must not
        # silently mean "delete the table".
        if not model["headers"]:
            self.statusBar().showMessage(
                "表格至少要保留一欄，這次編輯沒有存進去", 6000
            )
            return None
        try:
            return serialize_table(model)
        except Exception:  # noqa: BLE001 - a malformed model must not reach the bridge
            self.statusBar().showMessage(
                "表格內容無法轉回 Markdown，這次編輯沒有存進去", 6000
            )
            return None

    def _inline_edit_serialize_table(self, model_json: str) -> dict:
        """Render the live grid model as Markdown without writing anything.

        Backs the grid's "switch to source" button: the textarea has to open
        on what the grid currently holds, not on the text the block was
        opened with, or every cell edit made before the click is lost.
        """
        text = self._inline_edit_table_markdown(model_json)
        if text is None:
            return {"ok": False, "error": "bad-model"}
        return {"ok": True, "text": text}

    def _inline_edit_commit_table(
        self,
        start: int,
        end: int,
        original: str,
        model_json: str,
        sig: str = "",
    ) -> dict:
        """Write the grid editor's table model back as Markdown."""
        new = self._inline_edit_table_markdown(model_json)
        if new is None:
            return {"ok": False, "error": "bad-model"}
        # Reuse the raw path wholesale: optimistic lock, atomic write, reload
        # and index refresh are all identical once we have text. Only the
        # status line differs -- this path always rewrote a table, never a
        # paragraph, and saying "段落" made the user look for a change that
        # was never there.
        return self._inline_edit_commit(
            start, end, original, new, sig, done_message="已更新表格"
        )

    def _inline_edit_paste_image(self) -> dict:
        """Save the clipboard image next to the document, return its link."""
        if self._inline_edit_context() is None:
            self.statusBar().showMessage("請先儲存文件才能貼入圖片", 4000)
            return {"ok": False, "error": "unavailable"}
        image = QGuiApplication.clipboard().image()
        if not isinstance(image, QImage) or image.isNull():
            return {"ok": False, "error": "no-image"}
        rel = save_clipboard_image(image, self._current_file)
        if rel is None:
            self.statusBar().showMessage("圖片儲存失敗，請重試", 4000)
            return {"ok": False, "error": "save-failed"}
        return {"ok": True, "link": markdown_image_link(rel)}

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
        if (
            self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda: export_actions.export_pptx(self),
                purpose="匯出 PowerPoint",
            )
            return
        export_actions.export_pptx(self)

    def _export_docx(self):
        if (
            self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        ):
            self._request_live_wysiwyg_snapshot(
                lambda: export_actions.export_docx(self),
                purpose="匯出 Word",
            )
            return
        export_actions.export_docx(self)

    def _export_html(self):
        export_actions.export_html(self)

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
        self._flush_pdf_zoom_pipeline()
        if self._deferred_update_close_approved:
            self._deferred_update_close_approved = False
            super().closeEvent(event)
            return
        if (
            self._edit_mode
            and self._active_edit_backend == edit_backend.WYSIWYG_BACKEND
            and not self._wysiwyg_close_snapshot_approved
        ):
            event.ignore()

            def _close_after_snapshot() -> None:
                self._wysiwyg_close_snapshot_approved = True
                self.close()

            self._request_live_wysiwyg_snapshot(
                _close_after_snapshot, purpose="關閉視窗"
            )
            return
        self._wysiwyg_close_snapshot_approved = False
        if session_state.close_event(self, event):
            if update_flow.defer_close_until_updates_finish(self, event):
                self._deferred_update_close_approved = True
                return
            super().closeEvent(event)
