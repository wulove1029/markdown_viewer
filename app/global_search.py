"""Background full-text search across Markdown document libraries."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
import re
from threading import Event
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .theme import LIGHT, Theme, collection_stylesheet

_PATH_ROLE = Qt.ItemDataRole.UserRole
_QUERY_ROLE = Qt.ItemDataRole.UserRole.value + 1
_LINE_ROLE = Qt.ItemDataRole.UserRole.value + 2
_HEADER_ROLE = Qt.ItemDataRole.UserRole.value + 3

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".obsidian",
}


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


def search_markdown_files(
    roots: Iterable[str | Path],
    query: str,
    should_cancel: Callable[[], bool] | None = None,
) -> list[FileSearchResult]:
    """Search ``.md`` files below *roots* using case-insensitive literal matching.

    Files are decoded as UTF-8 with replacement enabled. A replacement marker
    indicates malformed input, so that file is skipped along with unreadable files.
    """

    needle = query.strip()
    if not needle:
        return []
    cancelled = should_cancel or (lambda: False)
    pattern = re.compile(re.escape(needle), re.IGNORECASE)
    results: list[FileSearchResult] = []
    seen_files: set[str] = set()

    for root in _unique_roots(roots):
        if cancelled():
            return []
        try:
            is_directory = root.is_dir()
        except OSError:
            continue
        if not is_directory:
            continue

        for dirpath, dirnames, filenames in os.walk(
            root, onerror=lambda _error: None
        ):
            if cancelled():
                return []
            dirnames[:] = [
                name
                for name in dirnames
                if not name.startswith(".") and name not in _SKIP_DIRS
            ]
            for filename in filenames:
                if cancelled():
                    return []
                if Path(filename).suffix.lower() != ".md":
                    continue
                path = Path(dirpath) / filename
                key = _path_key(path)
                if key in seen_files:
                    continue
                seen_files.add(key)
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "\ufffd" in text:
                    continue

                hits: list[SearchHit] = []
                for line_number, line in enumerate(text.splitlines(), start=1):
                    spans = tuple(
                        (match.start(), match.end()) for match in pattern.finditer(line)
                    )
                    if spans:
                        hits.append(SearchHit(line_number, line, spans))
                if hits:
                    results.append(FileSearchResult(path, tuple(hits)))

    return sorted(results, key=lambda result: str(result.path).casefold())


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


class _SearchSignals(QObject):
    finished = Signal(int, str, object)


class _SearchTask(QRunnable):
    def __init__(
        self,
        request_id: int,
        roots: list[Path],
        query: str,
        cancel_event: Event,
    ):
        super().__init__()
        self.request_id = request_id
        self.roots = roots
        self.query = query
        self.cancel_event = cancel_event
        self.signals = _SearchSignals()

    @Slot()
    def run(self):
        results = search_markdown_files(
            self.roots, self.query, self.cancel_event.is_set
        )
        self.signals.finished.emit(self.request_id, self.query, results)


class GlobalSearchView(QWidget):
    """Debounced background search UI for the left workspace panel."""

    def __init__(
        self,
        roots_provider: Callable[[], Iterable[str | Path]],
        on_result_selected: Callable[[str, str, int], None],
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("globalSearch")
        self._roots_provider = roots_provider
        self._on_result_selected = on_result_selected
        self._theme = LIGHT
        self._request_id = 0
        self._cancel_event: Event | None = None
        self._results: list[FileSearchResult] = []
        self._active_query = ""
        self._tasks: dict[int, _SearchTask] = {}
        self._pool = QThreadPool.globalInstance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("搜尋所有文件庫內容")
        self._input.setClearButtonEnabled(True)
        layout.addWidget(self._input)

        self._list = QListWidget()
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, stretch=1)

        self._status = QLabel("輸入關鍵字開始搜尋")
        self._status.setProperty("muted", True)
        layout.addWidget(self._status)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._start_search)
        self._input.textChanged.connect(self._on_text_changed)
        self._input.returnPressed.connect(self._search_now)

        self.apply_theme(LIGHT)

    def focus_input(self):
        self._input.setFocus()
        self._input.selectAll()

    def apply_theme(self, theme: Theme):
        self._theme = theme
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
        if self._results:
            self._render_results(self._active_query, self._results)

    def _on_text_changed(self, text: str):
        self._request_id += 1
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._debounce.stop()
        if not text.strip():
            self._active_query = ""
            self._results = []
            self._list.clear()
            self._status.setText("輸入關鍵字開始搜尋")
            return
        self._status.setText("等待搜尋…")
        self._debounce.start()

    def _search_now(self):
        self._debounce.stop()
        self._start_search()

    def _start_search(self):
        query = self._input.text().strip()
        if not query:
            return
        self._request_id += 1
        request_id = self._request_id
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._cancel_event = Event()
        try:
            roots = [Path(root) for root in self._roots_provider()]
        except Exception:
            roots = []
        self._list.clear()
        self._status.setText("正在搜尋…")
        task = _SearchTask(request_id, roots, query, self._cancel_event)
        self._tasks[request_id] = task
        task.signals.finished.connect(self._on_search_finished)
        self._pool.start(task)

    def _on_search_finished(
        self,
        request_id: int,
        query: str,
        results: list[FileSearchResult],
    ):
        self._tasks.pop(request_id, None)
        if request_id != self._request_id or query != self._input.text().strip():
            return
        self._active_query = query
        self._results = results
        self._render_results(query, results)

    def _render_results(self, query: str, results: list[FileSearchResult]):
        self._list.clear()
        if not results:
            item = QListWidgetItem("找不到符合的內容")
            item.setForeground(QColor(self._theme.text_subtle))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            self._status.setText("找不到符合的內容")
            return

        total = 0
        for result in results:
            total += result.match_count
            header = QListWidgetItem(f"{result.path.name}（{result.match_count} 筆）")
            font = QFont()
            font.setBold(True)
            header.setFont(font)
            header.setForeground(QColor(self._theme.text_muted))
            header.setToolTip(str(result.path))
            header.setData(_HEADER_ROLE, True)
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(header)

            for hit in result.hits:
                item = QListWidgetItem()
                item.setSizeHint(QSize(0, 42))
                item.setToolTip(f"{result.path}:{hit.line_number}")
                item.setData(_PATH_ROLE, str(result.path))
                item.setData(_QUERY_ROLE, query)
                item.setData(_LINE_ROLE, hit.line_number)
                self._list.addItem(item)

                label = QLabel(
                    f"<span style='color:{self._theme.text_subtle};'>"
                    f"{hit.line_number}:</span> "
                    f"{_highlighted_snippet(hit.line, query, self._theme)}"
                )
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setWordWrap(True)
                label.setStyleSheet("background: transparent; padding: 3px 8px;")
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                self._list.setItemWidget(item, label)

        self._status.setText(f"共 {total} 筆，{len(results)} 個檔案")

    def _on_item_clicked(self, item: QListWidgetItem):
        path = item.data(_PATH_ROLE)
        query = item.data(_QUERY_ROLE)
        line_number = item.data(_LINE_ROLE)
        if path and query and line_number:
            self._on_result_selected(path, query, int(line_number))


def _highlighted_snippet(line: str, query: str, theme: Theme) -> str:
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    matches = list(pattern.finditer(line))
    if not matches:
        return escape(line.strip())

    first = matches[0]
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
