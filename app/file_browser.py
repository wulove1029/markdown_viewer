"""Document library browser: a folder tree with file CRUD."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QObject,
    QPointF,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

from . import file_ops
from .atomic_io import set_hidden
from .document_libraries import (
    DocumentLibrary,
    DocumentLibraryStore,
    load_excluded_folders,
    should_skip_directory,
    discover_cloud_library_paths,
)
from .file_types import SUPPORTED_EXTENSIONS, is_pdf
from .theme import LIGHT, Theme, collection_stylesheet, svg_icon

_PATH_ROLE = Qt.ItemDataRole.UserRole
_LIBRARY_ROLE = Qt.ItemDataRole.UserRole.value + 1
_IS_DIR_ROLE = Qt.ItemDataRole.UserRole.value + 2
# Sorted list[str] of the tags assigned to a file row; drives the tag pills.
_TAGS_ROLE = Qt.ItemDataRole.UserRole.value + 3
# Theme-dependent explicit text tone used by placeholder/missing-source rows.
_TEXT_TONE_ROLE = Qt.ItemDataRole.UserRole.value + 4
_TEXT_TONE_MUTED = "muted"
_TEXT_TONE_SUBTLE = "subtle"

# Sidecar files the app maintains next to documents. They never appear in the
# tree (their suffix isn't in SUPPORTED_EXTENSIONS); we just tag them hidden on
# Windows so Explorer's default view stays clean.
_HIDDEN_SIDECAR_SUFFIXES = (".notes.json", ".highlights.json", ".bak")

# Second-line tag-pill geometry (px). English comments per project rules.
_PILL_HEIGHT = 16       # pill badge height
_PILL_HPAD = 6          # horizontal padding inside a pill (each side)
_PILL_HGAP = 4          # horizontal gap between adjacent pills
_PILL_LINE_GAP = 3      # vertical gap between the filename line and the pills
_ITEM_HMARGIN = 3       # left/right content margin inside the item rect

# File-tree geometry.  The branch lane is deliberately wider than the 16 px
# glyphs, so several nested levels remain readable instead of collapsing into
# a stack of nearly touching icons.
_TREE_INDENTATION = 18
_TREE_ROOT_HEIGHT = 34


def _relative_luminance(color: QColor) -> float:
    """WCAG relative luminance of a color, in [0, 1]."""

    def _lin(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * _lin(color.red())
        + 0.7152 * _lin(color.green())
        + 0.0722 * _lin(color.blue())
    )


def _pill_text_color(fill) -> QColor:
    """Dark text on light fills, white text on dark fills (contrast-aware).

    ``fill`` may be a hex string or a ``QColor``. Yellow-ish pills such as
    ``#F5B70A`` land above the threshold and therefore get dark text.
    """
    color = fill if isinstance(fill, QColor) else QColor(fill)
    if not color.isValid():
        color = QColor("#888888")
    return QColor("#1a1a1a") if _relative_luminance(color) > 0.5 else QColor("#ffffff")

# Custom drag mime carrying newline-joined absolute file paths.
_MIME_PATHS = "application/x-mdv-paths"


def _path_key(path) -> str:
    return str(Path(path)).casefold()


def _resolve_key(path) -> str:
    """Resolved, case-folded key matching TagIndex's storage keys."""
    try:
        return str(Path(path).resolve()).casefold()
    except OSError:
        return _path_key(path)


class _LibraryTree(QTreeWidget):
    """Library tree with modern hierarchy guides and draggable file rows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._branch_line_color = QColor(LIGHT.border)
        self._branch_chevron_color = QColor(LIGHT.text_muted)
        self._branch_active_color = QColor(LIGHT.accent)

    def set_branch_theme(self, theme: Theme) -> None:
        """Refresh guide and chevron colors without replacing tree items."""
        self._branch_line_color = QColor(theme.border)
        self._branch_chevron_color = QColor(theme.text_muted)
        self._branch_active_color = QColor(theme.accent)
        self.viewport().update()

    def drawBranches(self, painter, rect, index):  # noqa: N802 (Qt override)
        """Draw compact chevrons plus continuous ancestor guide lines.

        Qt's platform branch primitive varies considerably and, on Windows,
        can become almost invisible at the narrow indentation used by this
        sidebar.  The view already supplies one indentation lane per ancestor;
        painting those lanes directly keeps the hierarchy consistent across
        themes and display scaling.
        """
        if not index.isValid() or rect.width() <= 0 or rect.height() <= 0:
            return

        indentation = max(1, self.indentation())
        lane_count = max(1, int(round(rect.width() / indentation)))
        depth = lane_count - 1
        top = float(rect.top())
        bottom = float(rect.bottom() + 1)
        tagged_file = bool(index.data(_TAGS_ROLE)) and not index.data(_IS_DIR_ROLE)
        pill_line_height = _PILL_HEIGHT + _PILL_LINE_GAP * 2
        first_line_height = (
            max(1, rect.height() - pill_line_height)
            if tagged_file
            else rect.height()
        )
        mid_y = float(rect.top() + first_line_height / 2)
        right_to_left = self.layoutDirection() == Qt.LayoutDirection.RightToLeft

        def lane_x(level: int) -> float:
            if right_to_left:
                return float(rect.right() - level * indentation - indentation / 2)
            return float(rect.left() + level * indentation + indentation / 2)

        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Every visible descendant continues its ancestors' vertical guides.
        painter.setPen(QPen(self._branch_line_color, 1.0))
        for level in range(depth):
            x = lane_x(level)
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))

        current_x = lane_x(depth)
        if depth > 0:
            parent_x = lane_x(depth - 1)
            content_edge = float(rect.left() if right_to_left else rect.right())
            painter.drawLine(
                QPointF(parent_x, mid_y),
                QPointF(content_edge, mid_y),
            )

        has_children = self.model().hasChildren(index)
        is_open = has_children and self.isExpanded(index)
        if is_open:
            # Descendant rows pick this line up at their top edge.
            painter.drawLine(
                QPointF(current_x, mid_y), QPointF(current_x, bottom)
            )

        if has_children:
            color = (
                self._branch_active_color
                if self.selectionModel().isSelected(index)
                else self._branch_chevron_color
            )
            painter.setPen(
                QPen(
                    color,
                    1.8,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            if is_open:
                painter.drawLine(
                    QPointF(current_x - 3.2, mid_y - 1.4),
                    QPointF(current_x, mid_y + 1.8),
                )
                painter.drawLine(
                    QPointF(current_x, mid_y + 1.8),
                    QPointF(current_x + 3.2, mid_y - 1.4),
                )
            else:
                painter.drawLine(
                    QPointF(current_x - 1.4, mid_y - 3.2),
                    QPointF(current_x + 1.8, mid_y),
                )
                painter.drawLine(
                    QPointF(current_x + 1.8, mid_y),
                    QPointF(current_x - 1.4, mid_y + 3.2),
                )

        painter.restore()

    def mimeData(self, items):  # noqa: N802 (Qt override)
        paths: list[str] = []
        for item in items:
            if item is None or item.data(0, _IS_DIR_ROLE):
                continue
            raw = item.data(0, _PATH_ROLE)
            if raw:
                paths.append(str(Path(raw).resolve()))
        data = QMimeData()
        if paths:
            joined = "\n".join(paths)
            data.setData(_MIME_PATHS, QByteArray(joined.encode("utf-8")))
            data.setUrls([QUrl.fromLocalFile(p) for p in paths])
        return data


class _TagPillDelegate(QStyledItemDelegate):
    """Renders a tagged file row as a two-line card.

    Line 1 is the icon + filename (top-aligned); line 2 is a single row of
    rounded, named pill badges (one per tag) indented under the filename text,
    with contrast-aware text and a ``+N`` overflow badge when they don't fit.
    Untagged rows fall back to the default painting so they look unchanged.
    """

    def __init__(self, color_for=None, parent=None):
        super().__init__(parent)
        self._color_for = color_for
        self._text_color = None

    def set_text_color(self, color):
        """Set the filename text color (theme.text). Paired with the theme's
        subtle selection tint so selected tagged rows stay readable."""
        self._text_color = QColor(color) if color else None

    # ---- helpers -----------------------------------------------------
    def _tags(self, index) -> list[str]:
        """Return the tag list for a file row that should show pills.

        Returns ``[]`` (i.e. "render as a plain row") for folders, for rows
        without tags, or when no color callback was supplied.
        """
        if self._color_for is None or index.data(_IS_DIR_ROLE):
            return []
        tags = index.data(_TAGS_ROLE)
        return list(tags) if tags else []

    def _pill_font(self, base_font: QFont) -> QFont:
        pill_font = QFont(base_font)
        point = pill_font.pointSize()
        if point > 0:
            pill_font.setPointSize(max(6, point - 1))
        else:
            pixel = pill_font.pixelSize()
            if pixel > 0:
                pill_font.setPixelSize(max(8, pixel - 2))
        return pill_font

    # ---- Qt overrides ------------------------------------------------
    def sizeHint(self, option, index):  # noqa: N802 (Qt override)
        base = super().sizeHint(option, index)
        if index.data(_LIBRARY_ROLE):
            base.setHeight(max(base.height(), _TREE_ROOT_HEIGHT))
        if not self._tags(index):
            return base
        # Single pill line => constant extra height regardless of width, so
        # sizeHint and paint never disagree on row height.
        extra = _PILL_HEIGHT + _PILL_LINE_GAP * 2
        return QSize(base.width(), base.height() + extra)

    def paint(self, painter, option, index):  # noqa: N802 (Qt override)
        tags = self._tags(index)
        if not tags:
            # Untagged rows / folders: exactly the default look.
            super().paint(painter, option, index)
            return

        style = option.widget.style() if option.widget else QApplication.style()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        filename = opt.text

        # 1) Draw background / selection / hover over the FULL (tall) rect,
        #    suppressing the style's own text + icon so we can top-align them.
        opt.text = ""
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        rect = option.rect
        row1_h = super().sizeHint(option, index).height()
        fm = option.fontMetrics

        # 2) Optional decoration icon, vertically centered within line 1.
        deco_w = 0
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon) and not icon.isNull():
            isz = option.decorationSize
            iy = rect.top() + max(0, (row1_h - isz.height()) // 2)
            icon.paint(
                painter,
                QRect(rect.left() + _ITEM_HMARGIN, iy, isz.width(), isz.height()),
                Qt.AlignmentFlag.AlignCenter,
            )
            deco_w = isz.width() + 4

        text_x = rect.left() + _ITEM_HMARGIN + deco_w

        # 3) Filename on line 1 (top-aligned, not centered in the taller rect).
        # Filename uses the theme's normal text color even when selected. The
        # theme pairs its subtle selection tint (surface_active) with normal
        # text (see collection_stylesheet); forcing palette.highlightedText()
        # (often white) made selected tagged rows unreadable in the light theme.
        pen_color = self._text_color or opt.palette.text().color()
        painter.save()
        painter.setPen(pen_color)
        painter.setFont(option.font)
        text_rect = QRect(
            text_x, rect.top(), rect.right() - text_x - _ITEM_HMARGIN, row1_h
        )
        elided = fm.elidedText(
            filename, Qt.TextElideMode.ElideRight, max(0, text_rect.width())
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elided,
        )
        painter.restore()

        # 4) Pills on line 2, indented under the filename text.
        self._paint_pills(painter, rect, text_x, row1_h, tags, option.font)

    # ---- pill row ----------------------------------------------------
    def _paint_pills(self, painter, rect, x0, row1_h, tags, base_font):
        pill_font = self._pill_font(base_font)
        pill_fm = QFontMetrics(pill_font)
        y = rect.top() + row1_h + _PILL_LINE_GAP
        x = x0
        right_limit = rect.right() - _ITEM_HMARGIN
        radius = _PILL_HEIGHT / 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(pill_font)

        total = len(tags)
        drawn = 0
        for tag in tags:
            label = tag.lstrip("#")
            pill_w = pill_fm.horizontalAdvance(label) + _PILL_HPAD * 2
            # Stop before overflowing the right edge (nothing clipped mid-pill).
            if x + pill_w > right_limit and drawn > 0:
                break
            if x + pill_w > right_limit and drawn == 0:
                # Even the first pill can't fit: fall through to "+N" only.
                break
            try:
                fill = QColor(self._color_for(tag))
            except (TypeError, ValueError):
                fill = QColor("#888888")
            if not fill.isValid():
                fill = QColor("#888888")
            pill_rect = QRectF(x, y, pill_w, _PILL_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(pill_rect, radius, radius)
            painter.setPen(_pill_text_color(fill))
            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, label)
            x += pill_w + _PILL_HGAP
            drawn += 1

        if drawn < total:
            hidden = total - drawn
            plus = f"+{hidden}"
            plus_w = pill_fm.horizontalAdvance(plus) + _PILL_HPAD * 2
            plus_rect = QRectF(x, y, plus_w, _PILL_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#888888"))
            painter.drawRoundedRect(plus_rect, radius, radius)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(plus_rect, Qt.AlignmentFlag.AlignCenter, plus)

        painter.restore()


def _is_same_or_descendant(path: str | Path, folder: str | Path) -> bool:
    path_obj = Path(path)
    folder_key = _path_key(folder)
    return _path_key(path_obj) == folder_key or any(
        _path_key(parent) == folder_key for parent in path_obj.parents
    )


# ---------------- library scanning (pure, no Qt widgets) ----------------
# The disk walk is split from tree building so it can run on a worker thread:
# a large library on a slow (USB / network / cold-cache) drive used to block
# the UI thread for seconds before the main window could even appear.


@dataclass(slots=True)
class _ScanNode:
    """One row of the scanned tree, handed from the scan to the UI thread."""

    name: str
    path: str
    is_dir: bool
    tags: list[str] = field(default_factory=list)
    children: list["_ScanNode"] = field(default_factory=list)


@dataclass(slots=True)
class _LibraryScan:
    """Scan outcome for one library root."""

    library: DocumentLibrary
    exists: bool
    children: list[_ScanNode]
    count: int


@dataclass(slots=True)
class _ScanRequest:
    """Everything a scan needs, captured on the UI thread when it starts."""

    libraries: list[DocumentLibrary]
    query: str
    allowed: set[str] | None
    excluded: list[str]
    transient: set[str]
    tags_for: Callable[[Path], list[str]]
    filtering: bool
    active_tag: str


class _ScanCancelled(Exception):
    """Raised inside a scan when a newer scan has superseded it."""


class _ScanToken:
    """Cancellation flag shared between the UI thread and one scan job.

    A token may chain to a *parent* token (the view's lifetime token) so one
    ``cancel()`` when the widget goes away stops whatever scan is running.
    """

    __slots__ = ("_cancelled", "_parent")

    def __init__(self, parent: "_ScanToken | None" = None):
        self._cancelled = False
        self._parent = parent

    @property
    def cancelled(self) -> bool:
        if self._cancelled:
            return True
        return self._parent is not None and self._parent.cancelled

    def cancel(self):
        self._cancelled = True


def _sorted_entries(folder: str) -> list[os.DirEntry] | None:
    try:
        with os.scandir(folder) as it:
            return sorted(it, key=lambda e: e.name.casefold())
    except OSError:
        return None


def _entry_is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def _entry_is_file(entry: os.DirEntry) -> bool:
    try:
        return entry.is_file()
    except OSError:
        return False


def _scan_folder(
    folder: str,
    request: _ScanRequest,
    token: _ScanToken | None,
    ancestor_match: bool = False,
    relative: str = "",
) -> tuple[list[_ScanNode], int]:
    """Walk *folder* and return (child nodes, supported-file count).

    Mirrors the pruning rules of the tree: folders are listed first, sorted
    case-insensitively, and dropped when they hold no supported file (unless
    a transient folder created this session lives inside); then the files.
    ``os.scandir`` supplies the entry type from the directory listing itself,
    so no per-entry ``stat`` round-trip is needed on Windows.
    """
    if token is not None and token.cancelled:
        raise _ScanCancelled
    entries = _sorted_entries(folder)
    if entries is None:
        return [], 0
    query = request.query
    allowed = request.allowed
    nodes: list[_ScanNode] = []
    count = 0
    for entry in entries:
        if not _entry_is_dir(entry):
            continue
        rel = f"{relative}/{entry.name}" if relative else entry.name
        if should_skip_directory(rel, request.excluded):
            continue
        child_match = ancestor_match or bool(query and query in entry.name.casefold())
        sub_nodes, sub_count = _scan_folder(
            entry.path, request, token, child_match, rel
        )
        keep_transient = not request.filtering and any(
            _is_same_or_descendant(path, entry.path) for path in request.transient
        )
        if sub_count == 0 and not keep_transient:
            continue
        nodes.append(_ScanNode(entry.name, entry.path, True, [], sub_nodes))
        count += sub_count
    for entry in entries:
        if not _entry_is_file(entry):
            continue
        name = entry.name
        if name.lower().endswith(_HIDDEN_SIDECAR_SUFFIXES):
            # Keep pre-existing sidecars out of Explorer's default view.
            # Windows-only, best-effort; these never enter the tree.
            set_hidden(entry.path)
            continue
        if os.path.splitext(name)[1].lower() not in SUPPORTED_EXTENSIONS:
            continue
        if allowed is not None:
            if str(Path(entry.path).resolve()).casefold() not in allowed:
                continue
        if query and not ancestor_match and query not in name.casefold():
            continue
        try:
            tags = list(request.tags_for(Path(entry.path)))
        except Exception:
            # The tag index lives on the UI thread and may be edited while a
            # background scan reads it; a stale or missing pill is harmless,
            # a failed scan is not.
            tags = []
        nodes.append(_ScanNode(name, entry.path, False, tags))
        count += 1
    return nodes, count


def _scan_libraries(
    request: _ScanRequest, token: _ScanToken | None = None
) -> list[_LibraryScan]:
    results: list[_LibraryScan] = []
    for lib in request.libraries:
        root = Path(lib.path)
        if not root.exists() or not root.is_dir():
            results.append(_LibraryScan(lib, False, [], 0))
            continue
        children, count = _scan_folder(str(root), request, token)
        results.append(_LibraryScan(lib, True, children, count))
    return results


class _ScanSignals(QObject):
    # (generation, request, results-or-None)
    finished = Signal(int, object, object)


class _ScanJob(QRunnable):
    """Runs one library scan off the UI thread and reports back by signal.

    The signals object is owned by the job (not parented to the view) so a
    late-finishing scan cannot emit through a widget that has been destroyed.
    """

    def __init__(self, generation: int, request: _ScanRequest, token: _ScanToken):
        super().__init__()
        self.generation = generation
        self.request = request
        self.token = token
        self.signals = _ScanSignals()

    def run(self):
        try:
            results = _scan_libraries(self.request, self.token)
        except _ScanCancelled:
            return
        except Exception:  # pragma: no cover - defensive: never kill the pool
            results = None
        if self.token.cancelled:
            return
        self.signals.finished.emit(self.generation, self.request, results)


class FileBrowserView(QWidget):
    def __init__(
        self,
        on_file_selected,
        tag_index=None,
        on_manage_tags=None,
        on_add_tag: Callable[[list[Path]], None] | None = None,
        tag_color_for=None,
        parent=None,
        background_scan: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("fileBrowser")
        self._on_file_selected = on_file_selected
        self._tag_index = tag_index
        # background_scan=True walks the library folders on a worker thread so
        # a big / slow library never blocks the UI (the main window turns this
        # on); the default synchronous scan keeps the widget simple to test.
        self._background_scan = background_scan
        self._scan_pool = QThreadPool(self)
        self._scan_pool.setMaxThreadCount(1)
        self._scan_generation = 0
        self._scan_token: _ScanToken | None = None
        self._scan_inflight = False
        # Cancelled when the widget is destroyed so a scan still running does
        # not hold up teardown (the pool waits for its jobs on destruction).
        self._lifetime_token = _ScanToken()
        _token = self._lifetime_token
        self.destroyed.connect(lambda *_: _token.cancel())
        # Requests that arrived while a scan was running; replayed once the
        # tree for that scan has been built.
        self._pending_tree_state: dict | None = None
        self._pending_navigate: str | None = None
        self._pending_select: Path | None = None
        # (icon name, color) -> QIcon; the same folder/file glyph is reused for
        # every row instead of re-rendering the SVG per item.
        self._icon_cache: dict[tuple[str, str], QIcon] = {}
        # on_manage_tags(paths: list[Path]) opens the manage-tags dialog.
        self._on_manage_tags = on_manage_tags
        # on_add_tag(paths: list[Path]) quick-assigns one tag to the files.
        self._on_add_tag = on_add_tag
        # tag_color_for(tag: str) -> hex color for the tag pills.
        self._tag_color_for = tag_color_for
        # Cache of resolved-path-key -> sorted tags, rebuilt on each refresh.
        self._path_tags: dict[str, list[str]] = {}
        self._active_tag = ""
        self._theme = LIGHT
        self._store = DocumentLibraryStore()
        self._libraries: list[DocumentLibrary] = []
        # Expanded directory paths (raw strings). None = never restored, so
        # the first build expands the library roots by default.
        self._expanded: set[str] | None = None
        self._last_filtering = False
        self._built = False
        # Empty folders created from this view stay reachable for the current
        # session, so users can immediately create a note inside them.
        self._transient_folders: set[str] = set()
        # Optional hooks the main window installs so tabs / recents / session
        # state follow filesystem changes made here.
        self.on_note_created = None    # callable(path_str)
        self.on_new_note_requested = None  # callable(folder_str)
        self.on_paths_migrated = None  # callable({old: new})
        self.on_paths_deleted = None   # callable([path_str, ...])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(4)

        self._add_btn = QPushButton("加入文件庫")
        self._add_btn.setObjectName("addLibraryButton")
        self._add_btn.setToolTip("新增文件庫資料夾")
        self._add_btn.setAccessibleName("新增文件庫資料夾")
        self._add_btn.setIconSize(QSize(18, 18))
        self._add_btn.clicked.connect(self._add_library)

        self._refresh_btn = QPushButton()
        self._refresh_btn.setToolTip("重新掃描文件庫")
        self._refresh_btn.setAccessibleName("重新掃描文件庫")
        self._refresh_btn.setIconSize(QSize(18, 18))
        self._refresh_btn.clicked.connect(self.refresh_libraries)

        self._manage_btn = QPushButton("管理")
        self._manage_btn.setToolTip("管理文件庫來源")
        self._manage_btn.clicked.connect(self._manage_libraries)

        action_row.addWidget(self._add_btn)
        action_row.addWidget(self._refresh_btn)
        action_row.addStretch()
        action_row.addWidget(self._manage_btn)
        layout.addLayout(action_row)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("搜尋文件庫中的文件")
        self._filter.textChanged.connect(self._refresh_list)
        layout.addWidget(self._filter)

        self._tree = _LibraryTree()
        self._tree.setObjectName("libraryTree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(_TREE_INDENTATION)
        self._tree.setRootIsDecorated(True)
        self._tree.setMouseTracking(True)
        # Fixed decoration size so folder/file icons render crisply and the
        # tag-pill delegate can rely on a deterministic option.decorationSize.
        self._tree.setIconSize(QSize(16, 16))
        self._tree.itemClicked.connect(self._on_clicked)
        self._tree.itemExpanded.connect(self._on_folder_expansion_changed)
        self._tree.itemCollapsed.connect(self._on_folder_expansion_changed)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._show_context_menu)
        # Drag source: file rows export their paths as a custom mime.
        self._tree.setDragEnabled(True)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        # Variable row heights: tagged rows are taller (they gain a pill line),
        # so uniform row heights must stay off for the pills to fit.
        self._tree.setUniformRowHeights(False)
        self._tag_delegate = _TagPillDelegate(self._tag_color_for, parent=self._tree)
        self._tree.setItemDelegate(self._tag_delegate)
        layout.addWidget(self._tree, stretch=1)

        self._status = QLabel()
        self._status.setProperty("muted", True)
        layout.addWidget(self._status)

        self.apply_theme(LIGHT)
        self.refresh_libraries()

    def apply_theme(self, theme: Theme):
        theme_changed = theme != self._theme
        self._theme = theme
        if hasattr(self, "_tag_delegate"):
            self._tag_delegate.set_text_color(theme.text)
        self._add_btn.setIcon(svg_icon("folder-plus", theme.accent, 18))
        self._refresh_btn.setIcon(svg_icon("refresh", theme.text_muted, 18))
        self.setStyleSheet(self._stylesheet(theme))
        self._tree.set_branch_theme(theme)
        self._tree.setStyleSheet(self._tree_stylesheet(theme))
        # Icons and a few placeholder brushes bake in theme colors. Recolor
        # existing rows in place so applying a theme never rescans the document
        # folders or replaces items (preserving selection and expansion).
        if theme_changed:
            self._update_tree_item_theme()

    # ---------------- row theme ----------------
    def _update_tree_item_theme(self) -> None:
        """Refresh baked-in row colors without rebuilding the folder tree."""
        library_icon = self._library_icon()
        markdown_icon = svg_icon("file-text", self._theme.text_muted, 16)
        pdf_icon = svg_icon("file-text", self._theme.danger, 16)

        iterator = QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(0, _PATH_ROLE)
            if item.data(0, _IS_DIR_ROLE):
                if item.data(0, _LIBRARY_ROLE):
                    item.setIcon(0, library_icon)
                else:
                    item.setIcon(0, self._folder_icon(item.isExpanded()))
            elif path:
                item.setIcon(0, pdf_icon if is_pdf(path) else markdown_icon)
            elif not item.icon(0).isNull():
                # The disabled child of a missing library has a folder icon but
                # intentionally carries no path or directory role.
                item.setIcon(0, self._folder_icon(False))
            tone = item.data(0, _TEXT_TONE_ROLE)
            if tone == _TEXT_TONE_MUTED:
                item.setForeground(0, QColor(self._theme.text_muted))
            elif tone == _TEXT_TONE_SUBTLE:
                item.setForeground(0, QColor(self._theme.text_subtle))
            iterator += 1

    def _cached_icon(self, name: str, color: str) -> QIcon:
        key = (name, color)
        icon = self._icon_cache.get(key)
        if icon is None:
            icon = svg_icon(name, color, 16)
            self._icon_cache[key] = icon
        return icon

    def _library_icon(self) -> QIcon:
        """Book-like root glyph that distinguishes a library from a folder."""
        return self._cached_icon("library", self._theme.accent)

    def _folder_icon(self, expanded: bool = False) -> QIcon:
        """Accent folder glyph reflecting its current expansion state."""
        name = "folder-open" if expanded else "folder"
        return self._cached_icon(name, self._theme.accent)

    def _on_folder_expansion_changed(self, item: QTreeWidgetItem) -> None:
        if not item.data(0, _IS_DIR_ROLE):
            return
        if item.data(0, _LIBRARY_ROLE):
            item.setIcon(0, self._library_icon())
        else:
            item.setIcon(0, self._folder_icon(item.isExpanded()))

    def _file_icon(self, path) -> QIcon:
        """Document glyph: neutral for Markdown, red for PDF (color-coded)."""
        color = self._theme.danger if is_pdf(path) else self._theme.text_muted
        return self._cached_icon("file-text", color)

    def is_scanning(self) -> bool:
        """True while a background library scan has not yet built the tree."""
        return self._scan_inflight

    def navigate_to(self, folder: str | Path):
        if self._scan_inflight:
            # The tree is being rebuilt; expand once the new rows exist.
            self._pending_navigate = str(folder)
            return
        item = self._find_item(folder)
        while item is not None:
            item.setExpanded(True)
            item = item.parent()

    def select_path(self, filepath: str | Path):
        self._select_path(Path(filepath))

    def selected_directory(self) -> Path | None:
        """Directory of the current tree selection (a file row -> its parent)."""
        item = self._tree.currentItem()
        if item is None:
            return None
        raw = item.data(0, _PATH_ROLE)
        if not raw:
            return None
        path = Path(str(raw))
        if item.data(0, _IS_DIR_ROLE):
            return path
        return path.parent

    def library_roots(self) -> list[Path]:
        """Existing library root folders, in configured order."""
        return [
            Path(lib.path) for lib in self._libraries if Path(lib.path).is_dir()
        ]

    def reveal_created_note(self, path: str | Path) -> None:
        """Refresh + select a note created outside the tree's own actions."""
        path = Path(path)
        self._remember_expanded(str(path.parent))
        self.refresh_libraries()
        self._select_path(path)

    def set_tag_filter(self, tag: str):
        self._active_tag = tag or ""
        self._refresh_list()

    def has_open_folder(self) -> bool:
        return any(Path(lib.path).is_dir() for lib in self._libraries)

    def refresh_libraries(self):
        self._libraries = self._store.load()
        self._refresh_list()

    def update_file_tags(self, paths) -> None:
        """Incrementally refresh the tag pills of the given file rows.

        A tag change never alters the folder structure, so we skip the full,
        disk-rescanning ``refresh_libraries`` and only re-read each affected
        row's tags. Rows not currently in the tree (filtered out, or inside a
        collapsed/unbuilt subtree) are simply skipped -- they render correctly
        the next time they are built. Gaining or losing tags flips a row
        between one and two lines, so a layout pass is scheduled to let the
        pill delegate re-measure the row heights and repaint.
        """
        touched = False
        for path in paths:
            item = self._find_item(path)
            if item is None or item.data(0, _IS_DIR_ROLE):
                continue
            item.setData(0, _TAGS_ROLE, self._tags_for_path(Path(path)))
            touched = True
        if touched:
            self._tree.scheduleDelayedItemsLayout()

    # ---------------- expanded / selected state ----------------
    def tree_state(self) -> dict:
        """Snapshot of expanded folders + selected path for the session."""
        self._sync_expanded_from_tree()
        current = self._tree.currentItem()
        selected = current.data(0, _PATH_ROLE) if current else ""
        return {
            "expanded": sorted(self._expanded or set()),
            "selected": selected or "",
        }

    def restore_tree_state(self, state: dict):
        expanded = state.get("expanded")
        if isinstance(expanded, list):
            # Record it right away so a session saved before the first scan
            # finishes still round-trips the expansion set.
            self._expanded = {str(p) for p in expanded}
        if self._scan_inflight:
            self._pending_tree_state = state
            return
        if isinstance(expanded, list):
            self._apply_expanded()
        selected = state.get("selected")
        if selected:
            self._select_path(Path(str(selected)))

    def _is_filtering(self) -> bool:
        return bool(self._filter.text().strip() or self._active_tag)

    def _sync_expanded_from_tree(self):
        if not self._built or self._last_filtering:
            return
        expanded = set()
        iterator = QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(0, _PATH_ROLE)
            if path and item.data(0, _IS_DIR_ROLE) and item.isExpanded():
                expanded.add(str(path))
            iterator += 1
        self._expanded = expanded

    def _apply_expanded(self):
        if self._expanded is None:
            # First run: show the library roots opened.
            for i in range(self._tree.topLevelItemCount()):
                self._tree.topLevelItem(i).setExpanded(True)
            return
        keys = {p.casefold() for p in self._expanded}
        iterator = QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            path = item.data(0, _PATH_ROLE)
            if path and item.data(0, _IS_DIR_ROLE):
                item.setExpanded(_path_key(path) in keys)
            iterator += 1

    # ---------------- tree building ----------------
    def _rebuild_tag_map(self):
        """Reverse-index resolved path -> tags for lightweight pill painting.

        Prefers a native ``tag_index.tags_for`` accessor when present; otherwise
        derives the mapping once per refresh from the public tag API.
        """
        self._path_tags = {}
        if self._tag_index is None or hasattr(self._tag_index, "tags_for"):
            return
        try:
            for tag in self._tag_index.all_tags():
                for path in self._tag_index.files_with_tag(tag):
                    self._path_tags.setdefault(str(path).casefold(), []).append(tag)
        except (OSError, AttributeError):
            self._path_tags = {}
            return
        for tags in self._path_tags.values():
            tags.sort()

    def _tags_for_path(self, path) -> list[str]:
        if self._tag_index is None:
            return []
        accessor = getattr(self._tag_index, "tags_for", None)
        if accessor is not None:
            try:
                return sorted(accessor(path))
            except (OSError, AttributeError):
                return []
        return self._path_tags.get(_resolve_key(path), [])

    def _refresh_list(self):
        self._sync_expanded_from_tree()
        self._rebuild_tag_map()
        self._excluded_folders = load_excluded_folders()
        self._transient_folders = {
            path for path in self._transient_folders if Path(path).is_dir()
        }
        query = self._filter.text().strip().casefold()
        filtering = self._is_filtering()

        # Any scan still running is now stale.
        self._scan_generation += 1
        if self._scan_token is not None:
            self._scan_token.cancel()
            self._scan_token = None

        if not self._libraries:
            self._scan_inflight = False
            self._tree.clear()
            self._filter.setEnabled(False)
            self._refresh_btn.setEnabled(False)
            if self._active_tag:
                self._add_empty_item("沒有符合標籤的檔案")
            else:
                self._add_empty_item("尚未加入文件庫\n按「加入文件庫」選擇資料夾")
            self._status.setText("尚未設定文件庫")
            self._built = True
            self._last_filtering = filtering
            self._replay_pending()
            return
        self._filter.setEnabled(True)
        self._refresh_btn.setEnabled(True)

        allowed = None
        if self._active_tag:
            allowed = set()
            if self._tag_index is not None:
                allowed = {
                    str(Path(path).resolve()).casefold()
                    for path in self._tag_index.files_with_tag(self._active_tag)
                }

        request = _ScanRequest(
            libraries=list(self._libraries),
            query=query,
            allowed=allowed,
            excluded=list(self._excluded_folders),
            transient=set(self._transient_folders),
            tags_for=self._tags_for_path,
            filtering=filtering,
            active_tag=self._active_tag,
        )
        generation = self._scan_generation
        if not self._background_scan:
            self._apply_scan(generation, request, _scan_libraries(request))
            return

        self._scan_inflight = True
        self._status.setText("正在掃描文件庫…")
        token = _ScanToken(self._lifetime_token)
        self._scan_token = token
        job = _ScanJob(generation, request, token)
        job.signals.finished.connect(
            self._on_scan_finished, Qt.ConnectionType.QueuedConnection
        )
        self._scan_pool.start(job)

    @Slot(int, object, object)
    def _on_scan_finished(self, generation: int, request, results):
        if generation != self._scan_generation:
            return  # superseded by a newer refresh
        self._scan_token = None
        if results is None:
            # The walk failed unexpectedly. Do not retry synchronously here --
            # that would freeze the UI on exactly the large libraries this
            # path exists for. Leave the current rows, tell the user, and let
            # the refresh button start a new background scan.
            self._scan_inflight = False
            self._status.setText("掃描文件庫失敗，請按「重新掃描」再試")
            self._replay_pending()
            return
        self._apply_scan(generation, request, results)

    def _apply_scan(self, generation: int, request: _ScanRequest, results):
        """Build the tree rows from a finished scan (UI thread only)."""
        if generation != self._scan_generation:
            return
        self._scan_inflight = False
        filtering = request.filtering
        query = request.query
        self._tree.setUpdatesEnabled(False)
        try:
            self._tree.clear()
            total_shown = 0
            missing_count = 0
            for scan in results:
                lib = scan.library
                root_item = QTreeWidgetItem([lib.name])
                font = QFont()
                font.setBold(True)
                root_item.setFont(0, font)
                root_item.setIcon(0, self._library_icon())
                root_item.setToolTip(0, lib.path)
                root_item.setData(0, _PATH_ROLE, lib.path)
                root_item.setData(0, _LIBRARY_ROLE, lib.id)
                root_item.setData(0, _IS_DIR_ROLE, True)

                if not scan.exists:
                    missing_count += 1
                    if not filtering:
                        root_item.setText(0, f"{lib.name}（找不到資料夾）")
                        root_item.setForeground(0, QColor(self._theme.text_muted))
                        root_item.setData(0, _TEXT_TONE_ROLE, _TEXT_TONE_MUTED)
                        missing = QTreeWidgetItem([lib.path])
                        missing.setIcon(0, self._folder_icon(False))
                        missing.setFlags(
                            missing.flags() & ~Qt.ItemFlag.ItemIsEnabled
                        )
                        root_item.addChild(missing)
                        self._tree.addTopLevelItem(root_item)
                    continue

                self._build_items(root_item, scan.children)
                count = scan.count
                total_shown += count
                if filtering and count == 0:
                    continue
                root_item.setText(0, f"{lib.name}（{count}）")
                self._tree.addTopLevelItem(root_item)

            if total_shown == 0:
                if request.active_tag:
                    self._add_empty_item("沒有符合標籤的檔案")
                elif query:
                    self._add_empty_item("沒有符合搜尋的文件")

            if filtering:
                self._tree.expandAll()
            else:
                self._apply_expanded()
        finally:
            self._tree.setUpdatesEnabled(True)

        missing_text = f"，{missing_count} 個來源找不到" if missing_count else ""
        self._status.setText(
            f"{len(request.libraries)} 個文件庫，{total_shown} 份文件{missing_text}"
        )
        self._built = True
        self._last_filtering = filtering
        self._replay_pending()

    def _build_items(self, parent_item: QTreeWidgetItem, nodes) -> None:
        """Turn scan nodes into tree rows under *parent_item* (recursive)."""
        folder_icon = self._folder_icon(False)
        folder_font = QFont()
        folder_font.setWeight(QFont.Weight.DemiBold)
        for node in nodes:
            child = QTreeWidgetItem([node.name])
            child.setToolTip(0, node.path)
            child.setData(0, _PATH_ROLE, node.path)
            child.setData(0, _IS_DIR_ROLE, node.is_dir)
            if node.is_dir:
                child.setIcon(0, folder_icon)
                child.setFont(0, folder_font)
                self._build_items(child, node.children)
            else:
                child.setIcon(0, self._file_icon(node.path))
                child.setData(0, _TAGS_ROLE, node.tags)
            parent_item.addChild(child)

    def _replay_pending(self) -> None:
        """Apply tree-state / navigate / select requests that arrived mid-scan."""
        state = self._pending_tree_state
        self._pending_tree_state = None
        navigate = self._pending_navigate
        self._pending_navigate = None
        select = self._pending_select
        self._pending_select = None
        if state is not None:
            self.restore_tree_state(state)
        if navigate is not None:
            self.navigate_to(navigate)
        if select is not None:
            self._select_path(select)

    def _add_empty_item(self, text: str):
        item = QTreeWidgetItem([text])
        item.setForeground(0, QColor(self._theme.text_subtle))
        item.setData(0, _TEXT_TONE_ROLE, _TEXT_TONE_SUBTLE)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self._tree.addTopLevelItem(item)

    def _find_item(self, path: str | Path) -> QTreeWidgetItem | None:
        key = _path_key(path)
        iterator = QTreeWidgetItemIterator(self._tree)
        while iterator.value():
            item = iterator.value()
            item_path = item.data(0, _PATH_ROLE)
            if item_path and _path_key(item_path) == key:
                return item
            iterator += 1
        return None

    def _select_path(self, path: Path):
        if self._scan_inflight:
            # The row may not exist yet; select it once the scan lands.
            self._pending_select = path
            return
        item = self._find_item(path)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)

    def _on_clicked(self, item: QTreeWidgetItem, _column: int = 0):
        if item.data(0, _IS_DIR_ROLE):
            return
        path = item.data(0, _PATH_ROLE)
        if path and Path(path).exists():
            self._on_file_selected(path)

    # ---------------- context menu ----------------
    def _selected_file_paths(self, fallback=None) -> list[Path]:
        """Absolute paths of the selected file rows (folders excluded).

        Falls back to ``[fallback]`` when the selection holds no file rows, so
        a right-click on a row that isn't part of the current selection still
        targets that row.
        """
        paths: list[Path] = []
        for item in self._tree.selectedItems():
            if item is None or item.data(0, _IS_DIR_ROLE):
                continue
            raw = item.data(0, _PATH_ROLE)
            if raw:
                paths.append(Path(raw))
        if not paths and fallback is not None:
            return [fallback]
        return paths

    def _show_context_menu(self, pos):
        item = self._tree.itemAt(pos)
        menu = self._build_context_menu(item)
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _build_context_menu(self, item) -> QMenu:
        """Build the right-click menu for *item* (None -> the empty-area menu).

        Split out from ``_show_context_menu`` so tests can inspect the menu
        without driving the modal popup; the actions themselves are unchanged.
        """
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        path = item.data(0, _PATH_ROLE) if item else None
        if path and item.data(0, _IS_DIR_ROLE):
            library_id = item.data(0, _LIBRARY_ROLE)
            folder_exists = Path(path).is_dir()

            new_note_act = QAction("新增筆記", self)
            new_note_act.setEnabled(folder_exists)
            new_note_act.triggered.connect(
                lambda _=False, p=path: self._request_new_note_action(p)
            )
            menu.addAction(new_note_act)

            new_folder_act = QAction("新增資料夾", self)
            new_folder_act.setEnabled(folder_exists)
            new_folder_act.triggered.connect(
                lambda _=False, p=path: self._create_folder_action(p)
            )
            menu.addAction(new_folder_act)

            rename_act = QAction("重新命名", self)
            if library_id:
                rename_act.triggered.connect(
                    lambda _=False, lid=library_id: self._rename_library_action(lid)
                )
            else:
                rename_act.setEnabled(folder_exists)
                rename_act.triggered.connect(
                    lambda _=False, p=path: self._rename_folder_action(p)
                )
            menu.addAction(rename_act)

            reveal_act = QAction("在檔案總管顯示", self)
            reveal_act.setEnabled(folder_exists)
            reveal_act.triggered.connect(
                lambda _=False, p=path: self._open_location(p)
            )
            menu.addAction(reveal_act)
            menu.addSeparator()
        elif path:
            open_act = QAction("開啟文件", self)
            open_act.triggered.connect(
                lambda _=False, p=path: self._on_file_selected(p)
            )
            menu.addAction(open_act)

            if self._on_add_tag is not None:
                add_tag_act = QAction("加入標籤…", self)
                add_tag_act.triggered.connect(
                    lambda _=False, p=path: self._on_add_tag(
                        self._selected_file_paths(fallback=Path(p))
                    )
                )
                menu.addAction(add_tag_act)

            if self._on_manage_tags is not None:
                manage_tags_act = QAction("管理標籤…", self)
                manage_tags_act.triggered.connect(
                    lambda _=False, p=path: self._on_manage_tags([Path(p)])
                )
                menu.addAction(manage_tags_act)

            rename_act = QAction("重新命名", self)
            rename_act.triggered.connect(
                lambda _=False, p=path: self._rename_file_action(p)
            )
            menu.addAction(rename_act)

            move_act = QAction("移動到…", self)
            move_act.triggered.connect(
                lambda _=False, p=path: self._move_file_action(p)
            )
            menu.addAction(move_act)

            delete_act = QAction("刪除", self)
            delete_act.triggered.connect(
                lambda _=False, p=path: self._delete_file_action(p)
            )
            menu.addAction(delete_act)

            reveal_act = QAction("在檔案總管顯示", self)
            reveal_act.triggered.connect(
                lambda _=False, p=path: self._open_location(p)
            )
            menu.addAction(reveal_act)
            menu.addSeparator()

        add_act = QAction("新增文件庫資料夾", self)
        add_act.triggered.connect(self._add_library)
        menu.addAction(add_act)

        refresh_act = QAction("重新掃描文件庫", self)
        refresh_act.triggered.connect(self.refresh_libraries)
        menu.addAction(refresh_act)

        manage_act = QAction("管理文件庫", self)
        manage_act.triggered.connect(self._manage_libraries)
        menu.addAction(manage_act)
        return menu

    # ---------------- CRUD actions ----------------
    def _request_new_note_action(self, folder: str) -> None:
        """Use the host's full new-note flow when available.

        The main window installs this hook so Ctrl+N and the folder context
        menu share the same Markdown/TXT and source/Office choices. Standalone
        FileBrowserView consumers retain the legacy Markdown-only prompt.
        """
        if callable(self.on_new_note_requested):
            self.on_new_note_requested(str(folder))
            return
        self._create_note_action(folder)

    def _create_note_action(self, folder: str):
        name, ok = QInputDialog.getText(self, "新增筆記", "筆記名稱：")
        if not ok or not name.strip():
            return
        try:
            path = file_ops.create_note(folder, name)
        except OSError as exc:
            QMessageBox.warning(self, "新增筆記失敗", f"無法建立筆記：\n{exc}")
            return
        self._remember_expanded(folder)
        self.refresh_libraries()
        self._select_path(path)
        if self.on_note_created:
            self.on_note_created(str(path))

    def _create_folder_action(self, parent_path: str):
        name, ok = QInputDialog.getText(self, "新增資料夾", "資料夾名稱：")
        if not ok or not name.strip():
            return
        try:
            path = file_ops.create_folder(parent_path, name)
        except OSError as exc:
            QMessageBox.warning(self, "新增資料夾失敗", f"無法建立資料夾：\n{exc}")
            return
        self._remember_expanded(parent_path)
        self._transient_folders.add(str(path))
        self.refresh_libraries()
        self.navigate_to(path)

    def _rename_library_action(self, library_id: str):
        current = next(
            (lib.name for lib in self._libraries if lib.id == library_id), ""
        )
        name, ok = QInputDialog.getText(
            self, "重新命名文件庫", "文件庫顯示名稱：", text=current
        )
        if not ok or not name.strip() or name.strip() == current:
            return
        self._store.rename(library_id, name.strip())
        self.refresh_libraries()

    def _rename_folder_action(self, path: str):
        folder = Path(path)
        name, ok = QInputDialog.getText(
            self, "重新命名資料夾", "新名稱：", text=folder.name
        )
        if not ok or not name.strip() or name.strip() == folder.name:
            return
        try:
            mapping = file_ops.rename_folder(folder, name.strip())
        except OSError as exc:
            QMessageBox.warning(self, "重新命名失敗", f"無法重新命名資料夾：\n{exc}")
            return
        new_folder = folder.with_name(name.strip())
        self._migrate_expanded_prefix(str(folder), str(new_folder))
        self._finish_migration(mapping, select=new_folder)

    def _rename_file_action(self, path: str):
        p = Path(path)
        name, ok = QInputDialog.getText(
            self, "重新命名", "新檔名：", text=p.stem
        )
        if not ok:
            return
        name = name.strip()
        if name.lower().endswith(p.suffix.lower()) and len(name) > len(p.suffix):
            name = name[: -len(p.suffix)].strip()
        if not name or name == p.stem:
            return
        if not file_ops.is_valid_name(name):
            QMessageBox.warning(
                self,
                "重新命名失敗",
                f"檔名不能包含下列字元：{file_ops.INVALID_NAME_CHARS}",
            )
            return
        try:
            mapping = file_ops.rename_document(p, p.with_name(name + p.suffix))
        except OSError as exc:
            QMessageBox.warning(self, "重新命名失敗", f"無法重新命名檔案：\n{exc}")
            return
        self._finish_migration(mapping, select=Path(mapping[str(p)]))

    def _move_file_action(self, path: str):
        p = Path(path)
        folder = QFileDialog.getExistingDirectory(
            self,
            "移動到資料夾",
            str(p.parent),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        try:
            mapping = file_ops.move_document(p, folder)
        except OSError as exc:
            QMessageBox.warning(self, "移動失敗", f"無法移動檔案：\n{exc}")
            return
        if not mapping:
            return
        self._finish_migration(mapping, select=Path(mapping[str(p)]))

    def _delete_file_action(self, path: str):
        p = Path(path)
        if file_ops.HAS_SEND2TRASH:
            message = f"要將「{p.name}」移到資源回收筒嗎？"
        else:
            message = (
                f"將永久刪除「{p.name}」，無法復原。\n確定要刪除嗎？"
            )
        answer = QMessageBox.question(
            self,
            "刪除檔案",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            file_ops.delete_document(p)
        except OSError as exc:
            QMessageBox.warning(self, "刪除失敗", f"無法刪除檔案：\n{exc}")
            return
        if self._tag_index is not None:
            try:
                self._tag_index.remove_path(p)
            except OSError:
                pass
        self.refresh_libraries()
        if self.on_paths_deleted:
            self.on_paths_deleted([str(p)])

    def _finish_migration(self, mapping: dict, select: Path | None = None):
        if not mapping:
            return
        if self._tag_index is not None:
            try:
                self._tag_index.migrate_paths(mapping)
            except OSError:
                pass
        self.refresh_libraries()
        if select is not None:
            self._select_path(select)
        if self.on_paths_migrated:
            self.on_paths_migrated(dict(mapping))

    def _remember_expanded(self, folder: str):
        self._sync_expanded_from_tree()
        if self._expanded is None:
            self._expanded = {
                str(self._tree.topLevelItem(i).data(0, _PATH_ROLE))
                for i in range(self._tree.topLevelItemCount())
                if self._tree.topLevelItem(i).data(0, _PATH_ROLE)
            }
        self._expanded.add(str(folder))

    def _migrate_expanded_prefix(self, old_prefix: str, new_prefix: str):
        self._sync_expanded_from_tree()
        if not self._expanded:
            return
        old_key = _path_key(old_prefix)
        updated = set()
        for path in self._expanded:
            key = _path_key(path)
            if key == old_key or key.startswith(old_key + "\\") or key.startswith(
                old_key + "/"
            ):
                updated.add(new_prefix + path[len(old_prefix):])
            else:
                updated.add(path)
        self._expanded = updated

    # ---------------- library management ----------------
    def _add_library(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "新增文件庫資料夾",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        _lib, added = self._store.add(folder)
        if not added:
            QMessageBox.information(self, "文件庫已存在", "這個資料夾已在文件庫中。")
        self.refresh_libraries()

    def _manage_libraries(self):
        dialog = LibraryManagerDialog(self._store, self._theme, self)
        dialog.exec()
        if dialog.changed:
            self.refresh_libraries()

    def _open_location(self, path: str):
        subprocess.run(["explorer", "/select,", str(Path(path))])

    # ---------------- public wrappers ----------------
    # Thin pass-throughs so other panels (e.g. the 標籤 tab) can reuse the exact
    # same file operations -- same dialogs, file_ops, tag-index migration and
    # refresh/callbacks -- instead of re-implementing filesystem + tag logic.
    def rename_file(self, path: str | Path) -> None:
        self._rename_file_action(str(path))

    def move_file(self, path: str | Path) -> None:
        self._move_file_action(str(path))

    def delete_file(self, path: str | Path) -> None:
        self._delete_file_action(str(path))

    def reveal_file(self, path: str | Path) -> None:
        self._open_location(str(path))

    @staticmethod
    def _tree_stylesheet(theme: Theme) -> str:
        """Compact, continuous row styling reserved for the library tree."""
        return f"""
QTreeWidget#libraryTree {{
    background: {theme.surface};
    border: none;
    color: {theme.text};
    outline: 0;
    show-decoration-selected: 1;
}}
QTreeWidget#libraryTree:focus {{
    border: none;
}}
QTreeWidget#libraryTree:disabled {{
    background: {theme.surface_alt};
    color: {theme.text_subtle};
}}
QTreeWidget#libraryTree::item {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text};
    min-height: 22px;
    padding: 3px 6px;
}}
QTreeWidget#libraryTree::item:hover {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QTreeWidget#libraryTree::item:selected {{
    background: {theme.surface_active};
    border-color: {theme.accent_soft};
    color: {theme.text};
}}
QTreeWidget#libraryTree::item:selected:active {{
    border-color: {theme.accent};
}}
QTreeWidget#libraryTree::item:disabled {{
    background: transparent;
    color: {theme.text_subtle};
}}
"""

    def _stylesheet(self, theme: Theme) -> str:
        return f"""
QWidget#fileBrowser {{
    background: {theme.surface};
}}
QWidget#fileBrowser QLabel {{
    background: transparent;
    color: {theme.text};
}}
QWidget#fileBrowser QLabel[muted="true"] {{
    color: {theme.text_muted};
    font-size: 12px;
}}
QWidget#fileBrowser QLineEdit {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 32px;
    padding: 4px 10px;
}}
QWidget#fileBrowser QLineEdit:focus {{
    border-color: {theme.accent};
}}
QWidget#fileBrowser QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text_muted};
    min-height: 34px;
    min-width: 34px;
    padding: 0 8px;
}}
QWidget#fileBrowser QPushButton#addLibraryButton {{
    color: {theme.accent};
    border-color: {theme.border};
}}
QWidget#fileBrowser QPushButton#addLibraryButton:hover {{
    background: {theme.accent_soft};
    border-color: {theme.accent};
    color: {theme.text};
}}
QWidget#fileBrowser QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
    color: {theme.text};
}}
QWidget#fileBrowser QPushButton:pressed {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
"""

    def _menu_stylesheet(self) -> str:
        theme = self._theme
        return f"""
QMenu {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 4px;
    color: {theme.text};
}}
QMenu::item {{
    padding: 6px 20px;
    color: {theme.text};
}}
QMenu::item:selected {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
"""


class LibraryManagerDialog(QDialog):
    def __init__(
        self,
        store: DocumentLibraryStore,
        theme: Theme = LIGHT,
        parent=None,
    ):
        super().__init__(parent)
        self._store = store
        self._theme = theme
        self.changed = False

        self.setWindowTitle("管理文件庫")
        self.resize(560, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        hint = QLabel(
            "可加入本機資料夾，也可直接加入 Google Drive for desktop 的同步資料夾。"
        )
        hint.setWordWrap(True)
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._update_button_state)
        layout.addWidget(self._list, stretch=1)

        row = QHBoxLayout()
        self._add_btn = QPushButton("新增資料夾")
        self._add_btn.clicked.connect(self._add_folder)
        self._detect_btn = QPushButton("偵測雲端資料夾")
        self._detect_btn.clicked.connect(self._detect_cloud_folders)
        self._open_btn = QPushButton("開啟資料夾")
        self._open_btn.clicked.connect(self._open_selected)
        self._remove_btn = QPushButton("移除")
        self._remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(self._add_btn)
        row.addWidget(self._detect_btn)
        row.addStretch()
        row.addWidget(self._open_btn)
        row.addWidget(self._remove_btn)
        layout.addLayout(row)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("關閉")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self.apply_theme(theme)
        self._refresh()

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(self._stylesheet(theme))
        self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))

    def _refresh(self):
        self._list.clear()
        for lib in self._store.load():
            status = "可用" if Path(lib.path).exists() else "找不到資料夾"
            item = QListWidgetItem(f"{lib.name}\n{lib.path}\n{status}")
            item.setSizeHint(QSize(0, 66))
            item.setData(_LIBRARY_ROLE, lib.id)
            item.setToolTip(lib.path)
            if status != "可用":
                item.setForeground(QColor(self._theme.warning))
            self._list.addItem(item)

        if self._list.count() == 0:
            item = QListWidgetItem("尚未加入文件庫")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
        self._update_button_state()

    def _selected_library(self) -> DocumentLibrary | None:
        item = self._list.currentItem()
        if not item:
            return None
        lib_id = item.data(_LIBRARY_ROLE)
        if not lib_id:
            return None
        for lib in self._store.load():
            if lib.id == lib_id:
                return lib
        return None

    def _update_button_state(self):
        has_selection = self._selected_library() is not None
        self._open_btn.setEnabled(has_selection)
        self._remove_btn.setEnabled(has_selection)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "新增文件庫資料夾",
            str(Path.home()),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not folder:
            return
        _lib, added = self._store.add(folder)
        if not added:
            QMessageBox.information(self, "文件庫已存在", "這個資料夾已在文件庫中。")
        self.changed = True
        self._refresh()

    def _detect_cloud_folders(self):
        paths = discover_cloud_library_paths()
        added_count = 0
        for path in paths:
            _lib, added = self._store.add(path)
            if added:
                added_count += 1

        if added_count:
            QMessageBox.information(
                self, "已加入雲端資料夾", f"已加入 {added_count} 個資料夾。"
            )
            self.changed = True
            self._refresh()
        else:
            QMessageBox.information(
                self,
                "未偵測到新來源",
                "沒有找到新的 Google Drive、OneDrive 或 Dropbox 同步資料夾。",
            )

    def _open_selected(self):
        lib = self._selected_library()
        if lib:
            QDesktopServices.openUrl(QUrl.fromLocalFile(lib.path))

    def _remove_selected(self):
        lib = self._selected_library()
        if not lib:
            return
        answer = QMessageBox.question(
            self,
            "移除文件庫",
            f"要從清單移除「{lib.name}」嗎？\n\n檔案本身不會被刪除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._store.remove(lib.id)
        self.changed = True
        self._refresh()

    def _stylesheet(self, theme: Theme) -> str:
        return f"""
QDialog {{
    background: {theme.surface};
    color: {theme.text};
}}
QLabel {{
    background: transparent;
    color: {theme.text};
}}
QLabel[muted="true"] {{
    color: {theme.text_muted};
}}
QPushButton {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 34px;
    padding: 0 12px;
}}
QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.accent};
}}
QPushButton:pressed {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
QPushButton:disabled {{
    background: {theme.surface_alt};
    border-color: {theme.border};
    color: {theme.text_subtle};
}}
"""
