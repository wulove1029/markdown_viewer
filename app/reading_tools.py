"""A contextual reading outline and portable, source-linked excerpts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import collection_stylesheet
from .toc import TocView


@dataclass(frozen=True)
class Excerpt:
    identity: str
    text: str
    note: str = ""
    tags: tuple[str, ...] = ()
    page: int | None = None


def excerpts_markdown(path: Path, entries: list[Excerpt]) -> str:
    """Keep file URLs escaped and PDF page numbers human-readable (one based)."""
    title = path.name.replace("[", "\\[").replace("]", "\\]")
    parts = [f"# {path.stem} — 閱讀摘錄\n"]
    for entry in entries:
        fragment = (f"page={entry.page + 1}" if entry.page is not None
                    else "annotation=" + quote(entry.identity, safe=""))
        label = title + (f" · 第 {entry.page + 1} 頁" if entry.page is not None else "")
        parts.append(f"[來源：{label}](<{path.resolve().as_uri()}#{fragment}>)\n")
        if entry.text:
            parts.append("\n".join("> " + line for line in entry.text.splitlines()) + "\n")
        if entry.note:
            parts.append(entry.note + "\n")
        if entry.tags:
            parts.append("標籤：" + "、".join(entry.tags) + "\n")
    return "\n".join(parts) + "\n"


class ReadingTools(QObject):
    def __init__(self, window, settings):
        super().__init__(window)
        self.window = window
        self.settings = settings
        self.entries: list[Excerpt] = []
        self.history = []
        self._status_path = None
        self._reading_ratio = 0.0
        self._active_heading = ""
        self._theme = None
        self.status = QLabel()
        self.status.setAccessibleName("目前閱讀章節與進度")
        self.status.setMaximumWidth(320)
        window.statusBar().addPermanentWidget(self.status)
        self.dock = QDockWidget("閱讀工具", window)
        self.dock.setObjectName("readingToolsDock")
        self.tabs = QTabWidget()
        outline = QWidget()
        layout = QVBoxLayout(outline)
        row = QHBoxLayout()
        for label, callback in (("上一章", lambda: self.step(-1)),
                                ("下一章", lambda: self.step(1)),
                                ("返回", self.go_back)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.toc = TocView(self.navigate)
        layout.addWidget(self.toc)
        self.tabs.addTab(outline, "大綱")
        notes = QWidget()
        layout = QVBoxLayout(notes)
        self.query = QLineEdit()
        self.query.setPlaceholderText("篩選摘錄、筆記或標籤…")
        self.query.setAccessibleName("篩選目前文件註記")
        self.query.textChanged.connect(self.filter)
        layout.addWidget(self.query)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.setWordWrap(True)
        self.list.itemActivated.connect(self.activate)
        layout.addWidget(self.list)
        self.hint = QLabel("選取後複製；未選取時複製篩選結果。雙擊回原文。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        row = QHBoxLayout()
        copy = QPushButton("複製含來源摘錄")
        copy.clicked.connect(self.copy)
        create = QPushButton("整理成筆記")
        create.clicked.connect(self.create_note)
        row.addWidget(copy)
        row.addWidget(create)
        layout.addLayout(row)
        self.tabs.addTab(notes, "註記與摘錄")
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(4, 4, 4, 4)
        controls = QHBoxLayout()
        controls.addStretch()
        self.pin = QPushButton("釘選面板")
        self.pin.setCheckable(True)
        self.pin.setAccessibleName("固定右側工具面板位置")
        self.pin.setToolTip("固定閱讀工具與屬性面板，避免拖動或浮出視窗；仍可關閉面板。")
        controls.addWidget(self.pin)
        outer.addLayout(controls)
        outer.addWidget(self.tabs)
        self.dock.setWidget(container)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        window.tabifyDockWidget(window._properties_dock, self.dock)
        self.dock.hide()
        pinned = settings.value("reading_tools_pinned", False)
        self.pin.setChecked(pinned is True or str(pinned).lower() in {"1", "true"})
        self._apply_pinned(self.pin.isChecked(), persist=False)
        self.pin.toggled.connect(self._apply_pinned)
        self.dock.visibilityChanged.connect(lambda visible: self.refresh() if visible else None)
        changed = getattr(window._panel.toc, "entries_changed", None)
        if hasattr(changed, "connect"):
            changed.connect(self.update_outline)
        window._renderer.active_anchor_changed.connect(self.toc.set_active_anchor)
        window._renderer.active_anchor_changed.connect(self._heading_changed)
        progress = getattr(window._renderer, "reading_progress_changed", None)
        if hasattr(progress, "connect"):
            progress.connect(self._progress_changed)
        window._pdf_view.page_changed.connect(self._page_changed)
        window._view_menu.addAction(self.dock.toggleViewAction())

    def _apply_pinned(self, pinned, *, persist=True):
        features = QDockWidget.DockWidgetFeature.DockWidgetClosable
        if not pinned:
            features |= (QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        for dock in (self.dock, self.window._properties_dock):
            if pinned and dock.isFloating():
                dock.setFloating(False)
            dock.setFeatures(features)
        self.pin.setText("已釘選面板" if pinned else "釘選面板")
        if persist:
            self.settings.setValue("reading_tools_pinned", bool(pinned))

    def _heading_changed(self, anchor):
        self.sync_status()
        self._active_heading = next((str(title) for _level, title, target in
                                     self.window._panel.toc.entries() if target == anchor), "")
        self.sync_status()

    def _progress_changed(self, ratio):
        self.sync_status()
        self._reading_ratio = max(0.0, min(1.0, float(ratio)))
        self.sync_status()

    def sync_status(self):
        window = self.window
        self.apply_theme(window._theme)
        if window._current_file != self._status_path:
            self._status_path = window._current_file
            self._reading_ratio = 0.0
            self._active_heading = ""
        visible = bool(window._current_file and window._current_kind in {"markdown", "pdf"}
                       and not window._edit_mode)
        self.status.setVisible(visible)
        if not visible:
            return
        if window._current_kind == "pdf":
            total = window._pdf_view.page_count()
            text = (f"第 {window._pdf_view.current_page() + 1} / {total} 頁"
                    if total else "正在取得頁碼…")
        else:
            text = f"閱讀 {self._reading_ratio:.0%}"
            if self._active_heading:
                text += " · " + self._active_heading
        self.status.setToolTip(text)
        self.status.setAccessibleName(text)
        self.status.setText(self.status.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, 310))

    def apply_theme(self, theme):
        if self._theme == theme:
            return
        self._theme = theme
        self.tabs.setStyleSheet(f"""
QTabWidget::pane {{ background: {theme.surface}; border: 1px solid {theme.border}; }}
QTabWidget > QWidget {{ background: {theme.surface}; color: {theme.text}; }}
QTabBar::tab {{ background: {theme.window}; color: {theme.text_muted}; padding: 8px 10px; }}
QTabBar::tab:selected {{ background: {theme.surface}; color: {theme.text};
    border-bottom: 2px solid {theme.accent}; }}
QLabel {{ color: {theme.text_muted}; background: transparent; }}
QLineEdit {{ background: {theme.window}; color: {theme.text}; border: 1px solid {theme.border};
    padding: 7px; border-radius: 4px; }}
QLineEdit:focus {{ border: 2px solid {theme.accent}; }}
""")
        self.list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))
        self.toc.apply_theme(theme)

    def update_outline(self, entries):
        if self.dock.isHidden():
            return
        if self.toc.entries() == list(entries):
            return
        self.toc._populate(entries, "目前文件沒有大綱")

    def _page_changed(self, page):
        self.sync_status()
        candidates = [target for target in self.toc._anchors
                      if isinstance(target, int) and target <= page]
        self.toc.set_active_anchor(max(candidates) if candidates else None)

    def refresh(self):
        self.sync_status()
        if self.dock.isHidden():
            return
        self.update_outline(self.window._panel.toc.entries() or [])
        if self.toc._theme != self.window._theme:
            self.toc.apply_theme(self.window._theme)
        self.entries = []
        if self.window._current_kind == "markdown":
            self.entries = [Excerpt(a.id, a.exact, a.note, tuple(a.tags))
                            for a in self.window._doc_annotations.annotations]
        elif self.window._current_kind == "pdf":
            self.entries = [Excerpt(a.id, a.text, a.note, tuple(a.tags), a.page)
                            for a in getattr(self.window, "_pdf_highlights", [])]
            self.entries += [Excerpt(a.id, "", a.note, tuple(a.tags), a.page)
                             for a in getattr(self.window, "_pdf_notes", [])]
        self.filter()

    def filter(self, *_args):
        terms = self.query.text().casefold().split()
        self.list.clear()
        for entry in self.entries:
            text = "\n".join(filter(None, (entry.text, entry.note, " · ".join(entry.tags))))
            if not all(term in text.casefold() for term in terms):
                continue
            prefix = f"第 {entry.page + 1} 頁 · " if entry.page is not None else ""
            if (getattr(self.window, "_annotation_orphans_path", None) == self.window._current_file
                    and entry.identity in getattr(self.window, "_annotation_orphans", set())):
                prefix += "找不到原文 · "
            item = QListWidgetItem(prefix + text)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list.addItem(item)
        self.hint.setText(f"{self.list.count()} 則 · 雙擊回原文；未選取時匯出全部篩選結果。")

    def selected_entries(self):
        items = self.list.selectedItems() or [self.list.item(i) for i in range(self.list.count())]
        return [item.data(Qt.ItemDataRole.UserRole) for item in items]

    def copy(self):
        if self.window._current_file and self.selected_entries():
            QApplication.clipboard().setText(excerpts_markdown(
                self.window._current_file, self.selected_entries()))
            self.window.statusBar().showMessage("已複製摘錄及來源連結。", 4000)

    def create_note(self):
        if self.window._current_file and self.selected_entries():
            self.window._new_draft(excerpts_markdown(
                self.window._current_file, self.selected_entries()))

    def remember(self):
        window = self.window
        if window._current_file:
            position = (window._pdf_view.current_page() if window._current_kind == "pdf"
                        else window._renderer.scroll_y())
            self.history.append((str(window._current_file), window._current_kind, position))
            self.history = self.history[-50:]

    def navigate(self, target):
        self.remember()
        self.window._scroll_to_anchor(target)

    def step(self, direction):
        self.refresh()
        self.toc.step(direction)

    def go_back(self):
        if not self.history:
            return
        path, kind, position = self.history.pop()
        self.window._open_document_target(path, f"page={position + 1}" if kind == "pdf"
                                          else f"scroll={position}")

    def activate(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.remember()
        if entry.page is not None:
            self.window._pdf_view.jump_to_page(entry.page)
        elif self.window._find_annotation(entry.identity) is not None:
            self.window._annot_activated(entry.identity)
        else:
            self.window.statusBar().showMessage("來源註記已不存在，請重新整理。", 5000)
