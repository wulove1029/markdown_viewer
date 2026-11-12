"""Workspace presentation and discoverability, separate from document ownership."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QWidget,
)

from . import edit_backend, view_mode
from .command_palette import CommandPalette, action_entries


def _enabled(settings, key, default=False):
    value = settings.value(key, default)
    return value is True or str(value).lower() in {"true", "1", "yes", "on"}


class WorkspaceControls(QObject):
    """Keep compact/focus UI reversible without moving the live document."""

    def __init__(self, window, settings):
        super().__init__(window)
        self.window = window
        self.focused = False
        self.saved_visibility = []
        self._saving = False
        self._save_failed = False
        self._document_key = None
        self.settings = settings
        self.compact = settings.value("workspace_layout", "compact") != "expanded"
        toolbar = window._toolbar.layout()

        self.new_button = QPushButton("新增")
        self.new_button.setAccessibleName("新增筆記")
        self.new_button.clicked.connect(lambda: window._new_draft())
        toolbar.insertWidget(2, self.new_button)
        self.save_button = QPushButton("已儲存")
        self.save_button.setObjectName("workspaceSaveButton")
        self.save_button.clicked.connect(self.save)
        toolbar.insertWidget(4, self.save_button)

        self.mode = QComboBox()
        self.mode.setObjectName("workspaceMode")
        self.mode.setAccessibleName("閱讀、寫作或並排")
        for label, value in (("閱讀", "preview"), ("寫作", "edit"), ("並排", "split")):
            self.mode.addItem(label, value)
        self.backend = QComboBox()
        self.backend.setAccessibleName("寫作編輯器")
        self.backend.addItem("Markdown", edit_backend.SPLIT_BACKEND)
        self.backend.addItem("Office", edit_backend.WYSIWYG_BACKEND)
        position = toolbar.indexOf(window._reading_mode_combo)
        toolbar.insertWidget(position, self.mode)
        toolbar.insertWidget(position + 1, self.backend)
        self.mode.activated.connect(self._select_mode)
        self.backend.activated.connect(self._select_backend)
        self.focus_button = QPushButton("專注")
        self.focus_button.setToolTip("專注閱讀／寫作 (Ctrl+Alt+F)")
        self.focus_button.clicked.connect(self.toggle_focus)
        toolbar.insertWidget(toolbar.count() - 1, self.focus_button)
        self.more_button = QPushButton("更多")
        self.more_menu = QMenu(self.more_button)
        self.more_button.setMenu(self.more_menu)
        toolbar.insertWidget(toolbar.count() - 1, self.more_button)

        self.focus_rail = QWidget()
        self.focus_rail.setObjectName("focusRail")
        rail = QHBoxLayout(self.focus_rail)
        rail.setContentsMargins(16, 4, 16, 4)
        self.focus_title = QLabel("專注閱讀")
        rail.addWidget(self.focus_title, 1)
        leave = QPushButton("退出專注  Ctrl+Alt+F")
        leave.clicked.connect(self.toggle_focus)
        rail.addWidget(leave)
        window.centralWidget().layout().insertWidget(0, self.focus_rail)
        self.focus_rail.hide()

        self.layout_action = QAction("精簡工具列", self)
        self.layout_action.setCheckable(True)
        self.layout_action.setChecked(self.compact)
        self.layout_action.toggled.connect(self.set_compact)
        self.focus_action = QAction("專注閱讀／寫作\tCtrl+Alt+F", self)
        self.focus_action.setData("view.focus")
        self.focus_action.setProperty("commandId", "view.focus")
        self.focus_action.setCheckable(True)
        self.focus_action.triggered.connect(self.toggle_focus)
        self.palette_action = QAction("命令搜尋…\tCtrl+Alt+P", self)
        self.palette_action.setData("tools.command_palette")
        self.palette_action.setProperty("commandId", "tools.command_palette")
        self.palette_action.triggered.connect(self.show_palette)
        self.typewriter_action = QAction("打字機捲動", self)
        self.typewriter_action.setCheckable(True)
        self.typewriter_action.setChecked(_enabled(settings, "writing_typewriter"))
        self.typewriter_action.toggled.connect(self._set_typewriter)
        self.paragraph_action = QAction("聚焦目前段落", self)
        self.paragraph_action.setCheckable(True)
        self.paragraph_action.setChecked(_enabled(settings, "writing_focus_paragraph"))
        self.paragraph_action.toggled.connect(self._set_paragraph)
        view_menu = window._view_menu
        view_menu.addSeparator()
        view_menu.addActions([
            self.layout_action, self.focus_action, self.typewriter_action,
            self.paragraph_action, self.palette_action,
        ])
        self.outline_action = QAction("寫作大綱：跳轉、折疊、標題階層…", self)
        self.outline_action.triggered.connect(window._show_writing_outline)
        view_menu.addAction(self.outline_action)
        self.more_menu.addAction(self.outline_action)
        self.more_menu.addAction(self.palette_action)
        self.more_menu.addAction(self.layout_action)
        self.more_menu.addSeparator()
        for label, callback in (
            ("閱讀與寫作偏好…", window._open_preferences),
            ("匯出 PDF…", window._export_pdf),
            ("Mermaid 工作區…", window._open_mermaid_workspace),
            ("重新載入", window._reload_current),
        ):
            self.more_menu.addAction(label, callback)
        self._set_typewriter(self.typewriter_action.isChecked(), persist=False)
        self._set_paragraph(self.paragraph_action.isChecked(), persist=False)
        self.sync()

    def _set_typewriter(self, value, *, persist=True):
        if persist:
            self.settings.setValue("writing_typewriter", bool(value))
        setter = getattr(self.window._editor, "set_typewriter_scroll", None)
        if setter:
            setter(value)

    def _set_paragraph(self, value, *, persist=True):
        if persist:
            self.settings.setValue("writing_focus_paragraph", bool(value))
        setter = getattr(self.window._editor, "set_focus_paragraph", None)
        if setter:
            setter(value)

    def set_compact(self, value):
        self.compact = bool(value)
        self.settings.setValue(
            "workspace_layout", "compact" if self.compact else "expanded"
        )
        self.sync()

    def _select_mode(self, index):
        mode = self.mode.itemData(index)
        if mode == view_mode.PREVIEW:
            self.window._request_view_mode(mode)
        elif mode == view_mode.SPLIT:
            self.window._open_source_editor(mode)
        elif self.backend.currentData() == edit_backend.WYSIWYG_BACKEND:
            self.window._open_office_editor()
        else:
            self.window._open_source_editor(view_mode.EDIT)
        self.sync()

    def _select_backend(self, index):
        if self.backend.itemData(index) == edit_backend.WYSIWYG_BACKEND:
            self.window._open_office_editor()
        else:
            self.window._open_source_editor(view_mode.EDIT)
        self.sync()

    def save(self):
        self.set_save_state("saving")
        if not self.window._save_edits():
            self.set_save_state("failed")

    def set_save_state(self, state):
        self._saving = state == "saving"
        self._save_failed = state == "failed"
        self.sync()

    def sync(self):
        window = self.window
        window._reading_tools.sync_status()
        if self._document_key != window._active_path:
            self._document_key = window._active_path
            self._saving = self._save_failed = False
        editing = window._edit_mode
        markdown = window._current_kind == "markdown"
        office = editing and window._active_edit_backend == edit_backend.WYSIWYG_BACKEND
        limited = bool((window._active_editor_state() or {}).get("limited_source"))
        self.mode.setCurrentIndex(max(0, self.mode.findData(window._view_mode)))
        self.mode.setEnabled(markdown)
        self.backend.setVisible(editing and markdown and window._view_mode != view_mode.SPLIT)
        self.backend.setEnabled(not limited)
        self.backend.setToolTip("大文件使用完整來源編輯" if limited else "選擇寫作編輯器")
        if editing:
            self.backend.setCurrentIndex(1 if office else 0)
        window._reading_mode_combo.hide()
        window._editor_mode_badge.setVisible(not self.compact and markdown and editing)
        window._export_btn.setVisible(not self.compact)
        window._mermaid_btn.setVisible(not self.compact and markdown and editing)
        self.outline_action.setEnabled(editing and markdown and not office)
        if not (editing and markdown and not office):
            outline = getattr(window, "_writing_outline", None)
            if outline is not None:
                outline.close()
        self.typewriter_action.setEnabled(editing and not office)
        self.paragraph_action.setEnabled(editing and not office)
        dirty = bool(window._current_file and (
            (editing and window._editor.is_modified()) or window._active_tab_parked_dirty()
        ))
        self.save_button.setVisible(window._current_kind in {"markdown", "text"})
        self.save_button.setEnabled(editing and not self._saving)
        from .drafts import is_draft
        draft = is_draft(window, window._current_file)
        label = ("儲存中…" if self._saving else "儲存失敗，重試" if self._save_failed
                 else "儲存草稿…" if draft else "儲存變更" if dirty else "已儲存")
        self.save_button.setText(label)
        self.save_button.setAccessibleName(label + " (Ctrl+S)")
        self.save_button.setToolTip("將目前文件寫入磁碟 (Ctrl+S)")
        name = window._current_file.name if window._current_file else "Markdown Viewer"
        self.focus_title.setText(f"{'專注寫作' if editing else '專注閱讀'} · {name}")
        if self.focused:
            for widget, _visible in self.saved_visibility:
                widget.hide()

    def toggle_focus(self, _checked=False):
        window = self.window
        if not self.focused:
            window._leave_agents_only()
            widgets = (window.menuBar(), window._toolbar, window._panel,
                       window._tab_strip, window._properties_dock,
                       window._reading_tools.dock, window.statusBar())
            self.saved_visibility = [(widget, not widget.isHidden()) for widget in widgets]
            self.focused = True
            for widget, _visible in self.saved_visibility:
                widget.hide()
            self.focus_rail.show()
        else:
            self.focused = False
            self.focus_rail.hide()
            for widget, visible in self.saved_visibility:
                widget.setVisible(visible)
            self.saved_visibility = []
        self.focus_action.setChecked(self.focused)
        self.sync()

    def show_palette(self):
        window = self.window
        window._update_native_edit_actions()
        window._update_format_menu_actions()
        menus = [action.menu() for action in window.menuBar().actions() if action.menu()]
        entries = action_entries(menus)
        recent = self.settings.value("recent_commands", []) or []
        if isinstance(recent, str):
            recent = [recent]
        ranks = {label: index for index, label in enumerate(recent)}
        entries.sort(key=lambda entry: ranks.get(entry[0], len(ranks)))
        dialog = CommandPalette(entries, window)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_action:
            action = dialog.selected_action
            label = next((label for label, candidate in entries if candidate is action), "")
            self.settings.setValue(
                "recent_commands", [label] + [item for item in recent if item != label][:9])
            QTimer.singleShot(0, window, lambda: action.trigger() if action.isEnabled() else None)
