import json
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QCloseEvent, QShortcut, QTextCursor
from PySide6.QtWidgets import QPushButton, QWidget

from app import export_actions
from app import window as window_mod

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


class _Bridge(QObject):
    added = Signal(object)
    changed = Signal(object)
    removed = Signal(object)
    clicked = Signal(object)
    orphansReported = Signal(object)
    taskToggled = Signal(object)


class _FakeRenderer(QWidget):
    active_anchor_changed = Signal(str)
    wikilink_clicked = Signal(str)
    local_doc_clicked = Signal(str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self.bridge = _Bridge()
        self.loaded_paths = []
        self.empty_shown = False
        self._zoom = 1.0
        self._scroll_y = 0
        self._side_notes_visible = False
        self._on_headings_ready = on_headings_ready
        self.find_calls = []
        self.queued_find = None
        self.text_renders = []
        self.ratio_calls = []

    def set_annotation_side_notes_visible(self, visible):
        self._side_notes_visible = bool(visible)

    def set_zoom(self, factor):
        self._zoom = float(factor)
        return self._zoom

    def set_theme(self, _theme_name):
        pass

    def load_file(self, path, scroll_y=None):
        self.loaded_paths.append(Path(path))
        self._scroll_y = int(scroll_y or 0)
        if self._on_headings_ready:
            self._on_headings_ready([(1, Path(path).stem, Path(path).stem)])

    def show_empty(self):
        self.empty_shown = True

    def set_annotations(self, _annotations):
        pass

    def reload_current(self):
        pass

    def scroll_y(self):
        return self._scroll_y

    def set_scroll_y(self, value):
        self._scroll_y = int(value)

    def find_next(self, _text):
        pass

    def find_prev(self, _text):
        pass

    def find_text(self, text, result_callback=None):
        self.find_calls.append(text)

    def find_text_after_load(self, text):
        self.queued_find = text

    def cancel_pending_find(self):
        self.queued_find = None

    def scroll_to(self, _target):
        pass

    def scroll_to_ratio(self, ratio):
        self.ratio_calls.append(ratio)

    def select_annotation(self, _ann_id):
        pass

    def scroll_to_annotation(self, _ann_id):
        pass

    def render_markdown_text(
        self,
        text,
        theme="light",
        title="preview",
        base_url=None,
        scroll_ratio=None,
    ):
        self.text_renders.append(
            {"text": text, "theme": theme, "scroll_ratio": scroll_ratio}
        )

    def export_pdf(self, *_args, **_kwargs):
        pass

    def content_size(self, callback):
        callback((800, 1200))


class _FakePdfView(QWidget):
    page_changed = Signal(int)
    search_count_changed = Signal(int)
    highlight_requested = Signal(object)
    highlight_delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded = []

    def load(self, path):
        self.loaded.append(Path(path))
        return True

    def is_locked(self):
        return False

    def outline(self):
        return []

    def set_highlights(self, _highlights):
        pass

    def restore_page(self, _page):
        pass

    def current_page(self):
        return 0

    def set_zoom_factor(self, _factor):
        pass

    def apply_theme(self, _theme):
        pass

    def jump_to_page(self, _page):
        pass

    def search_next(self):
        pass

    def search_prev(self):
        pass

    def search(self, _text):
        pass

    def clear_search(self):
        pass

    def set_pen_mode(self, _enabled):
        pass

    def set_pen_color(self, _color):
        pass

    def reveal(self, *_args):
        pass


class _Noop:
    def __getattr__(self, _name):
        def _method(*_args, **_kwargs):
            return None

        return _method


class _Recent(_Noop):
    def __init__(self):
        self._paths = []
        self.active_tag = None

    def add(self, path):
        self._paths.append(path)

    def paths(self):
        return list(self._paths)

    def set_tag_filter(self, tag):
        self.active_tag = tag


class _FileBrowser(_Noop):
    def __init__(self):
        self.active_tag = None
        self.open_folder = False

    def set_tag_filter(self, tag):
        self.active_tag = tag

    def has_open_folder(self):
        return self.open_folder

    def refresh_libraries(self):
        pass


class _Tags(_Noop):
    def __init__(self):
        self.active_tag = None

    def set_active(self, tag):
        self.active_tag = tag


class _FakePanel(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.close_btn = QPushButton()
        self.toc = _Noop()
        self.file_browser = _FileBrowser()
        self.recent = _Recent()
        self.annotations = _Noop()
        self.backlinks = _Noop()
        self.pdf_notes = _Noop()
        self.pdf_highlights = _Noop()
        self.tags = _Tags()
        self.current_tab = None
        self.search_opened = False

    def apply_theme(self, _theme):
        pass

    def show_pdf_notes(self, _show):
        pass

    def set_annotations_enabled(self, _enabled):
        pass

    def switch_to(self, index):
        self.current_tab = index

    def show_search(self):
        self.search_opened = True


class _FakeTagIndex:
    def __init__(self):
        self.updates = []

    def all_tags(self):
        return []

    def tag_counts(self):
        return []

    def update(self, *args, **kwargs):
        self.updates.append((args, kwargs))

    def files_with_tag(self, _tag):
        return []


@pytest.fixture(autouse=True)
def _window_fakes(monkeypatch):
    monkeypatch.setattr(window_mod, "RendererView", _FakeRenderer)
    monkeypatch.setattr(window_mod, "PdfView", _FakePdfView)
    monkeypatch.setattr(window_mod, "LeftPanel", _FakePanel)
    monkeypatch.setattr(window_mod, "TagIndex", _FakeTagIndex)
    monkeypatch.setattr(window_mod.QTimer, "singleShot", staticmethod(lambda *a: None))
    monkeypatch.setattr(window_mod.MainWindow, "_refresh_tags_panel", lambda self: None)
    monkeypatch.setattr(window_mod.MainWindow, "_refresh_link_index", lambda self, force=False: None)


@pytest.fixture(autouse=True)
def _clean_settings(tmp_path, monkeypatch):
    settings_path = tmp_path / "settings.ini"

    def isolated_settings(*_args, **_kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(window_mod, "QSettings", isolated_settings)
    monkeypatch.setattr(window_mod.session_state, "QSettings", isolated_settings)
    settings = isolated_settings()
    keys = [
        "geometry",
        "open_tabs",
        "active_tab",
        "last_file",
        "content_zoom",
        "recent_files",
        "pdf_last_pages",
    ]
    for key in keys:
        settings.remove(key)
    yield


@pytest.fixture
def md_files(tmp_path):
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("# First\n\nAlpha", encoding="utf-8")
    second.write_text("# Second\n\nBeta", encoding="utf-8")
    return first, second


@pytest.fixture
def make_window(qapp):
    windows = []

    def _make():
        win = window_mod.MainWindow()
        windows.append(win)
        return win

    yield _make
    for win in reversed(windows):
        win.close()


def test_open_path_adds_tab_and_reuses_existing(make_window, md_files):
    first, second = md_files
    win = make_window()

    win.open_path(str(first))
    assert win._tab_bar.count() == 1
    assert win._tab_bar.tabData(0) == str(first)
    assert win._renderer.loaded_paths[-1] == first

    win.open_path(str(second))
    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 1

    win.open_path(str(first))
    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 0
    assert win._renderer.loaded_paths[-1] == first


def test_graph_view_is_modeless_and_does_not_change_document_tabs(
    make_window, md_files, qapp
):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    tab_count = win._tab_bar.count()

    win._open_graph_view()
    qapp.processEvents()

    assert win._graph_window is not None
    assert win._graph_window.isVisible()
    assert win._graph_window.isModal() is False
    assert win._tab_bar.count() == tab_count
    assert any(
        action.text().startswith("筆記關聯圖")
        for action in win.findChildren(window_mod.QAction)
    )
    assert any(
        shortcut.key().toString() == "Ctrl+G"
        for shortcut in win.findChildren(QShortcut)
    )
    win._graph_window.close()


def test_save_refreshes_an_open_graph_view(make_window, tmp_path, monkeypatch):
    note = tmp_path / "graph.md"
    note.write_text("[[Before]]", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    initial_index = window_mod.LinkIndex()
    initial_index.build([(note, note.read_text(encoding="utf-8"))])
    win._link_index = initial_index
    win._open_graph_view()

    def rebuild(force=False):
        assert force is True
        index = window_mod.LinkIndex()
        index.build([(note, note.read_text(encoding="utf-8"))])
        win._on_link_index_ready(index)

    monkeypatch.setattr(win, "_refresh_link_index", rebuild)
    win._toggle_edit_mode()
    win._editor.selectAll()
    win._editor.insertPlainText("[[After]]")

    assert win._save_edits() is True
    assert {node.label for node in win._graph_window.graph.nodes if node.ghost} == {
        "After"
    }
    win._graph_window.close()


def test_tag_selection_filters_recent_and_files_and_chooses_target_tab(make_window):
    win = make_window()
    win._panel.file_browser.open_folder = True

    win._on_tag_selected("focus")

    assert win._panel.recent.active_tag == "focus"
    assert win._panel.file_browser.active_tag == "focus"
    assert win._panel.tags.active_tag == "focus"
    assert win._panel.current_tab == 0

    win._on_tag_selected("")
    assert win._panel.recent.active_tag == ""
    assert win._panel.file_browser.active_tag == ""
    assert win._panel.current_tab == 0

    win._panel.file_browser.open_folder = False
    win._on_tag_selected("focus")
    assert win._panel.current_tab == 1


def test_body_tags_update_when_markdown_is_opened_and_saved(make_window, tmp_path):
    note = tmp_path / "tags.md"
    note.write_text("# Heading\ntext #opened and `#hidden`", encoding="utf-8")
    win = make_window()

    win.open_path(str(note))
    assert win._tag_index.updates[-1][1]["body_tags"] == ["opened"]

    win._toggle_edit_mode()
    win._editor.selectAll()
    win._editor.insertPlainText("saved #updated")
    assert win._save_edits() is True
    assert win._tag_index.updates[-1][1]["body_tags"] == ["updated"]


def test_global_search_opens_sidebar_and_focuses_search_panel(make_window):
    win = make_window()
    win._sidebar_open = False
    win._panel.hide()

    win._open_global_search()

    assert win._sidebar_open is True
    assert win._panel.isVisibleTo(win) is True
    assert win._panel.search_opened is True


def test_global_search_result_opens_file_and_starts_document_search(
    make_window, md_files
):
    first, _second = md_files
    win = make_window()

    win._open_global_search_result(str(first), "Alpha", 3)

    assert win._active_path == str(first)
    assert win._search_bar.isVisibleTo(win) is True
    assert win._search_input.text() == "Alpha"
    assert win._renderer.find_calls[-1] == "Alpha"
    assert win._renderer.queued_find == "Alpha"


def test_switching_tabs_loads_the_selected_document(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    win._renderer.set_scroll_y(37)
    win._tab_bar.setCurrentIndex(0)

    assert win._active_path == str(first)
    assert win._renderer.loaded_paths[-1] == first
    assert win._tab_state[str(second)]["scroll"] == 37


def test_closing_tabs_removes_state_and_shows_empty(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    win._on_tab_close(1)
    assert win._tab_bar.count() == 1
    assert str(second) not in win._tab_state

    win._on_tab_close(0)
    assert win._tab_bar.count() == 0
    assert win._current_file is None
    assert win._renderer.empty_shown is True


def test_detach_moves_tab_to_new_window(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    win._detach_tab(1)

    detached = [w for w in window_mod._DETACHED_WINDOWS if w is not win]
    assert win._tab_bar.count() == 1
    assert win._tab_bar.tabData(0) == str(first)
    assert len(detached) == 1
    assert detached[0]._is_detached is True
    assert detached[0]._tab_bar.tabData(0) == str(second)
    assert detached[0]._renderer.loaded_paths[-1] == second

    for detached_win in detached:
        detached_win.close()


def test_session_persists_and_restores_tabs(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted() is True

    settings = window_mod.QSettings(_ORG, _APP)
    assert json.loads(settings.value("open_tabs")) == [str(first), str(second)]
    assert int(settings.value("active_tab")) == 1

    restored = make_window()
    restored.restore_last_session()
    assert restored._tab_bar.count() == 2
    assert restored._tab_bar.currentIndex() == 1
    assert restored._renderer.loaded_paths[-1] == second


def test_export_guards_do_not_open_dialogs(make_window, md_files, monkeypatch):
    first, _second = md_files
    pdf = first.with_suffix(".pdf")
    pdf.write_bytes(b"%PDF-1.4\n")
    calls = []
    monkeypatch.setattr(
        export_actions.QFileDialog,
        "getSaveFileName",
        lambda *a, **k: calls.append("dialog") or ("", ""),
    )

    win = make_window()
    win.open_path(str(first))
    win._edit_mode = True
    win._export_pdf()
    win._export_pptx()
    win._export_docx()

    win._edit_mode = False
    win.open_path(str(pdf))
    win._export_pdf()
    win._export_pptx()
    win._export_docx()

    assert calls == []


def test_browser_migration_repoints_open_tabs_and_active_file(
    make_window, md_files
):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    renamed = second.with_name("renamed.md")
    second.rename(renamed)
    win._on_browser_paths_migrated({str(second): str(renamed)})

    assert win._tab_bar.tabData(1) == str(renamed)
    assert win._tab_bar.tabText(1) == "renamed.md"
    assert win._active_path == str(renamed)
    assert win._current_file == renamed
    assert str(renamed) in win._tab_state
    assert str(second) not in win._tab_state


def test_browser_delete_closes_matching_tab(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))

    second.unlink()
    win._on_browser_paths_deleted([str(second)])

    assert win._tab_bar.count() == 1
    assert win._tab_bar.tabData(0) == str(first)
    assert str(second) not in win._tab_state


def test_open_path_ipc_entry_adds_to_existing_window(make_window, md_files):
    first, second = md_files
    win = make_window()

    win.open_path(str(first))
    win.open_path(str(second))
    win.open_path(str(second))

    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 1
    assert [win._tab_bar.tabData(i) for i in range(2)] == [str(first), str(second)]


# --- daily notes and note templates (階段 3b) ---
def test_daily_note_creates_then_reopens_same_tab_in_edit_mode(
    make_window, tmp_path
):
    daily_folder = tmp_path / "new" / "Daily Notes"
    template = tmp_path / "daily.md"
    template.write_text("# {{title}}\n{{date}} {{time}}", encoding="utf-8")
    settings = window_mod.QSettings(_ORG, _APP)
    settings.setValue("daily_notes_folder", str(daily_folder))
    settings.setValue("daily_note_template", str(template))
    win = make_window()
    now = datetime(2026, 7, 11, 7, 6)

    assert any(
        action.text().startswith("開啟今日筆記")
        for action in win.findChildren(window_mod.QAction)
    )
    assert any(
        shortcut.key().toString() == "Ctrl+D"
        for shortcut in win.findChildren(QShortcut)
    )

    win._open_daily_note(now)

    note = daily_folder / "2026-07-11.md"
    assert note.read_text(encoding="utf-8") == "# 2026-07-11\n2026-07-11 07:06"
    assert win._current_file == note
    assert win._view_mode == "edit"
    assert win._tab_bar.count() == 1

    win._open_daily_note(now)
    assert win._current_file == note
    assert win._view_mode == "edit"
    assert win._tab_bar.count() == 1


def test_insert_template_from_configured_folder_at_cursor(
    make_window, md_files, tmp_path, monkeypatch
):
    first, _second = md_files
    templates = tmp_path / "Templates"
    templates.mkdir()
    template = templates / "Meeting.md"
    template.write_text(
        "{{title}} @ {{date}} {{time}}",
        encoding="utf-8",
    )
    window_mod.QSettings(_ORG, _APP).setValue("templates_folder", str(templates))
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getItem",
        staticmethod(lambda *args, **kwargs: ("Meeting.md", True)),
    )
    win = make_window()
    win.open_path(str(first))
    win._toggle_edit_mode()
    cursor = win._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    win._editor.setTextCursor(cursor)

    win._insert_template(now=datetime(2026, 7, 11, 16, 4))

    assert win._editor.toPlainText().endswith("first @ 2026-07-11 16:04")
    assert win._editor.is_modified() is True
    win._editor.mark_saved()  # avoid an interactive save prompt during teardown


def test_insert_template_missing_folder_is_graceful(
    make_window, md_files, tmp_path, monkeypatch
):
    first, _second = md_files
    window_mod.QSettings(_ORG, _APP).setValue(
        "templates_folder", str(tmp_path / "missing")
    )
    messages = []
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "information",
        staticmethod(lambda *args, **kwargs: messages.append(args[2])),
    )
    win = make_window()
    win.open_path(str(first))
    win._toggle_edit_mode()

    win._insert_template(now=datetime(2026, 7, 11, 16, 4))

    assert messages == ["範本資料夾不存在，或資料夾內沒有 Markdown 範本。"]


# --- view modes: preview / edit / split (階段 2a) ---
def test_ctrl_e_toggles_preview_and_plain_edit(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    assert win._view_mode == "preview"

    win._toggle_edit_mode()  # Ctrl+E
    assert win._view_mode == "edit"
    assert win._edit_mode is True
    assert win._stack.currentWidget() is win._editor_split
    assert win._edit_preview.isHidden()  # plain edit: no preview pane
    assert win._editor.toPlainText() == first.read_text(encoding="utf-8")

    win._toggle_edit_mode()  # Ctrl+E again -> back to preview
    assert win._view_mode == "preview"
    assert win._edit_mode is False
    assert win._stack.currentWidget() is win._renderer


def test_ctrl_shift_e_enters_split_directly_and_back(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))

    win._toggle_split_mode()  # Ctrl+Shift+E straight from preview
    assert win._view_mode == "split"
    assert win._stack.currentWidget() is win._editor_split
    assert not win._edit_preview.isHidden()
    # Entering split renders the current buffer immediately.
    assert win._edit_preview.text_renders
    assert (
        win._edit_preview.text_renders[-1]["text"]
        == first.read_text(encoding="utf-8")
    )

    win._toggle_split_mode()  # Ctrl+Shift+E again -> back to preview
    assert win._view_mode == "preview"
    assert win._stack.currentWidget() is win._renderer


def test_toolbar_button_cycles_three_modes(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))

    win._cycle_view_mode()
    assert win._view_mode == "edit"
    win._cycle_view_mode()
    assert win._view_mode == "split"
    assert not win._edit_preview.isHidden()
    win._cycle_view_mode()
    assert win._view_mode == "preview"


def test_edit_and_split_modes_unavailable_for_pdf(make_window, md_files):
    first, _second = md_files
    pdf = first.parent / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    win = make_window()
    win.open_path(str(pdf))

    win._toggle_edit_mode()
    win._toggle_split_mode()
    win._cycle_view_mode()

    assert win._view_mode == "preview"
    assert win._edit_mode is False
    assert win._stack.currentWidget() is win._pdf_view


def test_typing_debounces_rerender_only_in_split_mode(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    win._toggle_split_mode()
    win._edit_preview.text_renders.clear()

    win._editor.setPlainText("# changed")
    assert win._preview_timer.isActive()  # debounce armed, no render yet
    assert win._preview_timer.isSingleShot()
    assert 300 <= win._preview_timer.interval() <= 500
    assert win._edit_preview.text_renders == []

    win._preview_timer.stop()
    win._update_preview()  # what the debounce timeout fires
    assert win._edit_preview.text_renders[-1]["text"] == "# changed"

    # Plain edit mode: no preview pane, so typing must not arm the timer.
    win._set_view_mode("edit")
    assert win._preview_timer.isActive() is False
    win._editor.setPlainText("# more")
    assert win._preview_timer.isActive() is False


def test_save_in_split_mode_writes_file_and_stays_in_split(
    make_window, md_files
):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    win._toggle_split_mode()

    win._editor.selectAll()
    win._editor.insertPlainText("# New content")  # typing marks it modified
    assert win._editor.is_modified()
    assert win._save_edits() is True

    assert first.read_text(encoding="utf-8") == "# New content"
    assert win._editor.is_modified() is False
    assert win._view_mode == "split"  # saving does not leave split mode


def test_tab_switch_from_split_discards_and_resets_to_preview(
    make_window, md_files, monkeypatch
):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))
    win._toggle_split_mode()
    win._editor.selectAll()
    win._editor.insertPlainText("unsaved")
    assert win._editor.is_modified()
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: window_mod.QMessageBox.StandardButton.Discard
        ),
    )

    win._tab_bar.setCurrentIndex(0)

    assert win._view_mode == "preview"
    assert win._active_path == str(first)
    assert win._stack.currentWidget() is win._renderer
    assert second.read_text(encoding="utf-8") == "# Second\n\nBeta"  # untouched


def test_tab_switch_cancel_keeps_split_mode_and_tab(
    make_window, md_files, monkeypatch
):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))
    win._toggle_split_mode()
    win._editor.selectAll()
    win._editor.insertPlainText("unsaved")
    assert win._editor.is_modified()
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: window_mod.QMessageBox.StandardButton.Cancel
        ),
    )

    win._tab_bar.setCurrentIndex(0)

    assert win._view_mode == "split"
    assert win._active_path == str(second)
    assert win._tab_bar.currentIndex() == 1
    assert win._editor.toPlainText() == "unsaved"


def test_editor_scroll_sync_only_drives_preview_in_split(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))

    win._sync_preview_scroll()  # preview mode: must not touch the preview
    assert win._edit_preview.ratio_calls == []

    win._toggle_split_mode()
    win._edit_preview.ratio_calls.clear()
    win._sync_preview_scroll()
    assert win._edit_preview.ratio_calls == [0.0]  # offscreen bar has no range
    # The synced ratio is what the next debounced render restores.
    win._update_preview()
    assert win._edit_preview.text_renders[-1]["scroll_ratio"] == 0.0
