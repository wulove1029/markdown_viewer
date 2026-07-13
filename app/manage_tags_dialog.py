"""EndNote-style "Manage Tags" dialog for assigning document-level tags.

Shows a checkable list of all known tags (from the central ``TagIndex``) with a
color swatch per tag, plus an inline "create tag" row with a 7-swatch color
picker. When editing multiple files at once, tags present on only *some* of the
selected files render as tri-state (partially checked); leaving them partial
preserves each file's existing state on confirm.

All persistence flows through :mod:`app.doc_tags` so the MD/PDF dispatch and
sidecar writes stay consistent with the rest of the app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import doc_tags
from .tag_colors import TagColorStore

_SWATCH_PX = 14


def _swatch_icon(hex_color: str, size: int = _SWATCH_PX) -> QIcon:
    """Build a small filled-circle icon for *hex_color*."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(hex_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pix)


class ManageTagsDialog(QDialog):
    """Modal popup to view/toggle document-level tags for one or many files."""

    def __init__(
        self,
        paths: list[Path],
        tag_index,
        color_store: TagColorStore,
        on_changed: Callable[[list[Path]], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths: list[Path] = [Path(p) for p in paths]
        self._tag_index = tag_index
        self._color_store = color_store
        self._on_changed = on_changed
        # Selected color hex for the "create tag" row (defaults to first swatch).
        self._new_color: str = TagColorStore.palette_hexes()[0]

        self.setWindowTitle("管理標籤")
        self.setMinimumWidth(360)

        self._current_tags: dict[Path, set[str]] = {
            p: set(doc_tags.read_doc_tags(p)) for p in self._paths
        }

        self._build_ui()
        self._populate_tags()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        header = QLabel(self._header_text())
        header.setWordWrap(True)
        layout.addWidget(header)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list, 1)

        # ── create-a-tag row ──
        create_row = QHBoxLayout()
        self._new_input = QLineEdit()
        self._new_input.setPlaceholderText("新增標籤名稱")
        self._new_input.returnPressed.connect(self._create_tag)
        create_row.addWidget(self._new_input, 1)

        # 7-swatch color picker: one flat button per palette color.
        self._swatch_buttons: list[QPushButton] = []
        for hex_color in TagColorStore.palette_hexes():
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(20, 20)
            btn.setToolTip(hex_color)
            btn.clicked.connect(
                lambda _checked=False, c=hex_color: self._select_new_color(c)
            )
            create_row.addWidget(btn)
            self._swatch_buttons.append(btn)

        add_btn = QPushButton("新增")
        add_btn.clicked.connect(self._create_tag)
        create_row.addWidget(add_btn)

        layout.addLayout(create_row)
        self._select_new_color(self._new_color)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("確定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _header_text(self) -> str:
        if len(self._paths) == 1:
            return f"為「{self._paths[0].name}」設定標籤"
        return f"為 {len(self._paths)} 個檔案設定標籤"

    def _all_known_tags(self) -> list[str]:
        """Union of tags in the index, tags on the selected files, and tags the
        user has created (color store) but not yet assigned to any file."""
        tags: set[str] = set(self._tag_index.all_tags())
        tags |= set(self._color_store.known_tags())
        for owned in self._current_tags.values():
            tags |= owned
        return sorted(tags)

    def _populate_tags(self) -> None:
        self._list.clear()
        for tag in self._all_known_tags():
            self._add_tag_row(tag)

    def _add_tag_row(self, tag: str) -> QListWidgetItem:
        item = QListWidgetItem(tag)
        item.setIcon(_swatch_icon(self._color_store.color_for(tag)))
        item.setData(Qt.ItemDataRole.UserRole, tag)

        present = [t for t in self._current_tags.values() if tag in t]
        flags = (
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
        )
        if len(self._paths) > 1:
            flags |= Qt.ItemFlag.ItemIsUserTristate
        item.setFlags(flags)

        if len(present) == len(self._paths):
            state = Qt.CheckState.Checked
        elif not present:
            state = Qt.CheckState.Unchecked
        else:
            state = Qt.CheckState.PartiallyChecked
        item.setCheckState(state)
        self._list.addItem(item)
        return item

    # ── create-tag flow ────────────────────────────────────────────────

    def _select_new_color(self, hex_color: str) -> None:
        self._new_color = hex_color
        for btn in self._swatch_buttons:
            chosen = btn.toolTip() == hex_color
            border = "#FFFFFF" if chosen else "transparent"
            btn.setStyleSheet(
                f"background-color: {btn.toolTip()};"
                f"border: 2px solid {border}; border-radius: 3px;"
            )
            btn.setChecked(chosen)

    def _create_tag(self) -> None:
        name = self._new_input.text().strip()
        if not name:
            return
        # Persist the explicit color choice for this brand-new tag.
        self._color_store.set_color(name, self._new_color)

        # If the tag already exists in the list, just check it; else add a row.
        existing = self._find_item(name)
        if existing is None:
            existing = self._add_tag_row(name)
            existing.setIcon(_swatch_icon(self._new_color))
        else:
            existing.setIcon(_swatch_icon(self._new_color))
        existing.setCheckState(Qt.CheckState.Checked)
        self._list.scrollToItem(existing)
        self._new_input.clear()

    def _find_item(self, tag: str) -> QListWidgetItem | None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == tag:
                return item
        return None

    # ── confirm ────────────────────────────────────────────────────────

    def _accept(self) -> None:
        """Compute the final tag set per path and persist via app.doc_tags."""
        checked: set[str] = set()
        unchecked: set[str] = set()
        for i in range(self._list.count()):
            item = self._list.item(i)
            tag = item.data(Qt.ItemDataRole.UserRole)
            state = item.checkState()
            if state == Qt.CheckState.Checked:
                checked.add(tag)
            elif state == Qt.CheckState.Unchecked:
                unchecked.add(tag)
            # PartiallyChecked -> leave each file's state unchanged.

        for path in self._paths:
            final = set(self._current_tags.get(path, set()))
            final |= checked
            final -= unchecked
            # Preserve the file's original order where possible, append the rest.
            ordered = [t for t in doc_tags.read_doc_tags(path) if t in final]
            ordered += sorted(t for t in final if t not in ordered)
            doc_tags.write_doc_tags(path, ordered)

        self._on_changed(list(self._paths))
        self.accept()
