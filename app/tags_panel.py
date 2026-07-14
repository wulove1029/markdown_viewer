"""Sidebar panel listing all tags across the library, with file counts."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .file_types import is_markdown, is_pdf
from .theme import LIGHT, Theme, collection_stylesheet

# Custom drag mime carrying newline-joined absolute file paths (from the
# file browser). Kept in sync with app/file_browser.py.
_MIME_PATHS = "application/x-mdv-paths"

# File count for a tag node, stored on its own role so the delegate reads it
# directly instead of parsing the visible text. Offset past UserRole (which
# already carries the node's kind/tag dict) mirrors file_browser.py's roles.
_COUNT_ROLE = Qt.ItemDataRole.UserRole.value + 1

# Tag-node pill geometry (px). English comments per project rules.
_PILL_HEIGHT = 18       # pill badge height
_PILL_HPAD = 8          # horizontal padding inside the pill (each side)
_PILL_VMARGIN = 3       # vertical margin above/below the pill within the row
_PILL_COUNT_GAP = 6     # gap between the pill and the "· N" count
_ITEM_HMARGIN = 4       # left/right content margin inside the item rect


def _relative_luminance(color: QColor) -> float:
    """WCAG relative luminance of a color, in [0, 1].

    Copied (kept tiny) from the file browser's pill delegate rather than shared
    via import, to keep the two panels decoupled; both must stay in sync.
    """

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


class _TagNodeDelegate(QStyledItemDelegate):
    """Renders each ``kind == "tag"`` node as a filled, named color pill.

    The pill is filled with the tag's color and carries the tag name in
    contrast-aware text (dark on light fills, white on dark), followed by a
    muted "· N" file count. This mirrors the file browser's tag pills so the
    two tabs read consistently. Every other row -- the "全部（清除篩選）" row,
    the "尚無標籤"/placeholder rows, and the lazily-loaded file children --
    falls back to the default painting so they look unchanged.
    """

    def __init__(self, color_for=None, parent=None):
        super().__init__(parent)
        self._color_for = color_for
        # Muted color for the "· N" count; kept in sync with the theme.
        self._muted = QColor(LIGHT.text_subtle)

    def set_muted_color(self, hex_color: str) -> None:
        color = QColor(hex_color)
        if color.isValid():
            self._muted = color

    # ---- helpers -----------------------------------------------------
    def _tag_node(self, index):
        """Return ``(tag, count)`` for a tag row, or ``None`` for other rows."""
        data = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") != "tag":
            return None
        tag = data.get("tag", "") or ""
        raw = index.data(_COUNT_ROLE)
        try:
            count = int(raw)
        except (TypeError, ValueError):
            count = 0
        return tag, count

    def _pill_fill(self, tag: str) -> QColor:
        fill = QColor("#8B8D98")
        if self._color_for is not None:
            try:
                candidate = QColor(self._color_for(tag))
                if candidate.isValid():
                    fill = candidate
            except (TypeError, ValueError):
                pass
        return fill

    # ---- Qt overrides ------------------------------------------------
    def sizeHint(self, option, index):  # noqa: N802 (Qt override)
        base = super().sizeHint(option, index)
        if self._tag_node(index) is None:
            return base
        # A touch taller than plain text so the pill breathes, but still tidy.
        height = max(base.height(), _PILL_HEIGHT + _PILL_VMARGIN * 2)
        return QSize(base.width(), height)

    def paint(self, painter, option, index):  # noqa: N802 (Qt override)
        node = self._tag_node(index)
        if node is None:
            # 全部 / 尚無標籤 / placeholder / file children: default look.
            super().paint(painter, option, index)
            return
        tag, count = node

        style = option.widget.style() if option.widget else QApplication.style()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        # 1) Draw the row background / selection / hover, but suppress the
        #    style's own text + icon so the pill replaces them (and no stray
        #    swatch dot survives). option.rect already excludes the expander,
        #    so the pill never covers the tree's expand arrow.
        opt.text = ""
        opt.icon = QIcon()
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget
        )

        rect = option.rect
        fm = option.fontMetrics

        pill_h = min(rect.height() - _PILL_VMARGIN * 2, _PILL_HEIGHT)
        if pill_h <= 0:
            pill_h = _PILL_HEIGHT
        radius = pill_h / 2
        x = rect.left() + _ITEM_HMARGIN
        y = rect.top() + (rect.height() - pill_h) // 2
        right_limit = rect.right() - _ITEM_HMARGIN

        count_text = f"· {count}"
        count_w = fm.horizontalAdvance(count_text)

        # Reserve room for the count on the right; elide the tag label if the
        # name is too long to fit the remaining width (rare in a narrow panel).
        max_label_w = right_limit - x - _PILL_HPAD * 2 - _PILL_COUNT_GAP - count_w
        label = fm.elidedText(
            tag, Qt.TextElideMode.ElideRight, max(0, max_label_w)
        )
        pill_w = fm.horizontalAdvance(label) + _PILL_HPAD * 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(option.font)

        fill = self._pill_fill(tag)
        pill_rect = QRectF(x, y, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill_rect, radius, radius)
        painter.setPen(_pill_text_color(fill))
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, label)

        # "· N" count in the muted color. The theme pairs its subtle selection
        # tint with normal text, so the muted color stays readable when selected
        # too (borrowing highlightedText/white made it vanish on the light
        # theme's pale selection).
        painter.setPen(self._muted)
        count_x = int(x + pill_w + _PILL_COUNT_GAP)
        count_rect = QRect(
            count_x, rect.top(), max(0, right_limit - count_x), rect.height()
        )
        painter.drawText(
            count_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            count_text,
        )
        painter.restore()


class _TagDropTree(QTreeWidget):
    """Tag tree that accepts file-path drops to assign a tag.

    Dropping the file-browser's ``application/x-mdv-paths`` payload onto a tag
    node assigns that tag to the dragged files (same mime as the old flat
    list; only the drop target changed from a list row to a tree node).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # on_assign(tag: str, paths: list[Path]) -> None
        self._on_assign = None
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def set_assign_handler(self, handler):
        self._on_assign = handler

    @staticmethod
    def _paths_from(mime) -> list[Path]:
        if not mime.hasFormat(_MIME_PATHS):
            return []
        raw = bytes(mime.data(_MIME_PATHS)).decode("utf-8", "ignore")
        return [Path(line) for line in raw.splitlines() if line.strip()]

    @staticmethod
    def _tag_at(item) -> str:
        """Return the tag string for a drop landing on *item* (or "").

        Only real tag nodes accept a drop; the "全部" row, placeholders and
        file children resolve to "" (drop ignored).
        """
        if item is None:
            return ""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("kind") == "tag":
            return data.get("tag", "") or ""
        return ""

    def dragEnterEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MIME_PATHS):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_MIME_PATHS):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802 (Qt override)
        mime = event.mimeData()
        if not mime.hasFormat(_MIME_PATHS):
            super().dropEvent(event)
            return
        item = self.itemAt(event.position().toPoint())
        tag = self._tag_at(item)
        paths = self._paths_from(mime)
        if tag and paths and self._on_assign is not None:
            self._on_assign(tag, paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class TagsPanel(QWidget):
    def __init__(
        self,
        on_tag_selected,
        tag_color_for=None,
        on_delete_tag: Callable[[str], None] | None = None,
        on_rename_tag: Callable[[str], None] | None = None,
        on_assign_tag_to_paths=None,
        on_open_file: Callable[[Path], None] | None = None,
        on_manage_tags: Callable[[list[Path]], None] | None = None,
        on_add_tag: Callable[[list[Path]], None] | None = None,
        on_rename_file: Callable[[Path], None] | None = None,
        on_move_file: Callable[[Path], None] | None = None,
        on_delete_file: Callable[[Path], None] | None = None,
        on_reveal_file: Callable[[Path], None] | None = None,
        files_for_tag: Callable[[str], list[Path]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        # on_tag_selected(tag): "" clears the filter.
        self._on_tag_selected = on_tag_selected
        # on_open_file(path): open a file listed under the selected tag. Reuses
        # the same open-file callback the file browser uses (single click).
        self._on_open_file = on_open_file
        # File-child right-click actions. Each mirrors the 檔案 tab and reuses
        # the file browser's operations (via the window) so the tag index and
        # every view stay consistent. Any left as None is simply omitted from
        # the menu. on_manage_tags(paths: list[Path]); the rest take one Path.
        self._on_manage_tags = on_manage_tags
        # on_add_tag(paths: list[Path]) quick-assigns one tag to the file(s).
        self._on_add_tag = on_add_tag
        self._on_rename_file = on_rename_file
        self._on_move_file = on_move_file
        self._on_delete_file = on_delete_file
        self._on_reveal_file = on_reveal_file
        # files_for_tag(tag) -> list[Path]: files carrying *tag*, loaded lazily
        # to fill a tag node's children the first time it is expanded. When
        # None, tag nodes have no expander (nothing to load).
        self._files_for_tag = files_for_tag
        # tag_color_for(tag: str) -> hex color for the row swatch.
        self._tag_color_for = tag_color_for
        # on_delete_tag(tag: str) removes the tag (right-click menu).
        self._on_delete_tag = on_delete_tag
        # on_rename_tag(tag: str) renames the tag globally (right-click menu).
        self._on_rename_tag = on_rename_tag
        self._theme = LIGHT

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 8, 6)
        header.setSpacing(4)
        title = QLabel("標籤")
        title.setProperty("heading", True)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Single tree: each tag is an expandable node whose children are the
        # files (MD + PDF) carrying it. Clicking a tag toggles its files open
        # in place; clicking a file opens it. Files load lazily on first
        # expand (see *files_for_tag*), so the "標籤" tab stays self-contained
        # without a separate results list.
        self._tree = _TagDropTree()
        self._tree.setHeaderHidden(True)
        self._tree.setColumnCount(1)
        # Tag nodes are a touch taller (they carry a pill), so uniform row
        # heights must stay off for the pill row to get its full height.
        self._tree.setUniformRowHeights(False)
        self._tree.setIndentation(16)
        self._tree.setRootIsDecorated(True)
        self._tree.setExpandsOnDoubleClick(False)
        # Draw tag nodes as colored, named pills (matches the file browser);
        # 全部 / file children keep the default look inside the delegate.
        self._tag_delegate = _TagNodeDelegate(self._tag_color_for, self._tree)
        self._tree.setItemDelegate(self._tag_delegate)
        self._tree.set_assign_handler(on_assign_tag_to_paths)
        self._tree.itemClicked.connect(self._on_tree_clicked)
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._tree, 1)

        self.apply_theme(LIGHT)
        self.set_tags([])

    def apply_theme(self, theme: Theme):
        self._theme = theme
        self.setStyleSheet(
            f"""
QWidget {{ background: {theme.surface}; color: {theme.text}; }}
QLabel[heading="true"] {{
    color: {theme.text_muted};
    font-weight: 600;
}}
"""
        )
        self._tree.setStyleSheet(collection_stylesheet(theme, "QTreeWidget"))
        # Keep the pill delegate's muted count color aligned with the theme.
        self._tag_delegate.set_muted_color(theme.text_subtle)

    def set_tags(self, tag_counts):
        # Remember which tags were open so a rebuild (counts changed, tag
        # added/removed) restores the expanded state and reloads their files.
        expanded = self._expanded_tags()

        self._tree.blockSignals(True)
        self._tree.clear()
        clear_item = QTreeWidgetItem(["全部（清除篩選）"])
        clear_item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "all", "tag": ""})
        self._tree.addTopLevelItem(clear_item)
        if not tag_counts:
            empty = QTreeWidgetItem(["尚無標籤"])
            empty.setFlags(empty.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            empty.setData(0, Qt.ItemDataRole.UserRole, {"kind": "empty", "tag": ""})
            self._tree.addTopLevelItem(empty)
            self._tree.blockSignals(False)
            return
        for tag, count in tag_counts:
            # Text kept as a readable fallback (accessibility / if the delegate
            # is ever absent); the delegate draws a colored pill + "· N" count
            # from the tag and _COUNT_ROLE, so no swatch icon is set here.
            item = QTreeWidgetItem([f"#{tag}　·　{count}"])
            item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "tag", "tag": tag})
            item.setData(0, _COUNT_ROLE, int(count))
            self._tree.addTopLevelItem(item)
            # Lazy expansion: only offer an expander when files can be loaded.
            if self._files_for_tag is not None:
                self._add_placeholder(item)
        self._tree.blockSignals(False)

        # Restore previously-open tags; expanding reloads children lazily.
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "tag" and data.get("tag") in expanded:
                item.setExpanded(True)

    def set_active(self, tag: str):
        """Highlight the tree node matching *tag* ("" selects the 全部 row)."""
        self._tree.blockSignals(True)
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") in ("tag", "all") and data.get("tag") == tag:
                self._tree.setCurrentItem(item)
                break
        self._tree.blockSignals(False)

    @staticmethod
    def _file_type_prefix(path: Path) -> str:
        """Short prefix distinguishing PDF from Markdown rows."""
        if is_pdf(path):
            return "PDF"
        if is_markdown(path):
            return "MD"
        return "檔案"

    def set_tag_files(self, files=None) -> None:
        """Deprecated no-op kept for backward compatibility.

        Files carrying a tag are now shown as lazily-loaded child rows under
        their tag node in the tree (see *files_for_tag*), so there is no
        separate results list to fill. Older callers/tests may still call
        this; it does nothing.
        """
        return None

    # --- tree helpers -----------------------------------------------------
    def _add_placeholder(self, item: QTreeWidgetItem) -> None:
        """Attach a hidden placeholder so *item* shows an expander.

        The real file children replace it the first time the node expands
        (lazy load), keeping ``set_tags`` cheap when many tags are listed.
        """
        placeholder = QTreeWidgetItem([""])
        placeholder.setData(
            0, Qt.ItemDataRole.UserRole, {"kind": "placeholder", "tag": ""}
        )
        placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.addChild(placeholder)

    @staticmethod
    def _needs_load(item: QTreeWidgetItem) -> bool:
        """True when *item* still holds only its lazy-load placeholder."""
        if item.childCount() != 1:
            return False
        child = item.child(0)
        data = child.data(0, Qt.ItemDataRole.UserRole) or {}
        return data.get("kind") == "placeholder"

    def _expanded_tags(self) -> set:
        out: set = set()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == "tag" and item.isExpanded():
                out.add(data.get("tag"))
        return out

    def _load_children(self, item: QTreeWidgetItem, tag: str) -> None:
        """Replace *item*'s placeholder with its actual file children."""
        item.takeChildren()
        files = []
        if self._files_for_tag is not None:
            try:
                files = self._files_for_tag(tag) or []
            except (TypeError, ValueError):
                files = []
        for raw in files:
            path = Path(raw)
            child = QTreeWidgetItem(
                [f"{self._file_type_prefix(path)} · {path.name}"]
            )
            child.setData(
                0, Qt.ItemDataRole.UserRole,
                {"kind": "file", "tag": tag, "path": path},
            )
            child.setToolTip(0, str(path))
            item.addChild(child)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") != "tag":
            return
        if self._needs_load(item):
            self._load_children(item, data.get("tag", ""))

    def _on_tree_clicked(self, item: QTreeWidgetItem, _column: int = 0):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = data.get("kind")
        if kind == "all":
            self._on_tag_selected("")
        elif kind == "tag":
            self._on_tag_selected(data.get("tag", ""))
            # Toggle the tag's files open/closed in place.
            item.setExpanded(not item.isExpanded())
        elif kind == "file":
            path = data.get("path")
            if path is not None and self._on_open_file is not None:
                self._on_open_file(Path(path))

    def _on_context_menu(self, pos):
        menu = self._menu_for_item(self._tree.itemAt(pos))
        if menu is None or not menu.actions():
            return
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _menu_for_item(self, item) -> QMenu | None:
        """Return the right-click menu for *item*, or None when it has none.

        Tag nodes keep their existing "刪除標籤" menu; file children get the
        same file operations as the 檔案 tab. The 全部 row, placeholders and the
        "尚無標籤" empty row have no context menu. Split from exec so tests can
        inspect the menu without driving the modal popup.
        """
        if item is None:
            return None
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        kind = data.get("kind")
        if kind == "tag":
            return self._build_tag_menu(data.get("tag"))
        if kind == "file":
            return self._build_file_menu(data.get("path"))
        return None

    def _build_tag_menu(self, tag) -> QMenu:
        """Right-click menu for a tag node: rename / delete the tag.

        Each entry appears only when its callback is wired. Kept split from
        exec so tests can inspect the menu without driving the modal popup.
        """
        menu = QMenu(self)
        if not tag:
            return menu
        if self._on_rename_tag is not None:
            action = menu.addAction("重新命名標籤")
            action.triggered.connect(
                lambda _=False, t=tag: self._on_rename_tag(t)
            )
        if self._on_delete_tag is not None:
            action = menu.addAction("刪除標籤")
            action.triggered.connect(
                lambda _=False, t=tag: self._on_delete_tag(t)
            )
        return menu

    def _build_file_menu(self, path) -> QMenu:
        """Right-click menu for a file child, mirroring the 檔案 tab.

        Actions with no callback wired are omitted. The file operations
        (rename / move / delete / reveal) route back to the file browser's
        own logic via the window so the tag index and every view stay in sync.
        """
        menu = QMenu(self)
        if path is None:
            return menu
        path = Path(path)

        def add(label: str, handler, *, arg=path):
            if handler is None:
                return
            action = menu.addAction(label)
            action.triggered.connect(lambda _=False: handler(arg))

        add("開啟文件", self._on_open_file)
        add("加入標籤…", self._on_add_tag, arg=[path])
        add("管理標籤…", self._on_manage_tags, arg=[path])
        add("重新命名", self._on_rename_file)
        add("移動到…", self._on_move_file)
        add("刪除", self._on_delete_file)
        add("在檔案總管顯示", self._on_reveal_file)
        return menu
