"""Tabbed left workspace panel."""

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .annotations_panel import AnnotationsPanel
from .backlinks_panel import BacklinksPanel
from .file_browser import FileBrowserView
from .global_search import GlobalSearchView
from .pdf_highlights_panel import PdfMarkupPanel
from .recent_files import RecentFilesView
from .tags_panel import TagsPanel
from .theme import LIGHT, Theme, panel_stylesheet, svg_icon
from .toc import TocView


class LeftPanel(QWidget):
    def __init__(self, on_file_selected, on_anchor_clicked,
                 annotation_callbacks, pdf_note_callbacks=None,
                 pdf_highlight_callbacks=None,
                 on_tag_selected=None, search_roots_provider=None,
                 on_search_result=None,
                 on_manage_tags=None, tag_color_for=None,
                 on_create_tag=None, on_delete_tag=None,
                 on_rename_tag=None,
                 on_assign_tag_to_paths=None,
                 on_open_file=None,
                 on_rename_file=None,
                 on_move_file=None,
                 on_delete_file=None,
                 on_reveal_file=None,
                 files_for_tag=None,
                 on_doc_tags_changed=None,
                 theme: Theme = LIGHT, parent=None):
        super().__init__(parent)
        self.setObjectName("leftPanel")
        self.setMinimumWidth(180)
        self.setMaximumWidth(420)
        self._on_file_selected = on_file_selected
        self._theme = theme

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(4)

        self._title_label = QLabel("工作面板")
        self._title_label.setObjectName("panelTitle")

        self._close_btn = QPushButton()
        self._close_btn.setToolTip("收合側邊欄")
        self._close_btn.setAccessibleName("收合側邊欄")
        self._close_btn.setIconSize(QSize(20, 20))

        header_layout.addWidget(self._title_label)
        header_layout.addStretch()
        header_layout.addWidget(self._close_btn)
        layout.addWidget(self._header)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        tag_index = annotation_callbacks.get("tag_index")
        self._file_browser = FileBrowserView(
            on_file_selected=on_file_selected,
            tag_index=tag_index,
            on_manage_tags=on_manage_tags,
            tag_color_for=tag_color_for,
        )
        self._recent = RecentFilesView(
            on_file_selected=on_file_selected,
            tag_index=tag_index,
        )
        self._toc = TocView(on_anchor_clicked=on_anchor_clicked)
        self._annotations = AnnotationsPanel(annotation_callbacks)
        self._pdf_markup = PdfMarkupPanel(
            pdf_note_callbacks or {}, pdf_highlight_callbacks or {},
            on_doc_tags_changed=on_doc_tags_changed,
        )
        # The "標註" tab swaps between Markdown annotations and PDF markup
        # (highlights + page notes).
        self._annot_stack = QStackedWidget()
        self._annot_stack.addWidget(self._annotations)  # index 0 (markdown)
        self._annot_stack.addWidget(self._pdf_markup)   # index 1 (pdf)
        self._backlinks = BacklinksPanel(on_file_selected=on_file_selected)
        self._tags = TagsPanel(
            on_tag_selected=on_tag_selected or (lambda _t: None),
            tag_color_for=tag_color_for,
            on_create_tag=on_create_tag,
            on_delete_tag=on_delete_tag,
            on_rename_tag=on_rename_tag,
            on_assign_tag_to_paths=on_assign_tag_to_paths,
            # Reuse the same open-file callback the file browser uses, so
            # clicking a file under a tag opens it exactly like elsewhere.
            on_open_file=on_open_file or on_file_selected,
            # File-child context menu actions in the 標籤 tab. These reuse the
            # window's shared file operations (same as the 檔案 tab) so the tag
            # index and every view stay in sync; all default to None.
            on_manage_tags=on_manage_tags,
            on_rename_file=on_rename_file,
            on_move_file=on_move_file,
            on_delete_file=on_delete_file,
            on_reveal_file=on_reveal_file,
            # Lazily list a tag's files as its tree children when expanded.
            files_for_tag=files_for_tag,
        )
        self._search = GlobalSearchView(
            roots_provider=search_roots_provider or (lambda: []),
            on_result_selected=on_search_result or (
                lambda path, _query, _line: on_file_selected(path)
            ),
        )

        self._tabs.addTab(self._file_browser, "檔案")
        self._tabs.addTab(self._recent, "最近")
        self._tabs.addTab(self._toc, "目錄")
        self._tabs.addTab(self._annot_stack, "標註")
        # Keep backlinks at index 4 so the annotations tab index (3) is unchanged.
        self._tabs.addTab(self._backlinks, "連結")
        self._tabs.addTab(self._tags, "標籤")
        # Keep every established tab index stable; global search is appended.
        self._tabs.addTab(self._search, "搜尋")

        layout.addWidget(self._tabs)
        self.apply_theme(theme)

    @property
    def toc(self) -> TocView:
        return self._toc

    @property
    def file_browser(self) -> FileBrowserView:
        return self._file_browser

    @property
    def recent(self) -> RecentFilesView:
        return self._recent

    @property
    def annotations(self) -> AnnotationsPanel:
        return self._annotations

    @property
    def backlinks(self) -> BacklinksPanel:
        return self._backlinks

    @property
    def pdf_markup(self) -> PdfMarkupPanel:
        return self._pdf_markup

    @property
    def pdf_notes(self):
        return self._pdf_markup.notes

    @property
    def pdf_highlights(self):
        return self._pdf_markup.highlights

    @property
    def tags(self) -> TagsPanel:
        return self._tags

    @property
    def search(self) -> GlobalSearchView:
        return self._search

    def show_search(self):
        index = self._tabs.indexOf(self._search)
        if index >= 0:
            self._tabs.setCurrentIndex(index)
            self._search.focus_input()

    def show_pdf_notes(self, show: bool):
        self._annot_stack.setCurrentIndex(1 if show else 0)

    @property
    def close_btn(self) -> QPushButton:
        return self._close_btn

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(panel_stylesheet(theme))
        self._close_btn.setIcon(svg_icon("chevron-left", theme.text_muted, 20))
        self._file_browser.apply_theme(theme)
        self._recent.apply_theme(theme)
        self._toc.apply_theme(theme)
        self._annotations.apply_theme(theme)
        self._pdf_markup.apply_theme(theme)
        self._backlinks.apply_theme(theme)
        self._tags.apply_theme(theme)
        self._search.apply_theme(theme)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "開啟文件",
            "",
            "支援的文件 (*.md *.markdown *.pdf);;Markdown 檔案 (*.md *.markdown);;PDF 檔案 (*.pdf);;所有檔案 (*)",
        )
        if path:
            self._on_file_selected(path)

    def switch_to(self, index: int):
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def set_annotations_enabled(self, enabled: bool):
        index = self._tabs.indexOf(self._annot_stack)
        if index < 0:
            return
        self._tabs.setTabEnabled(index, enabled)
        if not enabled and self._tabs.currentIndex() == index:
            self._tabs.setCurrentIndex(0)
