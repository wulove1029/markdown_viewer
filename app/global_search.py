"""Background full-text search across Markdown and plain-text libraries."""

from __future__ import annotations

import os
import re
from collections import deque
from dataclasses import dataclass
from html import escape
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Callable, Iterable, Mapping

from PySide6.QtCore import (
    QEvent,
    QRunnable,
    QSize,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QFont, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import background_jobs, diagnostics
from .document_libraries import load_excluded_folders, prune_directory_names
from .file_types import document_kind
from .md_converter import read_text_detailed
from .search_index import SearchCancelled, SearchIndex, default_index, directory_versions
from .theme import LIGHT, Theme, collection_stylesheet
from .worker_relay import WorkerRelay

_PATH_ROLE = Qt.ItemDataRole.UserRole
_QUERY_ROLE = Qt.ItemDataRole.UserRole.value + 1
_LINE_ROLE = Qt.ItemDataRole.UserRole.value + 2
_HEADER_ROLE = Qt.ItemDataRole.UserRole.value + 3
_SNIPPET_ROLE = Qt.ItemDataRole.UserRole.value + 4

@dataclass(frozen=True)
class SearchHit:
    line_number: int
    line: str
    match_spans: tuple[tuple[int, int], ...]

    @property
    def match_count(self) -> int:
        return len(self.match_spans)


@dataclass(frozen=True)
class FileSearchResult:
    path: Path
    hits: tuple[SearchHit, ...]

    @property
    def match_count(self) -> int:
        return sum(hit.match_count for hit in self.hits)


@dataclass(frozen=True)
class SearchIssue:
    path: Path
    reason: str


@dataclass(frozen=True)
class SearchReport:
    results: tuple[FileSearchResult, ...] = ()
    roots: tuple[Path, ...] = ()
    readable_roots: int = 0
    searched_files: int = 0
    issues: tuple[SearchIssue, ...] = ()
    cancelled: bool = False
    source_error: bool = False


def search_markdown_files(
    roots: Iterable[str | Path],
    query: str,
    should_cancel: Callable[[], bool] | None = None,
) -> list[FileSearchResult]:
    """Return text-file matches, retaining the original list-based API."""
    return list(search_document_files(roots, query, should_cancel).results)


def search_document_files(
    roots: Iterable[str | Path],
    query: str,
    should_cancel: Callable[[], bool] | None = None,
    *,
    index: SearchIndex | None = None,
    overlays: Mapping[str | Path, str] | None = None,
    on_results: Callable[[tuple[FileSearchResult, ...], int], None] | None = None,
) -> SearchReport:
    """Search exact substrings, validating cached content against disk versions.

    Optional overlays are immutable snapshots of unsaved buffers captured on
    the GUI thread. Only paths within searchable roots participate. Progress
    callbacks run on the worker thread and contain newly discovered results.
    """

    needle = query.strip()
    if not needle:
        return SearchReport()
    cancelled = should_cancel or (lambda: False)
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    results: list[FileSearchResult] = []
    seen_files: set[str] = set()
    issues: dict[str, SearchIssue] = {}
    unique_roots = tuple(_unique_roots(roots))
    readable_roots = 0
    searched_files = 0
    excluded_folders = load_excluded_folders()
    content_index = index if index is not None else default_index
    overlay_text = {
        _path_key(Path(path).absolute()): text for path, text in (overlays or {}).items()
    }
    pending_results: list[FileSearchResult] = []
    last_progress = 0.0

    def record_issue(path: Path, reason: str):
        issues.setdefault(_path_key(path), SearchIssue(path, reason))

    def search_path(path: Path, version=None):
        nonlocal searched_files, last_progress
        key = _path_key(path)
        if key in seen_files:
            return
        seen_files.add(key)
        try:
            text = (
                overlay_text[key] if key in overlay_text
                else content_index.read(path, read_text_detailed, cancelled, version)
            )
        except OSError as exc:
            record_issue(path, str(exc))
            return
        if text is None:
            record_issue(path, "無法辨識文字編碼")
            return
        searched_files += 1
        # Most searches match a small portion of the library. Do not allocate
        # a complete splitlines list for every nonmatching cached document.
        if not pattern.search(text):
            return
        hits: list[SearchHit] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line_number % 256 == 0 and cancelled():
                raise SearchCancelled()
            spans = tuple((match.start(), match.end()) for match in pattern.finditer(line))
            if spans:
                hits.append(SearchHit(line_number, line, spans))
        if hits:
            result = FileSearchResult(path, tuple(hits))
            results.append(result)
            pending_results.append(result)
            now = monotonic()
            if on_results is not None and (last_progress == 0 or now - last_progress >= 0.1):
                if cancelled():
                    raise SearchCancelled()
                on_results(tuple(pending_results), searched_files)
                pending_results.clear()
                last_progress = now

    for root in unique_roots:
        if cancelled():
            return SearchReport(cancelled=True)
        try:
            is_directory = root.is_dir()
        except OSError as exc:
            record_issue(root, str(exc))
            continue
        if not is_directory:
            record_issue(root, "資料夾不存在或無法存取")
            continue

        def on_walk_error(error: OSError):
            record_issue(Path(error.filename) if error.filename else root, str(error))

        root_readable = False
        for dirpath, dirnames, filenames in os.walk(
            root, onerror=on_walk_error
        ):
            if cancelled():
                return SearchReport(cancelled=True)
            if not root_readable:
                readable_roots += 1
                root_readable = True
            relative_parent = Path(dirpath).relative_to(root)
            prune_directory_names(dirnames, relative_parent, excluded_folders)
            try:
                versions = directory_versions(Path(dirpath), cancelled)
            except SearchCancelled:
                return SearchReport(cancelled=True)
            for filename in filenames:
                if cancelled():
                    return SearchReport(cancelled=True)
                if document_kind(filename) not in ("markdown", "text"):
                    continue
                path = Path(dirpath) / filename
                try:
                    search_path(path, versions.get(filename))
                except SearchCancelled:
                    return SearchReport(cancelled=True)

        # A deleted-on-disk file may still be open with unsaved text. Include
        # that buffer under the same scope and exclusion rules as disk files.
        for overlay_path in (overlays or {}):
            path = Path(overlay_path).absolute()
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            allowed = True
            for depth, directory in enumerate(relative.parts[:-1]):
                names = [directory]
                prune_directory_names(names, Path(*relative.parts[:depth]), excluded_folders)
                if not names:
                    allowed = False
                    break
            if allowed and document_kind(path.name) in ("markdown", "text"):
                try:
                    search_path(path)
                except SearchCancelled:
                    return SearchReport(cancelled=True)

    if cancelled():
        return SearchReport(cancelled=True)
    if on_results is not None and pending_results:
        on_results(tuple(pending_results), searched_files)
    if cancelled():
        return SearchReport(cancelled=True)
    return SearchReport(
        results=tuple(sorted(results, key=lambda result: str(result.path).casefold())),
        roots=unique_roots,
        readable_roots=readable_roots,
        searched_files=searched_files,
        issues=tuple(issues.values()),
    )


def _unique_roots(roots: Iterable[str | Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = Path(root).expanduser()
        try:
            path = path.resolve()
        except OSError:
            pass
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(path)).casefold()


class _SearchTask(QRunnable):
    """One library search; reports through the view's ``WorkerRelay.emit``.

    ``on_progress(request_id, query, batch, scanned)`` and
    ``on_finished(request_id, query, results)`` may run on a worker thread.
    The task owns no Qt object, so nothing Qt tracks is released on the worker.
    """

    def __init__(
        self,
        request_id: int,
        roots: list[Path],
        query: str,
        cancel_event: Event,
        index: SearchIndex,
        overlays: Mapping[str | Path, str],
        *,
        on_progress: Callable[[int, str, object, int], None],
        on_finished: Callable[[int, str, object], None],
    ):
        super().__init__()
        self.request_id = request_id
        self.roots = roots
        self.query = query
        self.cancel_event = cancel_event
        self.index = index
        self.overlays = overlays
        self.on_progress = on_progress
        self.on_finished = on_finished

    @Slot()
    def run(self):
        started = monotonic()
        try:
            results = search_document_files(
                self.roots, self.query, self.cancel_event.is_set,
                index=self.index, overlays=self.overlays,
                on_results=lambda batch, scanned: self.on_progress(
                    self.request_id, self.query, batch, scanned
                ),
            )
        except Exception as exc:
            results = SearchReport(
                roots=tuple(self.roots), source_error=True,
                issues=(SearchIssue(Path("搜尋索引"), str(exc)),),
            )
        diagnostics.record(
            "library_search", duration_ms=(monotonic() - started) * 1000,
            work_items=results.searched_files,
            cancelled=results.cancelled or self.cancel_event.is_set(),
            failed=results.source_error,
        )
        self.on_finished(self.request_id, self.query, results)


class _SearchResultDelegate(QStyledItemDelegate):
    """Draw highlighted snippets only for visible rows, without child widgets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.theme = LIGHT

    def paint(self, painter, option, index):
        line = index.data(_SNIPPET_ROLE)
        if line is None:
            super().paint(painter, option, index)
            return
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        style = styled.widget.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setDocumentMargin(0)
        document.setHtml(
            f"<span style='color:{self.theme.text_subtle};'>"
            f"{index.data(_LINE_ROLE)}:</span> "
            + _highlighted_snippet(line, index.data(_QUERY_ROLE), self.theme)
        )
        document.setTextWidth(max(1, option.rect.width() - 16))
        painter.save()
        painter.setClipRect(option.rect)
        painter.translate(option.rect.left() + 8, option.rect.top() + 4)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, QColor(self.theme.text))
        document.documentLayout().draw(painter, context)
        painter.restore()


class GlobalSearchView(QWidget):
    """Debounced background search UI for the left workspace panel."""

    def __init__(
        self,
        roots_provider: Callable[[], Iterable[str | Path]],
        on_result_selected: Callable[[str, str, int], None],
        parent=None,
        *,
        overlays_provider: Callable[[], Mapping[str | Path, str]] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("globalSearch")
        self._roots_provider = roots_provider
        self._on_result_selected = on_result_selected
        self._overlays_provider = overlays_provider
        self._index = default_index
        self._theme = LIGHT
        self._request_id = 0
        self._cancel_event: Event | None = None
        self._results: list[FileSearchResult] = []
        self._report: SearchReport | None = None
        self._active_query = ""
        self._tasks: dict[int, _SearchTask] = {}
        self._pending_task: _SearchTask | None = None
        self._composing = False
        self._resume_after_composition = False
        self._pool = background_jobs.pool()
        # Search tasks report through relays this view owns; a task outliving
        # the view reports nothing and releases no Qt object on its worker.
        self._progress_relay = WorkerRelay(self, self._on_search_progress)
        self._finished_relay = WorkerRelay(self, self._on_search_finished)
        self._render_queue = deque()
        self._render_query = ""
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._append_pending_rows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("搜尋文件庫文字內容")
        self._input.setAccessibleName("搜尋文件庫文字內容")
        self._input.setClearButtonEnabled(True)
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

        self._scope = QLabel("搜尋 .md、.markdown、.txt；不含 PDF")
        self._scope.setProperty("muted", True)
        self._scope.setWordWrap(True)
        layout.addWidget(self._scope)

        self._list = QListWidget()
        self._delegate = _SearchResultDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setUniformItemSizes(True)
        self._list.setAccessibleName("文件庫搜尋結果")
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.installEventFilter(self)
        layout.addWidget(self._list, stretch=1)

        self._status = QLabel("輸入關鍵字開始搜尋")
        self._status.setProperty("muted", True)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self._start_search)
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._search_now)

        self.apply_theme(LIGHT)

    def focus_input(self):
        self._input.setFocus()
        self._input.selectAll()

    def set_overlays_provider(
        self, provider: Callable[[], Mapping[str | Path, str]] | None
    ) -> None:
        """Provide GUI-thread snapshots of unsaved file-backed text documents."""
        self._overlays_provider = provider

    def refresh(self) -> None:
        """Search the current query again after a save or library change."""
        self._debounce.stop()
        self._start_search()

    def rebuild_index(self) -> None:
        """Drop the derived content cache; source files are never modified."""
        self._index.invalidate()
        self.refresh()

    def closeEvent(self, event):
        self._request_id += 1
        self._composing = False
        self._resume_after_composition = False
        self._debounce.stop()
        self._render_timer.stop()
        self._pending_task = None
        if self._cancel_event is not None:
            self._cancel_event.set()
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if obj is self._input:
            if event.type() == QEvent.Type.InputMethod:
                was_composing = self._composing
                self._composing = bool(event.preeditString())
                if self._composing:
                    self._resume_after_composition |= self._debounce.isActive()
                    self._debounce.stop()
                elif was_composing:
                    self._resume_composition_search()
            elif event.type() == QEvent.Type.FocusOut:
                self._composing = False
                self._resume_composition_search()
            elif event.type() == QEvent.Type.KeyPress and self._composing:
                return False
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if obj is self._input and key == Qt.Key.Key_Down:
                for row in range(self._list.count()):
                    if self._list.item(row).data(_PATH_ROLE):
                        self._list.setCurrentRow(row)
                        self._list.setFocus()
                        return True
            elif obj is self._list and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Consume this event instead of also wiring itemActivated:
                # Qt platform styles differ in when activation is emitted.
                item = self._list.currentItem()
                if item is not None and not event.isAutoRepeat():
                    self._on_item_clicked(item)
                return True
        return super().eventFilter(obj, event)

    def _resume_composition_search(self):
        if self._resume_after_composition and self._input.text().strip():
            self._debounce.start()
        self._resume_after_composition = False

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self._delegate.theme = theme
        self.setStyleSheet(
            f"""
QWidget#globalSearch {{ background: {theme.surface}; }}
QWidget#globalSearch QLineEdit {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 32px;
    padding: 4px 10px;
}}
QWidget#globalSearch QLineEdit:focus {{ border-color: {theme.accent}; }}
QWidget#globalSearch QLabel[muted="true"] {{
    color: {theme.text_muted};
    font-size: 12px;
}}
"""
        )
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))
        if self._results or self._report is not None:
            self._render_results(self._active_query, self._results, self._report)

    def _on_text_changed(self, text: str):
        self._request_id += 1
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._debounce.stop()
        self._pending_task = None
        self._render_timer.stop()
        self._render_queue.clear()
        self._active_query = ""
        self._results = []
        self._report = None
        self._list.clear()
        self._status.setToolTip("")
        if not text.strip():
            self._status.setText("輸入關鍵字開始搜尋")
            return
        self._status.setText("等待搜尋…")
        if self._composing:
            self._resume_after_composition = True
        else:
            self._debounce.start()

    def _search_now(self):
        if self._composing:
            return
        self._debounce.stop()
        self._start_search()

    def _start_search(self):
        if self._composing:
            self._resume_after_composition = True
            return
        query = self._input.text().strip()
        if not query:
            return
        self._request_id += 1
        self._pending_task = None
        request_id = self._request_id
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._cancel_event = Event()
        self._active_query = ""
        self._results = []
        self._report = None
        self._status.setToolTip("")
        try:
            roots = [Path(root) for root in self._roots_provider()]
            overlays = dict(self._overlays_provider()) if self._overlays_provider else {}
        except Exception:
            self._on_search_finished(request_id, query, SearchReport(source_error=True))
            return
        self._list.clear()
        self._render_queue.clear()
        self._render_timer.stop()
        self._status.setText("正在搜尋…")
        task = _SearchTask(
            request_id, roots, query, self._cancel_event, self._index, overlays,
            on_progress=self._progress_relay.emit, on_finished=self._finished_relay.emit,
        )
        if self._tasks:
            # Keep only the latest waiting request. A cancelled scan exits at
            # the next file/line checkpoint before another scan consumes I/O.
            self._pending_task = task
        else:
            self._launch_task(task)

    def _launch_task(self, task: _SearchTask):
        self._tasks[task.request_id] = task
        self._pool.start(task, background_jobs.VISIBLE)

    def _on_search_progress(self, request_id, query, batch, scanned):
        if request_id != self._request_id or query != self._input.text().strip():
            return
        self._active_query = query
        self._results.extend(batch)
        self._queue_results(query, batch)
        self._status.setText(f"正在搜尋…已找到 {len(self._results)} 個檔案，已檢查 {scanned} 個")

    def _on_search_finished(
        self,
        request_id: int,
        query: str,
        results: SearchReport | list[FileSearchResult],
    ):
        self._tasks.pop(request_id, None)
        if self._pending_task is not None and not self._tasks:
            task = self._pending_task
            self._pending_task = None
            self._launch_task(task)
        if request_id != self._request_id or query != self._input.text().strip():
            return
        if isinstance(results, SearchReport):
            if results.cancelled:
                return
            self._report = results
            result_list = list(results.results)
        else:
            self._report = None
            result_list = results
        self._active_query = query
        self._results = result_list
        self._render_results(query, result_list, self._report)

    def _render_results(
        self, query: str, results: list[FileSearchResult], report: SearchReport | None = None
    ):
        self._render_timer.stop()
        self._render_queue.clear()
        self._list.clear()
        self._status.setToolTip(
            "\n".join(f"{issue.path}：{issue.reason}" for issue in report.issues)
            if report else ""
        )
        skipped = (
            f"；已略過 {len(report.issues)} 個無法讀取的項目"
            if report and report.issues else ""
        )
        if not results:
            message = "找不到符合的內容"
            if report is not None:
                if report.source_error:
                    message = "無法取得文件庫來源，請檢查文件庫設定"
                elif not report.roots:
                    message = "尚未加入文件庫，請到「檔案」加入資料夾"
                elif not report.readable_roots:
                    message = "無法讀取文件庫，請確認資料夾存在且可存取"
                elif not report.searched_files:
                    message = (
                        "無法讀取可搜尋的文字文件" if report.issues
                        else "文件庫中沒有可搜尋的文字文件"
                    )
            item = QListWidgetItem(message)
            item.setForeground(QColor(self._theme.text_subtle))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            self._status.setText(message + skipped)
            return

        total = sum(result.match_count for result in results)
        self._queue_results(query, results)
        self._status.setText(f"共 {total} 筆，{len(results)} 個檔案" + skipped)

    def _queue_results(self, query: str, results: Iterable[FileSearchResult]):
        self._render_query = query
        for result in results:
            self._render_queue.append((result, None))
            self._render_queue.extend((result, hit) for hit in result.hits)
        self._append_pending_rows()

    def _append_pending_rows(self):
        # Bound GUI work independently of result count. All rows remain queued
        # and reachable; there is no silent result limit.
        started = monotonic()
        self._list.setUpdatesEnabled(False)
        while self._render_queue:
            result, hit = self._render_queue.popleft()
            self._append_row(self._render_query, result, hit)
            if monotonic() - started >= 0.008:
                break
        self._list.setUpdatesEnabled(True)
        if self._render_queue:
            self._render_timer.start()
        else:
            self._render_timer.stop()

    def _append_row(self, query: str, result: FileSearchResult, hit: SearchHit | None):
        if hit is None:
            header = QListWidgetItem(f"{result.path.name}（{result.match_count} 筆）")
            font = QFont()
            font.setBold(True)
            header.setFont(font)
            header.setForeground(QColor(self._theme.text_muted))
            header.setToolTip(str(result.path))
            header.setData(_HEADER_ROLE, True)
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            header.setSizeHint(QSize(0, 42))
            self._list.addItem(header)
            return
        item = QListWidgetItem()
        item.setText(f"{hit.line_number}: {hit.line}")
        item.setSizeHint(QSize(0, 42))
        item.setToolTip(f"{result.path}:{hit.line_number}")
        item.setData(_PATH_ROLE, str(result.path))
        item.setData(_QUERY_ROLE, query)
        item.setData(_LINE_ROLE, hit.line_number)
        item.setData(_SNIPPET_ROLE, hit.line)
        self._list.addItem(item)

    def _on_item_clicked(self, item: QListWidgetItem):
        path = item.data(_PATH_ROLE)
        query = item.data(_QUERY_ROLE)
        line_number = item.data(_LINE_ROLE)
        if path and query and line_number:
            self._on_result_selected(path, query, int(line_number))


def _highlighted_snippet(line: str, query: str, theme: Theme) -> str:
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    first = pattern.search(line)
    if first is None:
        return escape(line.strip())
    start = max(0, first.start() - 70)
    end = min(len(line), max(first.end() + 100, start + 170))
    snippet = line[start:end]
    prefix = "…" if start else ""
    suffix = "…" if end < len(line) else ""
    parts: list[str] = [escape(prefix)]
    cursor = 0
    for match in pattern.finditer(snippet):
        parts.append(escape(snippet[cursor:match.start()]))
        parts.append(
            f"<span style='background-color:{theme.accent_soft};"
            f"color:{theme.text};font-weight:600;'>"
            f"{escape(match.group(0))}</span>"
        )
        cursor = match.end()
    parts.append(escape(snippet[cursor:]))
    parts.append(escape(suffix))
    return "".join(parts)
