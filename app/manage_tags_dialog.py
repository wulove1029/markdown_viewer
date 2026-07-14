"""EndNote-style "Manage Tags" dialog for assigning document-level tags.

Doubles as a lightweight tag *manager*: besides toggling which of a file's (or
several files') document-level tags are set, every listed tag can be renamed or
deleted in-place. Those two actions route back to the window's global tag
operations (``on_rename_tag`` / ``on_delete_tag``), which own the confirm /
input prompts, the tag-index migration and the panel refresh. After such a
global change the list is rebuilt from the freshly-persisted state while the
user's not-yet-confirmed checkbox changes on surviving tags are preserved.

Each row shows ``[checkbox][color dot][tag name] ... [rename][delete]``. When
editing multiple files at once, a tag present on only *some* of them renders as
tri-state (partially checked); leaving it partial preserves each file's existing
state on confirm.

All persistence flows through :mod:`app.doc_tags` so the MD/PDF dispatch and
sidecar writes stay consistent with the rest of the app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
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
from .theme import LIGHT, Theme, svg_icon

_SWATCH_PX = 12
_NEW_SWATCH_PX = 26
_ROW_BTN_PX = 26


def _swatch_pixmap(hex_color: str, size: int = _SWATCH_PX) -> QPixmap:
    """Build a small filled-circle pixmap for *hex_color* (row color dot)."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(QColor(hex_color))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return pix


class ManageTagsDialog(QDialog):
    """Modal popup to view/toggle/manage document-level tags for one/many files."""

    def __init__(
        self,
        paths: list[Path],
        tag_index,
        color_store: TagColorStore,
        on_changed: Callable[[list[Path]], None],
        theme: Theme | None = None,
        on_delete_tag: Callable[[str], None] | None = None,
        on_rename_tag: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._paths: list[Path] = [Path(p) for p in paths]
        self._tag_index = tag_index
        self._color_store = color_store
        self._on_changed = on_changed
        # Global tag operations (owned by the window): each pops its own confirm
        # / input dialog, migrates the tag index + color store, and refreshes the
        # main panel. Both persist immediately, so this dialog re-reads state
        # from disk afterwards. Left None when the caller does not support them.
        self._on_delete_tag = on_delete_tag
        self._on_rename_tag = on_rename_tag
        self._theme = theme
        # Selected color hex for the "create tag" row (defaults to first swatch).
        self._new_color: str = TagColorStore.palette_hexes()[0]

        # Per-tag row widgets, rebuilt whenever the list is (re)populated.
        self._checkboxes: dict[str, QCheckBox] = {}
        self._rename_buttons: dict[str, QPushButton] = {}
        self._delete_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle("管理標籤")
        self.setMinimumWidth(420)

        # Snapshot of each file's on-disk document tags; the base check state and
        # the confirm merge are computed from this. Refreshed on rebuild.
        self._current_tags: dict[Path, set[str]] = {}
        self._reload_current_tags()

        self._build_ui()
        self._apply_dialog_theme()
        self._populate_tags()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)

        title = QLabel("管理標籤")
        title.setProperty("dialogHeading", True)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(title)

        subtitle = QLabel(self._header_text())
        subtitle.setProperty("subtitle", True)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list, 1)

        # ── create-a-tag row ──
        create_row = QHBoxLayout()
        create_row.setSpacing(6)
        self._new_input = QLineEdit()
        self._new_input.setPlaceholderText("新增標籤名稱")
        self._new_input.returnPressed.connect(self._create_tag)
        create_row.addWidget(self._new_input, 1)

        # 7-swatch color picker: one compact flat button per palette color. The
        # box-model is reset (min-width/height, padding, margin) so the fixed
        # size is honored instead of inheriting the app-wide QPushButton metrics.
        self._swatch_buttons: list[QPushButton] = []
        for hex_color in TagColorStore.palette_hexes():
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(_NEW_SWATCH_PX, _NEW_SWATCH_PX)
            btn.setToolTip(hex_color)
            btn.clicked.connect(
                lambda _checked=False, c=hex_color: self._select_new_color(c)
            )
            create_row.addWidget(btn)
            self._swatch_buttons.append(btn)

        add_btn = QPushButton("新增")
        add_btn.setStyleSheet(
            "QPushButton { min-width: 0; min-height: 0; padding: 4px 12px; }"
        )
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

    def _apply_dialog_theme(self) -> None:
        """Style the whole dialog from *theme* so light/dark text stays legible.

        When no theme is supplied, styling is left to the parent's cascading
        stylesheet (backward-compatible behavior). The QListWidget in particular
        needs explicit colors: the app-wide sheet does not cover it, so its item
        text would otherwise render invisibly on the dark dialog background.
        """
        t = self._theme
        if t is None:
            return
        self.setStyleSheet(
            f"""
QDialog {{ background: {t.window}; color: {t.text}; }}
QLabel {{ background: transparent; color: {t.text}; }}
QLabel[subtitle="true"] {{ color: {t.text_muted}; }}
QCheckBox {{ background: transparent; color: {t.text}; }}
QListWidget {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
    color: {t.text};
    outline: 0;
}}
QListWidget::item {{ color: {t.text}; border: none; }}
QListWidget::item:selected {{ background: transparent; }}
QLineEdit {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
    color: {t.text};
    padding: 4px 8px;
    min-height: 0;
    selection-background-color: {t.accent_soft};
    selection-color: {t.text};
}}
QLineEdit:focus {{ border: 1px solid {t.accent}; }}
QPushButton {{
    background: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
    color: {t.text};
    min-height: 30px;
    padding: 4px 12px;
}}
QPushButton:hover {{ background: {t.surface_hover}; border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.surface_active}; }}
"""
        )

    def _header_text(self) -> str:
        if len(self._paths) == 1:
            return f"為「{self._paths[0].name}」設定標籤"
        return f"為 {len(self._paths)} 個檔案設定標籤"

    def _reload_current_tags(self) -> None:
        self._current_tags = {
            p: set(doc_tags.read_doc_tags(p)) for p in self._paths
        }

    def _all_known_tags(self) -> list[str]:
        """Union of tags in the index, tags on the selected files, and tags the
        user has created (color store) but not yet assigned to any file."""
        tags: set[str] = set(self._tag_index.all_tags())
        tags |= set(self._color_store.known_tags())
        for owned in self._current_tags.values():
            tags |= owned
        return sorted(tags)

    def _populate_tags(self, preserved: dict[str, Qt.CheckState] | None = None) -> None:
        self._list.clear()
        self._checkboxes.clear()
        self._rename_buttons.clear()
        self._delete_buttons.clear()
        for tag in self._all_known_tags():
            self._add_tag_row(tag)
            # Carry a surviving tag's not-yet-confirmed checkbox state across a
            # rebuild; new/renamed tags fall back to the freshly-read disk state.
            if preserved and tag in preserved and tag in self._checkboxes:
                self._checkboxes[tag].setCheckState(preserved[tag])

    def _initial_state(self, tag: str) -> Qt.CheckState:
        present = [t for t in self._current_tags.values() if tag in t]
        if present and len(present) == len(self._paths):
            return Qt.CheckState.Checked
        if not present:
            return Qt.CheckState.Unchecked
        return Qt.CheckState.PartiallyChecked

    def _add_tag_row(self, tag: str) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, tag)
        widget = self._build_row_widget(tag)
        self._list.addItem(item)
        item.setSizeHint(widget.sizeHint())
        self._list.setItemWidget(item, widget)
        return item

    def _build_row_widget(self, tag: str) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(8, 2, 6, 2)
        lay.setSpacing(6)

        checkbox = QCheckBox()
        if len(self._paths) > 1:
            checkbox.setTristate(True)
        checkbox.setCheckState(self._initial_state(tag))
        lay.addWidget(checkbox)
        self._checkboxes[tag] = checkbox

        dot = QLabel()
        dot.setPixmap(_swatch_pixmap(self._color_store.color_for(tag)))
        lay.addWidget(dot)

        name = QLabel(tag)
        lay.addWidget(name)
        lay.addStretch(1)

        if self._on_rename_tag is not None:
            rename_btn = self._icon_button("pencil", "改名", "重新命名標籤")
            rename_btn.clicked.connect(
                lambda _=False, t=tag: self._on_row_rename(t)
            )
            lay.addWidget(rename_btn)
            self._rename_buttons[tag] = rename_btn

        if self._on_delete_tag is not None:
            delete_btn = self._icon_button("x", "刪除", "刪除標籤")
            delete_btn.clicked.connect(
                lambda _=False, t=tag: self._on_row_delete(t)
            )
            lay.addWidget(delete_btn)
            self._delete_buttons[tag] = delete_btn

        return row

    def _icon_button(self, icon: str, fallback: str, tooltip: str) -> QPushButton:
        """A small, box-model-reset icon button for per-row rename/delete."""
        t = self._theme or LIGHT
        btn = QPushButton()
        try:
            btn.setIcon(svg_icon(icon, t.text_muted, 16))
        except KeyError:
            btn.setText(fallback)
        btn.setFixedSize(_ROW_BTN_PX, _ROW_BTN_PX)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            "QPushButton {"
            " background: transparent;"
            f" border: 1px solid {t.border}; border-radius: 5px;"
            " min-width: 0; min-height: 0; padding: 0; margin: 0; }"
            f" QPushButton:hover {{ background: {t.surface_hover};"
            f" border-color: {t.accent}; }}"
        )
        return btn

    # ── per-row manage actions (rename / delete) ───────────────────────

    def _on_row_rename(self, tag: str) -> None:
        """Rename *tag* globally, then rebuild the list from persisted state."""
        if self._on_rename_tag is None:
            return
        preserved = self._snapshot_states()
        self._on_rename_tag(tag)
        # The renamed tag no longer exists under its old name; drop its stale
        # snapshot so the rebuild reads the new name's state from disk.
        preserved.pop(tag, None)
        self._schedule_rebuild(preserved)

    def _on_row_delete(self, tag: str) -> None:
        """Delete *tag* globally, then rebuild the list from persisted state."""
        if self._on_delete_tag is None:
            return
        preserved = self._snapshot_states()
        self._on_delete_tag(tag)
        preserved.pop(tag, None)
        self._schedule_rebuild(preserved)

    def _snapshot_states(self) -> dict[str, Qt.CheckState]:
        """Capture the current (possibly unsaved) checkbox state per tag."""
        return {tag: cb.checkState() for tag, cb in self._checkboxes.items()}

    def _schedule_rebuild(self, preserved: dict[str, Qt.CheckState]) -> None:
        """Rebuild the list on the next event tick.

        Deferring avoids destroying the row's rename/delete button widget from
        inside its own ``clicked`` handler (a use-after-free), which would crash
        as control unwinds back into Qt's button code.
        """
        QTimer.singleShot(0, lambda: self._rebuild(preserved))

    def _rebuild(self, preserved: dict[str, Qt.CheckState]) -> None:
        self._reload_current_tags()
        self._populate_tags(preserved)

    # ── create-tag flow ────────────────────────────────────────────────

    def _select_new_color(self, hex_color: str) -> None:
        self._new_color = hex_color
        for btn in self._swatch_buttons:
            chosen = btn.toolTip() == hex_color
            border = "#FFFFFF" if chosen else "rgba(0, 0, 0, 0.28)"
            btn.setStyleSheet(
                "QPushButton {"
                f" background-color: {btn.toolTip()};"
                f" border: 2px solid {border}; border-radius: 4px;"
                " min-width: 0; min-height: 0; padding: 0; margin: 0; }"
            )
            btn.setChecked(chosen)

    def _create_tag(self) -> None:
        name = self._new_input.text().strip()
        if not name:
            return
        # Persist the explicit color choice for this brand-new tag.
        self._color_store.set_color(name, self._new_color)

        existing = self._checkboxes.get(name)
        if existing is None:
            self._add_tag_row(name)
            existing = self._checkboxes.get(name)
        if existing is not None:
            existing.setCheckState(Qt.CheckState.Checked)
            item = self._find_item(name)
            if item is not None:
                self._list.scrollToItem(item)
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
        for tag, checkbox in self._checkboxes.items():
            state = checkbox.checkState()
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
