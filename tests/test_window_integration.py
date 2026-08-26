import codecs
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget

from app import edit_backend
from app import export_actions
from app import md_table
from app import window as window_mod
from app.shortcuts import WINDOW_SHORTCUTS

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


class _Bridge(QObject):
    added = Signal(object)
    changed = Signal(object)
    removed = Signal(object)
    clicked = Signal(object)
    orphansReported = Signal(object)
    taskToggled = Signal(object)
    inlineEditStateChanged = Signal(bool)
    unhandledEscape = Signal(int)
    wysiwygEditRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.inline_edit_handlers = {}

    def set_inline_edit_handlers(
        self,
        fetch=None,
        commit=None,
        paste_image=None,
        commit_table=None,
        serialize_table=None,
        reload=None,
    ):
        self.inline_edit_handlers = {
            "fetch": fetch,
            "commit": commit,
            "paste_image": paste_image,
            "commit_table": commit_table,
            "serialize_table": serialize_table,
            "reload": reload,
        }


class _FakeRenderer(QWidget):
    active_anchor_changed = Signal(str)
    wikilink_clicked = Signal(str)
    local_doc_clicked = Signal(str)
    translate_requested = Signal(str)

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
        self.reload_calls = 0
        self.inline_edit_enabled = True
        self.search_escape_generation = 0
        self.preview_double_click_mode = "inline"

    def set_annotation_side_notes_visible(self, visible):
        self._side_notes_visible = bool(visible)

    def set_inline_edit_enabled(self, enabled):
        self.inline_edit_enabled = bool(enabled)

    def set_preview_double_click_mode(self, mode):
        self.preview_double_click_mode = "wysiwyg" if mode == "wysiwyg" else "inline"

    def set_search_escape_generation(self, generation):
        self.search_escape_generation = int(generation)

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
        self.reload_calls += 1

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
    outline_ready = Signal(int, object, object)
    zoom_changed = Signal(float)
    translate_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loaded = []
        self.outline_calls = 0
        self._load_generation = 0
        self.zoom_calls = []
        self.pending_wheel_zoom = None
        self.flush_wheel_zoom_calls = 0
        self.flush_wheel_zoom_contexts = []

    def load(self, path):
        self.loaded.append(Path(path))
        self._load_generation += 1
        return True

    def is_locked(self):
        return False

    def outline(self):
        self.outline_calls += 1
        return []

    def load_generation(self):
        return self._load_generation

    def set_highlights(self, _highlights):
        pass

    def restore_page(self, _page):
        pass

    def current_page(self):
        return 0

    def set_zoom_factor(self, factor, anchor=None):
        # Match PdfView: an explicit programmatic zoom supersedes a queued
        # wheel frame, even when the factor itself is unchanged.
        self.pending_wheel_zoom = None
        self.zoom_calls.append((float(factor), anchor))

    def flush_pending_wheel_zoom(self):
        self.flush_wheel_zoom_calls += 1
        owner = self.window()
        self.flush_wheel_zoom_contexts.append(
            (
                getattr(owner, "_current_file", None),
                getattr(owner, "_current_kind", None),
            )
        )
        factor = self.pending_wheel_zoom
        self.pending_wheel_zoom = None
        if factor is not None:
            self.set_zoom_factor(factor)
            self.zoom_changed.emit(factor)

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
    # A modal warning can never be dismissed on the offscreen test platform.
    # Stub it so an unexpected save failure is reported by assertions instead
    # of turning the test run into an unbounded wait.
    monkeypatch.setattr(window_mod.QMessageBox, "warning", lambda *args: None)
    # Same for the discard-edits confirmation: closing a window with a dirty
    # editor during fixture teardown must not block on a modal question.
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.Discard,
    )


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
    assert win._tab_bar.tabText(0) == "first.md"
    assert win._tab_bar.mode_badge_text(0) == "MD"
    assert win._renderer.loaded_paths[-1] == first

    win.open_path(str(second))
    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 1

    win.open_path(str(first))
    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 0
    assert win._renderer.loaded_paths[-1] == first


def test_markdown_editor_menu_routes_follow_current_file_kind(
    make_window, md_files, tmp_path
):
    markdown, _second = md_files
    text_file = tmp_path / "plain.txt"
    pdf_file = tmp_path / "plain.pdf"
    text_file.write_text("plain text", encoding="utf-8")
    pdf_file.write_bytes(b"%PDF-1.4\n")
    win = make_window()

    win._update_native_edit_actions()
    assert not win._source_edit_action.isEnabled()
    assert not win._source_split_action.isEnabled()
    assert not win._office_edit_action.isEnabled()

    win.open_path(str(markdown))
    win._update_native_edit_actions()
    assert win._tab_bar.mode_badge(win._tab_bar.currentIndex()) == "markdown"
    assert win._source_edit_action.isEnabled()
    assert win._source_split_action.isEnabled()
    assert win._office_edit_action.isEnabled()

    win.open_path(str(text_file))
    win._update_native_edit_actions()
    assert win._tab_bar.tabText(win._tab_bar.currentIndex()) == "plain.txt"
    assert win._tab_bar.mode_badge(win._tab_bar.currentIndex()) is None
    assert not win._source_edit_action.isEnabled()
    assert not win._source_split_action.isEnabled()
    assert not win._office_edit_action.isEnabled()

    win.open_path(str(pdf_file))
    win._update_native_edit_actions()
    assert win._tab_bar.tabText(win._tab_bar.currentIndex()) == "plain.pdf"
    assert win._tab_bar.mode_badge(win._tab_bar.currentIndex()) is None
    assert not win._source_edit_action.isEnabled()
    assert not win._source_split_action.isEnabled()
    assert not win._office_edit_action.isEnabled()


def test_pdf_outline_is_async_and_stale_results_do_not_replace_current_toc(
    make_window, md_files, tmp_path
):
    markdown, _second = md_files
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.4\n")
    second_pdf.write_bytes(b"%PDF-1.4\n")
    win = make_window()
    updates = []
    win._panel.toc.update_outline = lambda entries: updates.append(list(entries))

    win.open_path(str(first_pdf))
    first_generation = win._pdf_view.load_generation()
    assert win._pdf_view.outline_calls == 0
    assert updates == [[]]

    first_entries = [(1, "First", 0)]
    win._pdf_view.outline_ready.emit(
        first_generation, first_pdf, first_entries
    )
    assert updates[-1] == first_entries

    win.open_path(str(second_pdf))
    second_generation = win._pdf_view.load_generation()
    assert second_generation > first_generation
    assert updates[-1] == []
    before = len(updates)

    win._pdf_view.outline_ready.emit(
        first_generation, first_pdf, [(1, "Stale generation", 0)]
    )
    win._pdf_view.outline_ready.emit(
        second_generation, first_pdf, [(1, "Wrong path", 0)]
    )
    assert len(updates) == before

    second_entries = [(1, "Second", 0)]
    win._pdf_view.outline_ready.emit(
        second_generation, second_pdf, second_entries
    )
    assert updates[-1] == second_entries

    win._reload_current()
    reloaded_generation = win._pdf_view.load_generation()
    assert reloaded_generation > second_generation
    assert updates[-1] == []
    before = len(updates)
    win._pdf_view.outline_ready.emit(
        second_generation, second_pdf, [(1, "Stale same path", 0)]
    )
    assert len(updates) == before
    win._pdf_view.outline_ready.emit(
        reloaded_generation, second_pdf, [(1, "Reloaded", 0)]
    )
    assert updates[-1] == [(1, "Reloaded", 0)]

    win.open_path(str(markdown))
    before = len(updates)
    win._pdf_view.outline_ready.emit(
        reloaded_generation, second_pdf, [(1, "PDF after Markdown", 0)]
    )
    assert len(updates) == before
    assert win._pdf_view.outline_calls == 0


def test_pdf_wheel_zoom_syncs_shared_zoom_and_saved_preference(
    make_window, tmp_path
):
    settings = window_mod.QSettings(_ORG, _APP)
    settings.setValue("content_zoom", 1.4)
    pdf = tmp_path / "zoom.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    win = make_window()
    assert win._pdf_view.zoom_calls[-1] == (1.4, None)
    win.open_path(str(pdf))

    # Real PdfView applies the anchored zoom locally before emitting the signal.
    win._pdf_view.set_zoom_factor(1.6)
    win._pdf_view.zoom_changed.emit(1.6)

    assert win._content_zoom == pytest.approx(1.6)
    assert win._renderer._zoom == pytest.approx(1.4)
    assert win._edit_preview._zoom == pytest.approx(1.4)
    assert win._pdf_view.zoom_calls[-1] == (1.6, None)
    assert float(settings.value("content_zoom")) == pytest.approx(1.4)
    assert win.statusBar().currentMessage() == "縮放：160%"
    assert win._pdf_zoom_sync_timer.isActive()

    win._commit_pdf_wheel_zoom()

    assert win._renderer._zoom == pytest.approx(1.6)
    assert win._edit_preview._zoom == pytest.approx(1.6)
    assert win._pdf_view.zoom_calls[-1] == (1.6, None)
    assert float(settings.value("content_zoom")) == pytest.approx(1.6)
    assert win._pdf_zoom_sync_timer.isActive() is False


def test_keyboard_zoom_uses_fast_discrete_stops(make_window):
    settings = window_mod.QSettings(_ORG, _APP)
    win = make_window()

    win._zoom_in()
    win._zoom_in()
    win._zoom_in()

    assert win._content_zoom == pytest.approx(1.5)
    assert win._renderer._zoom == pytest.approx(1.5)
    assert win._edit_preview._zoom == pytest.approx(1.5)
    assert float(settings.value("content_zoom")) == pytest.approx(1.5)

    win._apply_zoom(1.17)
    win._zoom_out()
    assert win._content_zoom == pytest.approx(1.1)
    win._zoom_in()
    assert win._content_zoom == pytest.approx(1.25)


def test_idle_zoom_commit_does_not_cancel_a_new_pdf_wheel_frame(
    make_window, tmp_path
):
    pdf = tmp_path / "zoom-race.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    settings = window_mod.QSettings(_ORG, _APP)
    win = make_window()
    win.open_path(str(pdf))

    win._pdf_view.set_zoom_factor(1.1)
    win._pdf_view.zoom_changed.emit(1.1)
    # A new PdfView frame arrives just before the older 120 ms sync fires.
    win._pdf_view.pending_wheel_zoom = 1.2
    win._commit_pdf_wheel_zoom()

    assert win._pdf_view.pending_wheel_zoom == pytest.approx(1.2)
    assert float(settings.value("content_zoom")) == pytest.approx(1.1)

    win._pdf_view.flush_pending_wheel_zoom()
    win._commit_pdf_wheel_zoom()
    assert win._pdf_view.pending_wheel_zoom is None
    assert win._content_zoom == pytest.approx(1.2)
    assert float(settings.value("content_zoom")) == pytest.approx(1.2)


def test_pending_pdf_wheel_zoom_flushes_before_switch_and_reload(
    make_window, md_files, tmp_path
):
    markdown, _second_markdown = md_files
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.4\n")
    second_pdf.write_bytes(b"%PDF-1.4\n")
    settings = window_mod.QSettings(_ORG, _APP)
    win = make_window()
    win.open_path(str(first_pdf))

    win._pdf_view.pending_wheel_zoom = 1.6
    win.open_path(str(second_pdf))
    assert win._pdf_view.flush_wheel_zoom_calls == 1
    assert win._pdf_view.flush_wheel_zoom_contexts[-1] == (
        first_pdf,
        "pdf",
    )
    assert win._content_zoom == pytest.approx(1.6)
    assert float(settings.value("content_zoom")) == pytest.approx(1.6)
    assert win._pending_pdf_wheel_zoom is None
    assert win._pdf_zoom_sync_timer.isActive() is False

    win._pdf_view.pending_wheel_zoom = 1.7
    win._reload_current()
    assert win._pdf_view.flush_wheel_zoom_calls == 2
    assert win._pdf_view.flush_wheel_zoom_contexts[-1] == (
        second_pdf,
        "pdf",
    )
    assert win._content_zoom == pytest.approx(1.7)
    assert float(settings.value("content_zoom")) == pytest.approx(1.7)

    win._pdf_view.pending_wheel_zoom = 1.8
    win.open_path(str(markdown))
    assert win._pdf_view.flush_wheel_zoom_calls == 3
    assert win._pdf_view.flush_wheel_zoom_contexts[-1] == (
        second_pdf,
        "pdf",
    )
    assert win._current_kind == "markdown"
    assert win._content_zoom == pytest.approx(1.8)
    assert win._renderer._zoom == pytest.approx(1.8)
    assert win._edit_preview._zoom == pytest.approx(1.8)
    assert float(settings.value("content_zoom")) == pytest.approx(1.8)
    assert win._pending_pdf_wheel_zoom is None
    assert win._pdf_zoom_sync_timer.isActive() is False


def test_pending_pdf_wheel_zoom_flushes_before_empty_state_and_close(
    make_window, tmp_path
):
    pdf = tmp_path / "zoom.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    settings = window_mod.QSettings(_ORG, _APP)

    emptied = make_window()
    emptied.open_path(str(pdf))
    emptied._pdf_view.pending_wheel_zoom = 1.6
    emptied._on_tab_close(0)
    assert emptied._current_kind == ""
    assert emptied._pdf_view.flush_wheel_zoom_calls == 1
    assert float(settings.value("content_zoom")) == pytest.approx(1.6)
    assert emptied._pending_pdf_wheel_zoom is None
    assert emptied._pdf_zoom_sync_timer.isActive() is False

    closed = make_window()
    closed.open_path(str(pdf))
    closed._pdf_view.pending_wheel_zoom = 1.7
    closed.close()
    assert closed._pdf_view.flush_wheel_zoom_calls == 1
    assert float(settings.value("content_zoom")) == pytest.approx(1.7)
    assert closed._pending_pdf_wheel_zoom is None
    assert closed._pdf_zoom_sync_timer.isActive() is False


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


def test_registered_shortcuts_and_menu_hints_match_registry(make_window):
    win = make_window()
    portable = QKeySequence.SequenceFormat.PortableText
    expected = [
        (spec.command_id, QKeySequence(sequence).toString(portable))
        for spec in WINDOW_SHORTCUTS
        for sequence in spec.sequences
    ]
    actual = [
        (
            shortcut.property("commandId"),
            shortcut.key().toString(portable),
        )
        for shortcut in win._registered_shortcuts
    ]
    assert Counter(actual) == Counter(expected)
    assert len(actual) == len(expected)
    expected_context = {
        spec.command_id: (
            Qt.ShortcutContext.WidgetShortcut
            if spec.owner == "editor"
            else Qt.ShortcutContext.WindowShortcut
        )
        for spec in WINDOW_SHORTCUTS
    }
    assert all(
        shortcut.context() == expected_context[shortcut.property("commandId")]
        for shortcut in win._registered_shortcuts
    )
    object_names = [shortcut.objectName() for shortcut in win._registered_shortcuts]
    assert len(object_names) == len(set(object_names))
    assert all(name.startswith("shortcut.") for name in object_names)
    assert "Esc" not in {sequence for _command_id, sequence in actual}

    actions = [
        action
        for action in win.findChildren(window_mod.QAction)
        if action.data() in {spec.command_id for spec in WINDOW_SHORTCUTS}
    ]
    action_counts = Counter(action.data() for action in actions)
    assert action_counts == Counter(spec.command_id for spec in WINDOW_SHORTCUTS)
    for spec in WINDOW_SHORTCUTS:
        action = next(action for action in actions if action.data() == spec.command_id)
        assert action.shortcut().isEmpty()
        assert action.text().endswith(f"\t{spec.menu_hint}")


def test_search_input_shift_enter_goes_previous_and_escape_closes(
    make_window, qapp
):
    win = make_window()
    previous = []
    next_result = []
    cancelled = []
    win._search_input.previous_requested.connect(lambda: previous.append(True))
    win._search_input.returnPressed.connect(lambda: next_result.append(True))
    win._search_input.cancel_requested.connect(lambda: cancelled.append(True))
    win._search_bar.show()
    win._search_input.show()
    win._search_input.setFocus()

    QTest.keyClick(
        win._search_input,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ShiftModifier,
    )
    assert previous == [True]
    assert next_result == []

    QTest.keyClick(win._search_input, Qt.Key.Key_Return)
    assert next_result == [True]

    QTest.keyClick(win._search_input, Qt.Key.Key_Escape)
    assert cancelled == [True]
    assert win._search_bar.isHidden()


def test_context_escape_closes_wikilink_popup_before_open_search(
    make_window, qapp
):
    win = make_window()
    win.show()
    win._stack.setCurrentWidget(win._editor_split)
    win._editor.set_content("[[ro")
    cursor = win._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    win._editor.setTextCursor(cursor)
    win._editor.set_wikilink_candidates(["Roadmap"])
    win._editor.setFocus()
    win._editor._show_wikilink_completions()
    qapp.processEvents()

    popup = win._editor._completer.popup()
    assert popup.isVisible()
    win._search_bar.show()
    QTest.keyClick(win._editor, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert popup.isVisible() is False
    assert win._search_bar.isVisible()


def test_unhandled_escape_closes_search_from_other_main_window_controls(
    make_window, qapp
):
    win = make_window()
    win.show()
    win._search_bar.show()
    win._theme_btn.setFocus()
    qapp.processEvents()

    QTest.keyClick(win._theme_btn, Qt.Key.Key_Escape)
    assert win._search_bar.isHidden()


def test_web_preview_unhandled_escape_closes_open_search(make_window):
    win = make_window()
    win._search_bar.show()
    win._set_search_escape_enabled(True)
    generation = win._active_search_escape_generation

    win._renderer.bridge.unhandledEscape.emit(generation)

    assert win._search_bar.isHidden()

    win._editor_search_bar.show()
    win._set_search_escape_enabled(True)
    generation = win._active_search_escape_generation
    win._edit_preview.bridge.unhandledEscape.emit(generation)
    assert win._editor_search_bar.isHidden()


def test_stale_web_escape_cannot_close_a_new_search(make_window):
    win = make_window()
    win._search_bar.show()
    win._set_search_escape_enabled(True)
    stale_generation = win._active_search_escape_generation

    win._close_search()
    win._search_bar.show()
    win._set_search_escape_enabled(True)
    current_generation = win._active_search_escape_generation

    win._renderer.bridge.unhandledEscape.emit(stale_generation)
    assert win._search_bar.isHidden() is False

    win._renderer.bridge.unhandledEscape.emit(current_generation)
    assert win._search_bar.isHidden()


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


def test_tag_selection_is_scoped_to_tags_tab_only(make_window):
    win = make_window()

    win._on_tag_selected("focus")

    # The tag node is highlighted in the 標籤 tree (set_active)...
    assert win._panel.tags.active_tag == "focus"
    # ...but selecting a tag must NOT filter the 檔案 / 最近 tabs: those keep
    # showing every file, so tags never hide files in the other views.
    assert win._panel.recent.active_tag is None
    assert win._panel.file_browser.active_tag is None
    # ...and it must not switch tabs either.
    assert win._panel.current_tab is None

    win._on_tag_selected("")
    assert win._panel.tags.active_tag == ""
    assert win._panel.recent.active_tag is None
    assert win._panel.file_browser.active_tag is None
    assert win._panel.current_tab is None


def test_doc_tag_change_updates_rows_incrementally_not_full_rescan(
    make_window, tmp_path
):
    note = tmp_path / "note.md"
    note.write_text("# note", encoding="utf-8")
    win = make_window()

    calls = {"refresh": 0, "update": []}
    win._panel.file_browser.refresh_libraries = lambda: calls.__setitem__(
        "refresh", calls["refresh"] + 1
    )
    win._panel.file_browser.update_file_tags = lambda paths: calls[
        "update"
    ].append(list(paths))

    win._on_doc_tags_changed([note])

    # A tag edit must take the cheap incremental path: only the affected file
    # rows are refreshed, never a full disk-rescanning refresh_libraries().
    assert calls["refresh"] == 0
    assert calls["update"] == [[note]]


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


def test_save_failure_returns_without_blocking_on_modal_warning(
    make_window, tmp_path, monkeypatch
):
    note = tmp_path / "save-error.md"
    note.write_text("before", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    win._toggle_edit_mode()
    win._editor.selectAll()
    win._editor.insertPlainText("after")
    warnings = []
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    monkeypatch.setattr(
        window_mod,
        "atomic_write_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    assert win._save_edits() is False
    assert len(warnings) == 1
    assert win._editor.is_modified() is True
    assert note.read_text(encoding="utf-8") == "before"


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


def test_duplicate_tab_names_disambiguate_keep_dirty_marker_and_recompute(
    make_window, tmp_path
):
    first = tmp_path / "project-a" / "docs" / "README.md"
    second = tmp_path / "project-b" / "docs" / "README.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("# first", encoding="utf-8")
    second.write_text("# second", encoding="utf-8")
    win = make_window()

    win.open_path(str(first))
    assert win._tab_bar.tabText(0) == "README.md"
    assert win._tab_bar.mode_badge_text(0) == "MD"
    win.open_path(str(second))
    assert [win._tab_bar.tabText(index) for index in range(2)] == [
        "README.md · project-a/docs",
        "README.md · project-b/docs",
    ]
    assert [win._tab_bar.mode_badge_text(index) for index in range(2)] == [
        "MD",
        "MD",
    ]
    assert win._tab_bar.tabToolTip(0) == f"{first}\n工作區：Markdown"
    assert win._tab_bar.tabToolTip(1) == f"{second}\n工作區：Markdown"
    assert win._tab_bar.accessibleTabName(0).startswith("Markdown 工作區，")

    win._edit_mode = True
    win._editor.document().setModified(True)
    win._update_dirty_ui()
    assert (
        win._tab_bar.tabText(1)
        == "● README.md · project-b/docs"
    )

    win._editor.document().setModified(False)
    win._on_tab_close(1)
    assert win._tab_bar.tabText(0) == "README.md"
    assert win._tab_bar.mode_badge_text(0) == "MD"


def test_all_tabs_menu_selects_live_path_after_reorder(
    make_window, tmp_path, qapp
):
    paths = []
    win = make_window()
    for index in range(14):
        path = tmp_path / f"CoilSync_封閉LAN_需求分析_{index:02}.md"
        path.write_text(f"# {index}", encoding="utf-8")
        paths.append(path)
        win.open_path(str(path))

    win.resize(900, 640)
    win.show()
    qapp.processEvents()
    assert win._tab_bar.has_overflow() is True
    assert win._tab_strip.overflow_button.isVisible() is True

    menu = win._tab_strip.build_tabs_menu()
    first_action = next(
        action for action in menu.actions() if action.data() == str(paths[0])
    )
    win._tab_bar.moveTab(0, win._tab_bar.count() - 1)
    first_action.trigger()
    qapp.processEvents()

    assert win._tab_bar.tabData(win._tab_bar.currentIndex()) == str(paths[0])
    assert win._active_path == str(paths[0])
    assert win._renderer.loaded_paths[-1] == paths[0]
    active = win._tab_bar.tabRect(win._tab_bar.currentIndex())
    visible = win._tab_bar.visible_tabs_rect()
    assert active.left() >= visible.left()
    assert active.right() <= visible.right()

    loaded_before_move = list(win._renderer.loaded_paths)
    active_index = win._tab_bar.currentIndex()
    win._tab_bar.moveTab(active_index, 0)
    qapp.processEvents()
    assert win._renderer.loaded_paths == loaded_before_move
    assert win._tab_bar.tabData(win._tab_bar.currentIndex()) == str(paths[0])
    active = win._tab_bar.tabRect(win._tab_bar.currentIndex())
    visible = win._tab_bar.visible_tabs_rect()
    assert active.left() >= visible.left()
    assert active.right() <= visible.right()


def test_tab_context_menu_closes_right_and_other_tabs(make_window, tmp_path):
    paths = []
    win = make_window()
    for index in range(4):
        path = tmp_path / f"note-{index}.md"
        path.write_text(f"# {index}", encoding="utf-8")
        paths.append(path)
        win.open_path(str(path))

    right_menu = win._build_tab_context_menu(1)
    close_right = next(
        action for action in right_menu.actions() if action.text() == "關閉右側分頁"
    )
    assert close_right.isEnabled() is True
    assert any(action.text() == "移至新視窗" for action in right_menu.actions())
    close_right.trigger()
    assert [win._tab_bar.tabData(index) for index in range(2)] == [
        str(paths[0]),
        str(paths[1]),
    ]

    win.open_path(str(paths[2]))
    win.open_path(str(paths[3]))
    others_menu = win._build_tab_context_menu(1)
    close_others = next(
        action for action in others_menu.actions() if action.text() == "關閉其他分頁"
    )
    close_others.trigger()
    assert win._tab_bar.count() == 1
    assert win._tab_bar.tabData(0) == str(paths[1])
    assert win._active_path == str(paths[1])

    only_menu = win._build_tab_context_menu(0)
    assert next(
        action for action in only_menu.actions() if action.text() == "關閉其他分頁"
    ).isEnabled() is False
    assert next(
        action for action in only_menu.actions() if action.text() == "關閉右側分頁"
    ).isEnabled() is False


def test_bulk_tab_close_cancel_keeps_every_tab(make_window, tmp_path, monkeypatch):
    paths = []
    win = make_window()
    for index in range(4):
        path = tmp_path / f"dirty-{index}.md"
        path.write_text(f"# {index}", encoding="utf-8")
        paths.append(path)
        win.open_path(str(path))

    win._edit_mode = True
    win._editor.document().setModified(True)
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: window_mod.QMessageBox.StandardButton.Cancel,
    )
    menu = win._build_tab_context_menu(1)
    next(
        action for action in menu.actions() if action.text() == "關閉右側分頁"
    ).trigger()

    assert [win._tab_bar.tabData(index) for index in range(4)] == [
        str(path) for path in paths
    ]
    assert win._active_path == str(paths[-1])

    # Let fixture teardown close the window without reopening the modal prompt.
    win._editor.document().setModified(False)
    win._edit_mode = False


def test_stale_close_others_action_does_not_close_remaining_tabs(
    make_window, tmp_path
):
    paths = []
    win = make_window()
    for index in range(3):
        path = tmp_path / f"stale-{index}.md"
        path.write_text(f"# {index}", encoding="utf-8")
        paths.append(path)
        win.open_path(str(path))

    menu = win._build_tab_context_menu(1)
    close_others = next(
        action for action in menu.actions() if action.text() == "關閉其他分頁"
    )
    assert win._close_tab_by_path(str(paths[1])) is True

    close_others.trigger()

    assert [win._tab_bar.tabData(index) for index in range(2)] == [
        str(paths[0]),
        str(paths[2]),
    ]


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


def test_detaching_active_pdf_carries_the_last_pending_wheel_zoom(
    make_window, md_files, tmp_path
):
    markdown, _second_markdown = md_files
    pdf = tmp_path / "detached.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    win = make_window()
    win.open_path(str(markdown))
    win.open_path(str(pdf))
    win._pdf_view.pending_wheel_zoom = 1.6
    existing_detached = set(window_mod._DETACHED_WINDOWS)

    win._detach_tab(1)

    detached = [
        w for w in window_mod._DETACHED_WINDOWS
        if w not in existing_detached and w is not win
    ]
    assert len(detached) == 1
    detached_win = detached[0]
    assert win._pdf_view.flush_wheel_zoom_calls >= 1
    assert detached_win._current_kind == "pdf"
    assert detached_win._content_zoom == pytest.approx(1.6)
    assert detached_win._pdf_view.zoom_calls[0] == (1.6, None)
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
    window_mod.session_state.remember_document_edit_backend(
        second, edit_backend.WYSIWYG_BACKEND
    )

    renamed = second.with_name("renamed.md")
    second.rename(renamed)
    win._on_browser_paths_migrated({str(second): str(renamed)})

    assert win._tab_bar.tabData(1) == str(renamed)
    assert win._tab_bar.tabText(1) == "renamed.md"
    assert win._tab_bar.mode_badge_text(1) == "MD"
    assert win._active_path == str(renamed)
    assert win._current_file == renamed
    assert str(renamed) in win._tab_state
    assert str(second) not in win._tab_state
    assert window_mod.session_state.load_document_edit_backend(second) is None
    assert window_mod.session_state.load_document_edit_backend(renamed) == (
        edit_backend.WYSIWYG_BACKEND
    )


def test_browser_delete_closes_matching_tab(make_window, md_files):
    first, second = md_files
    win = make_window()
    win.open_path(str(first))
    win.open_path(str(second))
    window_mod.session_state.remember_document_edit_backend(
        second, edit_backend.WYSIWYG_BACKEND
    )

    second.unlink()
    win._on_browser_paths_deleted([str(second)])

    assert win._tab_bar.count() == 1
    assert win._tab_bar.tabData(0) == str(first)
    assert str(second) not in win._tab_state
    assert window_mod.session_state.load_document_edit_backend(second) is None


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


def test_existing_daily_note_uses_its_remembered_source_editor(
    make_window, tmp_path
):
    daily_folder = tmp_path / "Daily Notes"
    daily_folder.mkdir()
    note = daily_folder / "2026-07-11.md"
    note.write_text("existing", encoding="utf-8")
    window_mod.QSettings(_ORG, _APP).setValue(
        "daily_notes_folder", str(daily_folder)
    )
    window_mod.session_state.remember_document_edit_backend(
        note, edit_backend.SOURCE_BACKEND
    )
    win = make_window()
    win._edit_backend = edit_backend.WYSIWYG_BACKEND

    win._open_daily_note(datetime(2026, 7, 11, 7, 6))

    assert win._current_file == note
    assert win._active_edit_backend == edit_backend.SOURCE_BACKEND
    assert win._stack.currentWidget() is win._editor_split


def test_existing_daily_note_uses_its_remembered_office_editor(
    make_window, tmp_path
):
    daily_folder = tmp_path / "Daily Notes"
    daily_folder.mkdir()
    note = daily_folder / "2026-07-11.md"
    note.write_text("existing", encoding="utf-8")
    window_mod.QSettings(_ORG, _APP).setValue(
        "daily_notes_folder", str(daily_folder)
    )
    window_mod.session_state.remember_document_edit_backend(
        note, edit_backend.WYSIWYG_BACKEND
    )
    win = make_window()
    win._edit_backend = edit_backend.SOURCE_BACKEND

    win._open_daily_note(datetime(2026, 7, 11, 7, 6))

    assert win._current_file == note
    assert win._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert win._stack.currentWidget() is win._wysiwyg_view


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


def test_recent_attachment_is_reimported_with_a_safe_link_for_another_note(
    make_window, tmp_path, monkeypatch
):
    first = tmp_path / "one" / "first.md"
    second = tmp_path / "two" / "second.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"# One\n")
    second.write_bytes(b"# Two\n")
    source = tmp_path / "shared manual.pdf"
    source.write_bytes(b"%PDF-test")
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: window_mod.QMessageBox.StandardButton.Discard
        ),
    )
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(source), "")),
    )
    win = make_window()
    win.open_path(str(first))
    win._toggle_edit_mode()

    win._insert_attachment_via_dialog()

    first_copy = first.parent / "assets" / source.name
    assert first_copy.is_file()
    expected_link = "[shared manual.pdf](assets/shared%20manual.pdf)"
    assert expected_link in win._editor.toPlainText()

    win.open_path(str(second))
    win._toggle_edit_mode()
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getItem",
        staticmethod(lambda *args, **kwargs: (list(args[3])[0], True)),
    )

    win._insert_recent_resource()

    second_copy = second.parent / "assets" / source.name
    assert second_copy.is_file()
    assert expected_link in win._editor.toPlainText()
    recent = win._recent_resource_entries()
    assert len(recent) == 1
    assert Path(recent[0].absolute_path) == first_copy.resolve()


# --- view modes: preview / edit / split (階段 2a) ---
def test_ctrl_e_toggles_preview_and_plain_edit(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
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
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
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


def test_top_right_utilities_are_grouped_and_theme_button_tracks_action(
    make_window, qapp
):
    win = make_window()
    win.resize(1000, 700)
    win.show()
    qapp.processEvents()

    controls = win._toolbar_utilities
    assert controls.objectName() == "toolbarUtilities"
    assert controls.size().width() == 85
    assert controls.size().height() == 38
    assert win._theme_btn.objectName() == "themeToggleButton"
    assert win._update_btn.objectName() == "updateButton"
    assert win._theme_btn.parent() is controls
    assert win._update_btn.parent() is controls
    assert win._theme_btn.geometry().right() < win._update_btn.geometry().left()
    assert win._theme_btn.property("iconName") == "moon"
    assert win._theme_btn.toolTip() == "切換為深色模式"
    assert win._theme_action.text() == "切換為深色模式"

    light_icon = win._theme_btn.icon().cacheKey()
    QTest.mouseClick(win._theme_btn, Qt.MouseButton.LeftButton)

    assert win._theme_name == "dark"
    assert win._theme_btn.property("iconName") == "sun"
    assert win._theme_btn.toolTip() == "切換為淺色模式"
    assert win._theme_btn.accessibleName() == "切換為淺色模式"
    assert win._theme_action.text() == "切換為淺色模式"
    assert win._theme_btn.icon().cacheKey() != light_icon
    assert window_mod.QSettings(_ORG, _APP).value("theme") == "dark"


def test_update_utility_invokes_one_manual_check_after_theme_refreshes(
    make_window, qapp, monkeypatch
):
    win = make_window()
    calls = []
    monkeypatch.setattr(
        win, "_check_for_updates", lambda manual: calls.append(manual)
    )
    win.show()
    qapp.processEvents()

    assert win._update_btn.property("updateState") == "idle"
    assert win._update_btn.property("iconName") == "circle-arrow-up"
    assert window_mod.VERSION in win._update_btn.toolTip()

    win._apply_theme()
    win._refresh_icons()
    QTest.mouseClick(win._update_btn, Qt.MouseButton.LeftButton)
    assert calls == [True]

    win._theme_btn.setFocus()
    QTest.keyClick(win._theme_btn, Qt.Key.Key_Tab)
    assert win._update_btn.hasFocus() is True
    QTest.keyClick(win._update_btn, Qt.Key.Key_Space)
    assert calls == [True, True]


def test_cached_update_badge_rechecks_or_opens_live_update(
    make_window, qapp, monkeypatch
):
    available_version = f"{int(window_mod.VERSION.split('.', 1)[0]) + 1}.0.0"
    window_mod.QSettings(_ORG, _APP).setValue(
        "available_update_version", available_version
    )
    win = make_window()
    checks = []
    prompts = []
    monkeypatch.setattr(
        win, "_check_for_updates", lambda manual: checks.append(manual)
    )
    monkeypatch.setattr(
        window_mod.update_flow,
        "prompt_for_update",
        lambda window, update: prompts.append((window, update)),
    )
    win.show()
    qapp.processEvents()

    assert win._toolbar_utilities.update_state == "available"
    assert win._update_btn.property("badgeVisible") is True
    assert f"v{available_version}" in win._update_btn.toolTip()

    # A cached version label has no installer metadata, so clicking refreshes it.
    QTest.mouseClick(win._update_btn, Qt.MouseButton.LeftButton)
    assert checks == [True]
    assert prompts == []

    live_update = object()
    win._available_update = live_update
    QTest.mouseClick(win._update_btn, Qt.MouseButton.LeftButton)
    assert checks == [True]
    assert prompts == [(win, live_update)]


def test_update_menu_action_is_disabled_while_checking_or_downloading(
    make_window, monkeypatch
):
    win = make_window()
    prompts = []
    win._available_update = object()
    monkeypatch.setattr(
        window_mod.update_flow,
        "prompt_for_update",
        lambda *args: prompts.append(args),
    )

    for state in ("checking", "downloading"):
        win._set_update_state(state)
        assert win._update_btn.isEnabled() is False
        assert win._update_action.isEnabled() is False
        win._update_action.trigger()
        win._on_update_button_clicked()
        assert prompts == []


def test_window_close_defers_once_until_running_update_finishes(
    make_window, qapp, monkeypatch
):
    class RunningUpdate(QObject):
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.running = True

        def isRunning(self):  # noqa: N802 (QThread-compatible fake)
            return self.running

    win = make_window()
    win.show()
    qapp.processEvents()
    close_checks = []
    monkeypatch.setattr(
        window_mod.session_state,
        "close_event",
        lambda _window, _event: close_checks.append(True) or True,
    )
    monkeypatch.setattr(
        window_mod.QTimer,
        "singleShot",
        staticmethod(lambda *args: args[-1]()),
    )
    quit_calls = []
    monkeypatch.setattr(
        window_mod.update_flow.QApplication,
        "quit",
        staticmethod(lambda: quit_calls.append(True)),
    )
    thread = RunningUpdate()
    win._update_check_thread = thread

    assert win.close() is False
    assert win.isHidden() is True
    assert win._update_close_pending is True
    assert win._deferred_update_close_approved is True
    assert close_checks == [True]

    thread.running = False
    thread.finished.emit()
    qapp.processEvents()

    assert win._update_close_pending is False
    assert win._deferred_update_close_approved is False
    assert close_checks == [True]
    assert quit_calls == [True]


@pytest.mark.parametrize(
    ("cached_version", "shows_badge"),
    [
        ("0.0.1", False),
        (window_mod.VERSION, False),
        (f"v{window_mod.VERSION}", False),
        ("garbage", False),
        ("garbage9999", False),
        ("1.26.0garbage", False),
        ("1..26", False),
        ("9999.0.0", True),
    ],
)
def test_cached_update_badge_only_keeps_semantically_newer_versions(
    make_window, cached_version, shows_badge
):
    settings = window_mod.QSettings(_ORG, _APP)
    settings.setValue("available_update_version", cached_version)

    win = make_window()

    assert (win._toolbar_utilities.update_state == "available") is shows_badge
    assert bool(win._cached_update_version) is shows_badge
    if shows_badge:
        assert settings.value("available_update_version") == cached_version.lstrip(
            "vV"
        )
    else:
        assert settings.contains("available_update_version") is False


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
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
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


def test_tab_switch_from_split_preserves_dirty_buffer_and_restores_it(
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
    questions = []
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "question",
        staticmethod(lambda *a, **k: questions.append((a, k))),
    )

    win._tab_bar.setCurrentIndex(0)

    assert win._view_mode == "preview"
    assert win._active_path == str(first)
    assert win._stack.currentWidget() is win._renderer
    assert second.read_text(encoding="utf-8") == "# Second\n\nBeta"  # untouched
    assert questions == []
    second_state = win._tab_state[str(second)]
    assert second_state["editor_document"].toPlainText() == "unsaved"
    assert second_state["editor_document"].isModified()

    win._tab_bar.setCurrentIndex(1)

    assert win._view_mode == "split"
    assert win._editor.toPlainText() == "unsaved"
    assert win._editor.is_modified()


def test_tab_switch_never_prompts_and_opens_target_tab(
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
    with monkeypatch.context() as context:
        context.setattr(
            window_mod.QMessageBox,
            "question",
            staticmethod(
                lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("tab switching must not prompt")
                )
            ),
        )
        win._tab_bar.setCurrentIndex(0)

    assert win._view_mode == "preview"
    assert win._active_path == str(first)
    assert win._tab_bar.currentIndex() == 0
    assert win._tab_state[str(second)]["editor_document"].toPlainText() == "unsaved"


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


# --- 加入標籤… quick-tag (檔案 tab file menu backing method) ---
def _use_real_tag_stores(monkeypatch, tmp_path):
    """Swap the faked TagIndex for a real one, isolated to *tmp_path*, so
    ``_add_tag_to_paths`` can be asserted end-to-end. Call before make_window().
    """
    from app.tag_colors import TagColorStore as _ColorStore
    from app.tag_index import TagIndex as _TagIndex

    monkeypatch.setattr(
        window_mod, "TagIndex", lambda: _TagIndex(tmp_path / "tags.json")
    )
    store = _ColorStore(path=tmp_path / "colors.json")
    monkeypatch.setattr(window_mod.TagColorStore, "load", lambda *a, **k: store)


def test_add_tag_to_paths_assigns_typed_tag_to_multiple_files(
    make_window, md_files, monkeypatch, tmp_path
):
    from app import doc_tags as doc_tags_facade

    first, second = md_files
    _use_real_tag_stores(monkeypatch, tmp_path)
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getItem",
        staticmethod(lambda *a, **k: ("quick", True)),
    )
    win = make_window()

    win._add_tag_to_paths([first, second])

    assert "quick" in doc_tags_facade.read_doc_tags(first)
    assert "quick" in doc_tags_facade.read_doc_tags(second)
    keys = win._tag_index.files_with_tag("quick")
    assert str(first.resolve()) in keys
    assert str(second.resolve()) in keys


def test_add_tag_to_paths_accepts_existing_tag(
    make_window, md_files, monkeypatch, tmp_path
):
    from app import doc_tags as doc_tags_facade

    first, second = md_files
    _use_real_tag_stores(monkeypatch, tmp_path)
    doc_tags_facade.write_doc_tags(first, ["focus"])  # 'focus' already exists
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getItem",
        staticmethod(lambda *a, **k: ("focus", True)),
    )
    win = make_window()

    win._add_tag_to_paths([second])

    assert "focus" in doc_tags_facade.read_doc_tags(second)
    assert str(second.resolve()) in win._tag_index.files_with_tag("focus")


def test_add_tag_to_paths_cancel_makes_no_change(
    make_window, md_files, monkeypatch, tmp_path
):
    from app import doc_tags as doc_tags_facade

    first, _second = md_files
    _use_real_tag_stores(monkeypatch, tmp_path)
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getItem",
        staticmethod(lambda *a, **k: ("ignored", False)),  # ok=False -> no-op
    )
    win = make_window()

    win._add_tag_to_paths([first])

    assert doc_tags_facade.read_doc_tags(first) == []
    assert win._tag_index.files_with_tag("ignored") == []


# --- inline editing of a preview block (assets/inline_edit.js <-> window) ---
def _inline_handlers(win):
    return win._renderer.bridge.inline_edit_handlers


def test_inline_edit_handlers_are_registered_on_the_preview_bridge(make_window):
    win = make_window()

    handlers = _inline_handlers(win)

    assert set(handlers) == {
        "fetch", "commit", "paste_image", "commit_table",
        "serialize_table", "reload",
    }
    assert all(callable(h) for h in handlers.values())


def test_inline_edit_fetch_returns_the_exact_source_lines(make_window, tmp_path):
    note = tmp_path / "inline.md"
    note.write_text("# Title\n\nalpha\nbeta\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))

    reply = _inline_handlers(win)["fetch"](2, 3)

    assert reply["ok"] is True
    assert reply["text"] == "alpha\nbeta"
    # The page hands this straight back on commit; see
    # _inline_edit_signature for why the text check alone is not enough.
    assert reply["sig"]


def test_inline_edit_fetch_rejects_a_range_past_the_end(make_window, tmp_path):
    note = tmp_path / "short.md"
    note.write_text("only line\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))

    assert _inline_handlers(win)["fetch"](40, 41)["ok"] is False


def test_inline_edit_commit_rewrites_the_file_and_rerenders(make_window, tmp_path):
    note = tmp_path / "commit.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    before = win._renderer.reload_calls

    result = _inline_handlers(win)["commit"](2, 2, "alpha", "alpha edited\nplus a line")

    assert result == {"ok": True}
    assert note.read_text(encoding="utf-8") == "# Title\n\nalpha edited\nplus a line\n"
    assert win._renderer.reload_calls == before + 1


def test_inline_edit_commit_preserves_crlf_line_endings(make_window, tmp_path):
    note = tmp_path / "crlf.md"
    note.write_bytes(b"# Title\r\n\r\nalpha\r\n")
    win = make_window()
    win.open_path(str(note))

    assert _inline_handlers(win)["commit"](2, 2, "alpha", "beta")["ok"] is True

    assert note.read_bytes() == b"# Title\r\n\r\nbeta\r\n"


def test_inline_edit_commit_preserves_the_original_encoding(make_window, tmp_path):
    note = tmp_path / "big5.md"
    note.write_bytes("# 標題\n\n段落\n".encode("cp950"))
    win = make_window()
    win.open_path(str(note))

    assert _inline_handlers(win)["commit"](2, 2, "段落", "改過的段落")["ok"] is True

    assert note.read_bytes().decode("cp950") == "# 標題\n\n改過的段落\n"


def test_inline_edit_commit_refuses_when_the_file_changed_underneath(
    make_window, tmp_path
):
    note = tmp_path / "stale.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    note.write_text("# Title\n\nsomeone else wrote this\n", encoding="utf-8")
    before = win._renderer.reload_calls

    result = _inline_handlers(win)["commit"](2, 2, "alpha", "my edit")

    assert result["ok"] is False
    # Untouched on disk, and deliberately *not* re-rendered: the page is
    # the only place the text the user typed still exists, so reloading to
    # "fix" the stale line numbers would destroy what needs saving.
    assert note.read_text(encoding="utf-8") == "# Title\n\nsomeone else wrote this\n"
    assert win._renderer.reload_calls == before


def test_inline_edit_is_refused_while_the_editor_owns_the_buffer(
    make_window, tmp_path
):
    note = tmp_path / "editing.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    win._toggle_edit_mode()
    assert win._edit_mode is True

    handlers = _inline_handlers(win)
    assert handlers["fetch"](2, 2)["ok"] is False
    assert handlers["commit"](2, 2, "alpha", "beta")["ok"] is False
    assert handlers["paste_image"]()["ok"] is False
    assert note.read_text(encoding="utf-8") == "# Title\n\nalpha\n"


def test_inline_edit_flag_follows_the_view_mode(make_window, tmp_path):
    note = tmp_path / "modes.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    assert win._renderer.inline_edit_enabled is True

    win._toggle_edit_mode()
    assert win._renderer.inline_edit_enabled is False

    win._toggle_edit_mode()  # back to preview
    assert win._renderer.inline_edit_enabled is True


def test_inline_edit_paste_image_saves_next_to_the_document(
    make_window, tmp_path, monkeypatch
):
    from PySide6.QtGui import QImage

    note = tmp_path / "paste.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))

    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    monkeypatch.setattr(
        window_mod.QGuiApplication,
        "clipboard",
        staticmethod(lambda: type("_Clip", (), {"image": lambda self: image})()),
    )

    result = _inline_handlers(win)["paste_image"]()

    assert result["ok"] is True
    assert result["link"].startswith("![](assets/image-")
    saved = list((tmp_path / "assets").glob("*.png"))
    assert len(saved) == 1


def test_inline_edit_paste_image_reports_an_empty_clipboard(
    make_window, tmp_path, monkeypatch
):
    from PySide6.QtGui import QImage

    note = tmp_path / "noimage.md"
    note.write_text("# Title\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    monkeypatch.setattr(
        window_mod.QGuiApplication,
        "clipboard",
        staticmethod(lambda: type("_Clip", (), {"image": lambda self: QImage()})()),
    )

    assert _inline_handlers(win)["paste_image"]() == {"ok": False, "error": "no-image"}
    assert not (tmp_path / "assets").exists()


def test_inline_edit_is_unavailable_without_an_open_document(make_window):
    win = make_window()

    handlers = _inline_handlers(win)
    assert handlers["fetch"](0, 0)["ok"] is False
    assert handlers["paste_image"]()["ok"] is False


def test_task_checkbox_write_back_still_works(make_window, tmp_path):
    note = tmp_path / "tasks.md"
    note.write_text("# Title\n\n- [ ] one\n- [ ] two\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))

    win._on_task_toggled(2, True)

    assert note.read_text(encoding="utf-8") == "# Title\n\n- [x] one\n- [ ] two\n", (
        win.statusBar().currentMessage()
    )


def test_inline_edit_commit_refreshes_the_tag_index(make_window, tmp_path):
    note = tmp_path / "indexed.md"
    note.write_text("# Heading\n\ntext #before\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    assert win._tag_index.updates[-1][1]["body_tags"] == ["before"]

    _inline_handlers(win)["commit"](2, 2, "text #before", "text #after")

    assert win._tag_index.updates[-1][1]["body_tags"] == ["after"]


def test_inline_edit_commit_falls_back_to_utf8_when_the_encoding_cannot_hold_it(
    make_window, tmp_path
):
    note = tmp_path / "downgrade.md"
    note.write_bytes("# 標題\n\n段落\n".encode("cp950"))
    win = make_window()
    win.open_path(str(note))

    result = _inline_handlers(win)["commit"](2, 2, "段落", "段落 ✅")
    assert result["ok"] is True, (result, win.statusBar().currentMessage())

    assert note.read_bytes().decode("utf-8") == "# 標題\n\n段落 ✅\n"


# --- the table grid editor on top of the same commit path ------------------
_TABLE_DOC = (
    "# Title\n"
    "\n"
    "| Name | Qty |\n"
    "|---|--:|\n"
    "| apple | 3 |\n"
    "\n"
    "tail paragraph\n"
)
_TABLE_SOURCE = "| Name | Qty |\n|---|--:|\n| apple | 3 |"


def _table_note(tmp_path):
    note = tmp_path / "table.md"
    note.write_text(_TABLE_DOC, encoding="utf-8")
    return note


def test_inline_edit_fetch_hands_a_table_block_its_model(make_window, tmp_path):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))

    reply = _inline_handlers(win)["fetch"](2, 4)

    assert reply["ok"] is True
    assert reply["text"] == _TABLE_SOURCE
    assert reply["table"] == {
        "headers": ["Name", "Qty"],
        "aligns": ["", "right"],
        "rows": [["apple", "3"]],
        "indent": "",
    }


def test_inline_edit_fetch_leaves_a_plain_block_without_a_table(
    make_window, tmp_path
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))

    reply = _inline_handlers(win)["fetch"](6, 6)

    # No "table" key at all, so the page keeps using the raw textarea.
    assert reply["text"] == "tail paragraph"
    assert "table" not in reply


def test_inline_edit_commit_table_writes_the_normalized_table(
    make_window, tmp_path
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    handlers = _inline_handlers(win)
    model = handlers["fetch"](2, 4)["table"]
    model["rows"].append(["banana", "12"])
    before = win._renderer.reload_calls

    result = handlers["commit_table"](2, 4, _TABLE_SOURCE, json.dumps(model))

    assert result == {"ok": True}, win.statusBar().currentMessage()
    # The table is rewritten in the normalized layout; every other line of the
    # document is untouched.
    assert note.read_text(encoding="utf-8") == (
        "# Title\n"
        "\n"
        "| Name   | Qty  |\n"
        "| ------ | ---: |\n"
        "| apple  | 3    |\n"
        "| banana | 12   |\n"
        "\n"
        "tail paragraph\n"
    )
    assert win._renderer.reload_calls == before + 1


def test_inline_edit_commit_table_refuses_a_stale_block(make_window, tmp_path):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    handlers = _inline_handlers(win)
    model = handlers["fetch"](2, 4)["table"]
    model["rows"][0][0] = "edited in the grid"
    note.write_text(
        _TABLE_DOC.replace("apple", "someone else"), encoding="utf-8"
    )
    on_disk = note.read_text(encoding="utf-8")

    result = handlers["commit_table"](2, 4, _TABLE_SOURCE, json.dumps(model))

    assert result == {"ok": False, "error": "stale"}
    assert note.read_text(encoding="utf-8") == on_disk


def test_inline_edit_commit_table_rejects_a_model_it_cannot_use(
    make_window, tmp_path
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    handlers = _inline_handlers(win)

    for payload in (
        "not json at all",
        "[1, 2, 3]",
        json.dumps({"rows": [["a"]]}),
        json.dumps({"headers": "Name", "rows": []}),
        # An empty header list is refused on purpose: it would serialize to ""
        # and wipe the whole table off the page.
        json.dumps({"headers": [], "aligns": [], "rows": [], "indent": ""}),
    ):
        assert handlers["commit_table"](2, 4, _TABLE_SOURCE, payload) == {
            "ok": False,
            "error": "bad-model",
        }, payload

    assert note.read_text(encoding="utf-8") == _TABLE_DOC


def test_inline_edit_commit_table_is_refused_while_the_editor_owns_the_buffer(
    make_window, tmp_path
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    handlers = _inline_handlers(win)
    model = handlers["fetch"](2, 4)["table"]
    win._toggle_edit_mode()

    result = handlers["commit_table"](2, 4, _TABLE_SOURCE, json.dumps(model))

    assert result == {"ok": False, "error": "unavailable"}
    assert note.read_text(encoding="utf-8") == _TABLE_DOC


# --- the document-revision half of the optimistic lock -----------------------
# Two byte-identical tables: whatever the block text says, it says it about
# either of them, so the text comparison alone cannot tell which one a write
# was aimed at. Only the file revision can.
_TWIN_TABLE = "| A | B |\n| --- | --- |\n| 1 | 2 |"
_TWIN_DOC = _TWIN_TABLE + "\n\n" + _TWIN_TABLE + "\n"
# Four lines: exactly the height of one table plus its blank separator, so the
# insert lines the *first* table up with the second one's line numbers.
_ONE_BLOCK = "pad\npad\npad\npad\n"
_EDITED_MODEL = json.dumps(
    {
        "headers": ["A", "B"],
        "aligns": ["", ""],
        "rows": [["EDITED", "2"]],
        "indent": "",
    }
)


def _twin_note(tmp_path):
    note = tmp_path / "twins.md"
    note.write_text(_TWIN_DOC, encoding="utf-8")
    return note


def test_inline_edit_fetch_pins_the_revision_it_read_the_line_numbers_from(
    make_window, tmp_path
):
    note = _twin_note(tmp_path)
    win = make_window()
    win.open_path(str(note))

    second = _inline_handlers(win)["fetch"](4, 6)

    assert second["text"] == _TWIN_TABLE
    # Same text as the first table, so the signature is the only thing that
    # distinguishes this reply from one about lines 0..2.
    assert second["sig"] == _inline_handlers(win)["fetch"](0, 2)["sig"]
    assert second["sig"]


def test_inline_edit_commit_table_refuses_a_write_aimed_at_a_twin_table(
    make_window, tmp_path
):
    note = _twin_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    reply = _inline_handlers(win)["fetch"](4, 6)
    # Someone prepends exactly one block's worth of lines, so lines 4..6 now
    # hold the *first* table -- byte-identical to what the page was shown.
    note.write_text(_ONE_BLOCK + _TWIN_DOC, encoding="utf-8")
    before = win._renderer.reload_calls

    result = _inline_handlers(win)["commit_table"](
        4, 6, reply["text"], _EDITED_MODEL, reply["sig"]
    )

    assert result == {"ok": False, "error": "stale"}
    # Neither table touched, and the page left standing so the user can still
    # copy what they built out of the grid.
    assert note.read_text(encoding="utf-8") == _ONE_BLOCK + _TWIN_DOC
    assert win._renderer.reload_calls == before


def test_without_a_signature_a_twin_table_swallows_the_write(make_window, tmp_path):
    """Documents the hole the signature closes -- not desired behaviour.

    An empty signature is the backward-compatible path, and this is the price
    of it: the text check passes against the wrong table and the edit lands
    there. If this ever stops being true the signature plumbing has become
    redundant; until then it is the only defence.
    """
    note = _twin_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    reply = _inline_handlers(win)["fetch"](4, 6)
    note.write_text(_ONE_BLOCK + _TWIN_DOC, encoding="utf-8")

    result = _inline_handlers(win)["commit_table"](
        4, 6, reply["text"], _EDITED_MODEL, ""
    )

    assert result == {"ok": True}
    lines = note.read_text(encoding="utf-8").split("\n")
    # The write landed on the first table; the one the user was editing is
    # still sitting there untouched.
    assert "EDITED" in lines[6]
    assert "\n".join(lines[8:11]) == _TWIN_TABLE


def test_inline_edit_commit_refuses_a_stale_signature_even_when_the_text_matches(
    make_window, tmp_path
):
    note = tmp_path / "sig.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    reply = _inline_handlers(win)["fetch"](2, 2)
    # A foreign write that leaves line 2 alone: the text check would sail
    # straight through, but the line numbers came from a different revision.
    note.write_text("# Title\n\nalpha\nappended elsewhere\n", encoding="utf-8")

    result = _inline_handlers(win)["commit"](
        2, 2, reply["text"], "my edit", reply["sig"]
    )

    assert result == {"ok": False, "error": "stale"}
    assert note.read_text(encoding="utf-8") == (
        "# Title\n\nalpha\nappended elsewhere\n"
    )


def test_inline_edit_commit_skips_the_check_without_a_signature(
    make_window, tmp_path
):
    note = tmp_path / "nosig.md"
    note.write_text("# Title\n\nalpha\n", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))

    # A page injected before the signature existed sends "", and must not be
    # locked out of editing entirely.
    result = _inline_handlers(win)["commit"](2, 2, "alpha", "my edit", "")

    assert result == {"ok": True}
    assert note.read_text(encoding="utf-8") == "# Title\n\nmy edit\n"


# --- serialize-only, for the grid's "switch to source" button ----------------
def test_inline_edit_serialize_table_renders_markdown_without_writing(
    make_window, tmp_path
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    before = note.read_text(encoding="utf-8")

    result = _inline_handlers(win)["serialize_table"](_EDITED_MODEL)

    assert result["ok"] is True
    assert md_table.parse_table(result["text"]) == json.loads(_EDITED_MODEL)
    # The button only swaps the editor; nothing is saved until Ctrl+Enter.
    assert note.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "payload", ["{", "[]", '{"headers": []}', '{"headers": "A"}']
)
def test_inline_edit_serialize_table_rejects_a_model_it_cannot_use(
    make_window, tmp_path, payload
):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))

    result = _inline_handlers(win)["serialize_table"](payload)

    assert result == {"ok": False, "error": "bad-model"}


def test_inline_edit_reload_rerenders_on_the_pages_request(make_window, tmp_path):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    win._preview_editing = True
    before = win._renderer.reload_calls

    result = _inline_handlers(win)["reload"]()

    assert result == {"ok": True}
    assert win._renderer.reload_calls == before + 1
    # The page that set the flag is gone with the re-render.
    assert win._preview_editing is False


# --- guarding an open preview editor against a re-render --------------------
def _answer_question(monkeypatch, button):
    """Patch the confirmation dialog and record that it was put up."""
    asked = []

    def question(*args, **kwargs):
        asked.append(args[1] if len(args) > 1 else "")
        return button

    monkeypatch.setattr(window_mod.QMessageBox, "question", question)
    return asked


def _window_with_an_open_grid(make_window, tmp_path):
    note = _table_note(tmp_path)
    win = make_window()
    win.open_path(str(note))
    # What assets/inline_edit.js reports when it opens an editor.
    win._renderer.bridge.inlineEditStateChanged.emit(True)
    assert win._preview_editing is True
    return win


def test_reloading_asks_before_discarding_an_open_preview_edit(
    make_window, tmp_path, monkeypatch
):
    win = _window_with_an_open_grid(make_window, tmp_path)
    asked = _answer_question(monkeypatch, window_mod.QMessageBox.StandardButton.No)
    before = win._renderer.reload_calls

    win._reload_current()

    assert asked
    assert win._renderer.reload_calls == before


def test_reloading_goes_ahead_once_the_preview_edit_is_given_up(
    make_window, tmp_path, monkeypatch
):
    win = _window_with_an_open_grid(make_window, tmp_path)
    _answer_question(monkeypatch, window_mod.QMessageBox.StandardButton.Yes)
    before = win._renderer.reload_calls

    win._reload_current()

    assert win._renderer.reload_calls == before + 1
    assert win._preview_editing is False


def test_entering_the_editor_asks_before_discarding_an_open_preview_edit(
    make_window, tmp_path, monkeypatch
):
    win = _window_with_an_open_grid(make_window, tmp_path)
    asked = _answer_question(monkeypatch, window_mod.QMessageBox.StandardButton.No)

    win._enter_edit_mode()

    assert asked
    assert win._edit_mode is False


def test_external_change_asks_before_discarding_an_open_preview_edit(
    make_window, tmp_path, monkeypatch
):
    win = _window_with_an_open_grid(make_window, tmp_path)
    asked = _answer_question(monkeypatch, window_mod.QMessageBox.StandardButton.No)
    before = win._renderer.reload_calls

    win._prompt_external_change()

    assert asked
    assert win._renderer.reload_calls == before


def test_external_change_reloads_once_the_preview_edit_is_given_up(
    make_window, tmp_path, monkeypatch
):
    win = _window_with_an_open_grid(make_window, tmp_path)
    _answer_question(monkeypatch, window_mod.QMessageBox.StandardButton.Yes)
    before = win._renderer.reload_calls

    win._prompt_external_change()

    assert win._renderer.reload_calls == before + 1
    assert win._preview_editing is False


def test_closing_the_preview_editor_clears_the_flag(make_window, tmp_path):
    win = _window_with_an_open_grid(make_window, tmp_path)

    win._renderer.bridge.inlineEditStateChanged.emit(False)

    assert win._preview_editing is False


# ---------------- plain-text (.txt) documents ----------------
def test_open_txt_shows_literal_content_in_editor(make_window, tmp_path):
    note = tmp_path / "plain.txt"
    content = "# not a heading\n*stars*\n[[not a link]]\n"
    note.write_text(content, encoding="utf-8")
    win = make_window()

    win.open_path(str(note))

    assert win._current_kind == "text"
    assert win._edit_mode is True
    assert win._stack.currentWidget() is win._editor_split
    assert win._editor.toPlainText() == content
    assert win._editor._plain_text_mode is True
    assert str(note) in win._panel.recent.paths()
    # Editor-only: the mode toggles and export stay off, search stays on.
    assert win._edit_btn.isEnabled() is False
    assert win._export_btn.isEnabled() is False
    assert win._search_btn.isEnabled() is True
    win._toggle_edit_mode()
    assert win._edit_mode is True
    assert win._stack.currentWidget() is win._editor_split
    win._toggle_split_mode()
    assert win._view_mode == window_mod.view_mode.EDIT


def test_txt_edit_save_preserves_crlf_and_skips_preview_reload(
    make_window, tmp_path
):
    note = tmp_path / "crlf.txt"
    note.write_bytes(b"one\r\ntwo\r\n")
    win = make_window()
    win.open_path(str(note))
    assert win._editor.toPlainText() == "one\ntwo\n"

    win._editor.setPlainText("one\ntwo\nthree\n")
    assert win._save_edits() is True

    assert note.read_bytes() == b"one\r\ntwo\r\nthree\r\n"
    assert win._renderer.reload_calls == 0
    assert win._edit_mode is True  # saving never leaves the text editor


def test_txt_utf8_bom_round_trips_without_leaking_into_editor(
    make_window, tmp_path
):
    note = tmp_path / "bom.txt"
    note.write_bytes(codecs.BOM_UTF8 + "哈囉\n".encode("utf-8"))
    win = make_window()
    win.open_path(str(note))

    assert "﻿" not in win._editor.toPlainText()
    assert win._editor.toPlainText() == "哈囉\n"

    win._editor.setPlainText("哈囉 世界\n")
    assert win._save_edits() is True
    assert note.read_bytes() == codecs.BOM_UTF8 + "哈囉 世界\n".encode("utf-8")


def test_txt_utf16_bom_decodes_and_saves_utf16(make_window, tmp_path):
    note = tmp_path / "wide.txt"
    note.write_bytes("寬字元\r\n".encode("utf-16"))
    win = make_window()
    win.open_path(str(note))

    assert win._editor.toPlainText() == "寬字元\n"

    win._editor.setPlainText("寬字元 改\n")
    assert win._save_edits() is True
    assert note.read_bytes() == "寬字元 改\r\n".encode("utf-16")


def test_txt_undecodable_bytes_show_error_not_garbage(
    make_window, tmp_path, monkeypatch
):
    warnings = []
    monkeypatch.setattr(
        window_mod.QMessageBox,
        "warning",
        lambda *args: warnings.append(args[2] if len(args) > 2 else ""),
    )
    note = tmp_path / "bad.txt"
    note.write_bytes(b"\xff\xff\x00\x81\x81\xfe")
    win = make_window()

    win.open_path(str(note))

    assert warnings  # 無法讀取檔案編碼 reached the user
    assert win._edit_mode is False
    assert win._stack.currentWidget() is win._renderer
    assert win._renderer.empty_shown is True
    assert win._editor.toPlainText() == ""


def test_session_restore_reopens_txt_tab(make_window, tmp_path):
    note = tmp_path / "keep.txt"
    note.write_text("hello\n", encoding="utf-8")
    settings = window_mod.QSettings(_ORG, _APP)
    settings.setValue("open_tabs", json.dumps([str(note)]))
    settings.setValue("active_tab", 0)
    win = make_window()

    win.restore_last_session()

    assert win._tab_bar.count() == 1
    assert win._current_kind == "text"
    assert win._edit_mode is True
    assert win._editor.toPlainText() == "hello\n"


def test_txt_unsaved_close_prompts_and_discard_closes(
    make_window, tmp_path, monkeypatch
):
    note = tmp_path / "dirty.txt"
    note.write_text("original", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    win._editor.setPlainText("changed")
    win._editor.document().setModified(True)
    asked = _answer_question(
        monkeypatch, window_mod.QMessageBox.StandardButton.Discard
    )

    assert win._on_tab_close(0) is True

    assert asked
    assert win._tab_bar.count() == 0
    assert note.read_text(encoding="utf-8") == "original"


def test_tab_switch_between_md_and_txt_restores_views(
    make_window, md_files, tmp_path
):
    first, _second = md_files
    note = tmp_path / "plain.txt"
    note.write_text("text body", encoding="utf-8")
    win = make_window()

    win.open_path(str(first))
    assert win._stack.currentWidget() is win._renderer
    win.open_path(str(note))
    assert win._stack.currentWidget() is win._editor_split
    assert win._editor._plain_text_mode is True

    win._tab_bar.setCurrentIndex(0)
    assert win._current_kind == "markdown"
    assert win._stack.currentWidget() is win._renderer

    win._tab_bar.setCurrentIndex(1)
    assert win._current_kind == "text"
    assert win._stack.currentWidget() is win._editor_split
    assert win._editor.toPlainText() == "text body"

    # Going back to Markdown editing turns Markdown features on again.
    win._tab_bar.setCurrentIndex(0)
    win._toggle_edit_mode()
    assert win._edit_mode is True
    assert win._editor._plain_text_mode is False


# ---------------- Ctrl+N new note ----------------
def test_new_note_creates_opens_and_edits(make_window, tmp_path, monkeypatch):
    win = make_window()
    win._panel.file_browser.selected_directory = lambda: tmp_path
    revealed = []
    win._panel.file_browser.reveal_created_note = (
        lambda p: revealed.append(Path(p))
    )

    class _FakeDialog:
        def __init__(self, folder, theme, parent=None, **_kwargs):
            self._folder = Path(folder)
            self._path = None

        def exec(self):
            from app import file_ops

            self._path = file_ops.create_document(self._folder, "新筆記", ".txt")
            return window_mod.QDialog.DialogCode.Accepted

        def created_path(self):
            return self._path

    monkeypatch.setattr(window_mod, "NewNoteDialog", _FakeDialog)

    win._new_note()

    created = tmp_path / "新筆記.txt"
    assert created.exists()
    assert created.read_bytes() == b""
    assert revealed == [created]
    assert win._tab_bar.count() == 1
    assert win._current_file == created
    assert win._current_kind == "text"
    assert win._edit_mode is True


def test_new_note_falls_back_to_first_library_root(
    make_window, tmp_path, monkeypatch
):
    win = make_window()
    win._panel.file_browser.selected_directory = lambda: None
    win._panel.file_browser.library_roots = lambda: [tmp_path]
    folders = []

    class _CancelDialog:
        def __init__(self, folder, theme, parent=None, **_kwargs):
            folders.append(Path(folder))

        def exec(self):
            return window_mod.QDialog.DialogCode.Rejected

        def created_path(self):
            return None

    monkeypatch.setattr(window_mod, "NewNoteDialog", _CancelDialog)

    win._new_note()

    assert folders == [tmp_path]
    assert win._tab_bar.count() == 0
    assert list(tmp_path.iterdir()) == []


def test_file_tree_new_note_uses_the_same_dialog_for_the_requested_folder(
    make_window, tmp_path, monkeypatch
):
    win = make_window()
    requested = tmp_path / "nested"
    requested.mkdir()
    win._panel.file_browser.selected_directory = lambda: tmp_path
    folders = []

    class _CancelDialog:
        def __init__(self, folder, theme, parent=None, **_kwargs):
            folders.append(Path(folder))

        def exec(self):
            return window_mod.QDialog.DialogCode.Rejected

        def created_path(self):
            return None

    monkeypatch.setattr(window_mod, "NewNoteDialog", _CancelDialog)

    win._panel.file_browser.on_new_note_requested(str(requested))

    assert folders == [requested]
    assert win._tab_bar.count() == 0


def test_new_note_without_any_folder_asks_and_cancels_cleanly(
    make_window, monkeypatch
):
    win = make_window()
    win._panel.file_browser.selected_directory = lambda: None
    win._panel.file_browser.library_roots = lambda: []
    asked = []
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getExistingDirectory",
        staticmethod(lambda *a, **k: (asked.append(True), "")[1]),
    )

    def _fail_dialog(*_a, **_k):
        raise AssertionError("dialog must not open without a folder")

    monkeypatch.setattr(window_mod, "NewNoteDialog", _fail_dialog)

    win._new_note()

    assert asked == [True]
    assert win._tab_bar.count() == 0


# ---------------- Markdown format toolbar ----------------
def test_format_bold_modifies_document_and_undoes_in_one_step(
    make_window, md_files
):
    first, _second = md_files  # "# First\n\nAlpha"
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    assert win._edit_mode is True

    editor = win._editor
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)  # "First"
    editor.setTextCursor(cursor)

    win._format_bold()
    assert editor.toPlainText() == "# **First**\n\nAlpha"
    assert editor.textCursor().selectedText() == "First"

    editor.document().undo()  # a single undo step restores the original
    assert editor.toPlainText() == "# First\n\nAlpha"


def test_format_italic_shortcut_handler_wraps_selection(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()

    editor = win._editor
    cursor = editor.textCursor()
    cursor.setPosition(9)
    cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)  # "Alpha"
    editor.setTextCursor(cursor)

    win._format_italic()
    assert editor.toPlainText() == "# First\n\n*Alpha*"


def test_word_style_format_shortcuts_are_real_and_editor_scoped(
    make_window, md_files, qapp
):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    win.show()
    editor = win._editor
    editor.setFocus()
    qapp.processEvents()

    cursor = editor.textCursor()
    cursor.setPosition(9)
    cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)  # "Alpha"
    editor.setTextCursor(cursor)
    QTest.keyClick(
        editor, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier
    )
    assert editor.toPlainText() == "# First\n\n[Alpha](url)"
    assert editor.textCursor().selectedText() == "url"

    editor.document().undo()
    cursor = editor.textCursor()
    cursor.setPosition(9)
    editor.setTextCursor(cursor)
    QTest.keyClick(
        editor,
        Qt.Key.Key_8,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert editor.toPlainText() == "# First\n\n- Alpha"

    editor.document().undo()
    QTest.keyClick(
        editor,
        Qt.Key.Key_7,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert editor.toPlainText() == "# First\n\n1. Alpha"

    before = editor.toPlainText()
    win._toggle_search()
    win._ed_find.setFocus()
    QTest.keyClick(
        win._ed_find, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier
    )
    assert editor.toPlainText() == before


def test_ctrl_k_does_not_format_plain_text(make_window, tmp_path, qapp):
    note = tmp_path / "plain.txt"
    note.write_text("plain text", encoding="utf-8")
    win = make_window()
    win.open_path(str(note))
    win.show()
    win._editor.setFocus()
    qapp.processEvents()
    cursor = win._editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    win._editor.setTextCursor(cursor)

    QTest.keyClick(
        win._editor, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier
    )

    assert win._editor.toPlainText() == "plain text"


def test_slash_image_cancel_keeps_query_and_success_replaces_it(
    make_window, md_files, tmp_path, monkeypatch, qapp
):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    win.show()
    editor = win._editor
    editor.set_content("")
    editor.setFocus()
    qapp.processEvents()

    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: ("", "")),
    )
    QTest.keyClicks(editor, "/image")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText() == "/image"
    assert editor._slash_popup.isHidden()

    source = tmp_path / "inserted.png"
    source.write_bytes(b"png")
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(source), "圖片 (*.png)")),
    )
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    cursor.removeSelectedText()
    editor.setTextCursor(cursor)
    QTest.keyClicks(editor, "/image")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert editor.toPlainText().startswith("![](")
    assert "/image" not in editor.toPlainText()

    editor.document().undo()
    assert editor.toPlainText() == "/image"


def test_format_handlers_noop_in_preview_and_for_txt(
    make_window, md_files, tmp_path
):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    assert win._edit_mode is False
    win._format_bold()  # preview mode: nothing to do
    assert win._editor.toPlainText() == ""

    note = tmp_path / "plain.txt"
    note.write_text("just text\n", encoding="utf-8")
    win.open_path(str(note))
    assert win._edit_mode is True
    assert win._editor._plain_text_mode is True
    win._format_bold()
    win._apply_format_action("table")
    assert win._editor.toPlainText() == "just text\n"


def test_format_toolbar_visible_for_markdown_edit_hidden_for_txt(
    make_window, md_files, tmp_path
):
    first, _second = md_files
    note = tmp_path / "plain.txt"
    note.write_text("text\n", encoding="utf-8")
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics

    win.open_path(str(first))
    assert win._format_toolbar.isHidden()  # preview mode

    win._toggle_edit_mode()
    assert not win._format_toolbar.isHidden()

    win._toggle_edit_mode()  # back to preview
    assert win._format_toolbar.isHidden()

    win.open_path(str(note))  # .txt opens straight into the editor
    assert win._edit_mode is True
    assert win._format_toolbar.isHidden()


def test_format_toolbar_buttons_apply_actions(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()

    editor = win._editor
    cursor = editor.textCursor()
    cursor.setPosition(9)
    cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)  # "Alpha"
    editor.setTextCursor(cursor)

    win._format_toolbar.button("quote").click()
    assert editor.toPlainText() == "# First\n\n> Alpha"

    win._format_toolbar.button("h2").click()
    assert editor.toPlainText() == "# First\n\n## > Alpha"


def test_format_toolbar_reflects_reliable_cursor_context(
    make_window, md_files, qapp
):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    editor = win._editor
    cursor = editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    qapp.processEvents()

    assert win._format_toolbar.button("h1").property("formatActive") is True
    assert win._format_toolbar.button("bold").property("formatActive") is False

    win._format_toolbar.button("bold").click()
    qapp.processEvents()
    assert win._format_toolbar.button("bold").property("formatActive") is True


def test_format_toolbar_has_all_second_batch_buttons(make_window):
    win = make_window()
    ids = win._format_toolbar.action_ids()
    for action_id in (
        "image", "mermaid", "math_inline", "math_block",
        "wikilink", "highlight",
    ):
        assert action_id in ids
        assert win._format_toolbar.button(action_id) is not None
    extension = win._format_toolbar._extension_button
    assert extension is not None
    assert extension.text() == "⋯"
    assert extension.toolTip() == "更多格式工具"


def test_format_toolbar_overflow_is_contained_and_invokes_hidden_action(qapp):
    from app.format_toolbar import FormatToolbar
    from app.theme import LIGHT

    toolbar = FormatToolbar()
    try:
        toolbar.apply_theme(LIGHT)
        toolbar.resize(480, toolbar.sizeHint().height())
        toolbar.show()
        qapp.processEvents()
        extension = toolbar._extension_button

        assert extension is not None
        assert extension.isVisible()
        assert extension.geometry().right() <= toolbar.rect().right()

        # Qt builds the native extension menu during toolbar layout.  Inspect
        # and trigger it directly; clicking would enter QMenu's modal loop.
        menu = extension.menu()
        assert menu is not None
        hidden_actions = [
            action for action in menu.actions() if not action.isSeparator()
        ]
        assert hidden_actions
        assert all(action.data() for action in hidden_actions)

        triggered = []
        toolbar.action_triggered.connect(triggered.append)
        expected = hidden_actions[0].data()
        hidden_actions[0].trigger()
        assert triggered == [expected]
    finally:
        toolbar.close()
        toolbar.deleteLater()
        qapp.processEvents()


def test_second_batch_buttons_apply_actions(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    editor = win._editor

    cursor = editor.textCursor()
    cursor.setPosition(9)
    cursor.setPosition(14, QTextCursor.MoveMode.KeepAnchor)  # "Alpha"
    editor.setTextCursor(cursor)
    win._format_toolbar.button("highlight").click()
    assert editor.toPlainText() == "# First\n\n<mark>Alpha</mark>"
    assert editor.textCursor().selectedText() == "Alpha"

    win._format_toolbar.button("wikilink").click()
    assert editor.toPlainText() == "# First\n\n<mark>[[Alpha]]</mark>"

    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    win._format_toolbar.button("mermaid").click()
    assert editor.toPlainText().endswith(
        "```mermaid\nflowchart LR\n"
        "    A[步驟一] --> B[步驟二]\n```\n"
    )
    assert editor.textCursor().selectedText() == "步驟一"

    win._format_toolbar.button("math_block").click()
    assert "$$\n\n$$" in editor.toPlainText()


def test_image_button_without_saved_path_shows_status(
    make_window, md_files, monkeypatch
):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(first))
    win._toggle_edit_mode()
    win._editor.set_document_path(None)  # simulate an unsaved buffer

    opened = []
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (opened.append(True), ("", ""))[1]),
    )
    before = win._editor.toPlainText()

    win._apply_format_action("image")

    assert opened == []  # the dialog must not even open
    assert win._editor.toPlainText() == before
    assert win.statusBar().currentMessage() == "請先儲存文件才能貼入圖片"


def test_image_button_imports_asset_and_inserts_link(
    make_window, tmp_path, monkeypatch
):
    docs = tmp_path / "docs"
    docs.mkdir()
    note = docs / "note.md"
    note.write_text("# N\n\nBody", encoding="utf-8")
    src = tmp_path / "elsewhere" / "pic.png"
    src.parent.mkdir()
    src.write_bytes(b"\x89PNG-fake")

    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(note))
    win._toggle_edit_mode()
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(src), "")),
    )
    cursor = win._editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    win._editor.setTextCursor(cursor)

    win._apply_format_action("image")

    assert win._editor.toPlainText().endswith("![](assets/pic.png)")
    copied = docs / "assets" / "pic.png"
    assert copied.read_bytes() == b"\x89PNG-fake"

    win._editor.document().undo()  # one undo step removes the whole link
    assert "assets/pic.png" not in win._editor.toPlainText()


def test_image_button_via_toolbar_click(make_window, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    note = docs / "note.md"
    note.write_text("Body", encoding="utf-8")
    src = tmp_path / "out" / "shot.png"
    src.parent.mkdir()
    src.write_bytes(b"png")

    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise split-backend editor mechanics
    win.open_path(str(note))
    win._toggle_edit_mode()
    monkeypatch.setattr(
        window_mod.QFileDialog,
        "getOpenFileName",
        staticmethod(lambda *a, **k: (str(src), "")),
    )

    win._format_toolbar.button("image").click()

    assert "![](assets/shot.png)" in win._editor.toPlainText()


# ---------------- new .md notes open in split mode ----------------
def test_new_md_note_uses_original_markdown_split_by_default(make_window, tmp_path):
    win = make_window()
    note = tmp_path / "fresh.md"
    note.write_text("", encoding="utf-8")

    win._on_browser_note_created(str(note))

    assert win._current_kind == "markdown"
    assert win._view_mode == "split"
    assert win._stack.currentWidget() is win._editor_split
    assert win._edit_preview.isVisibleTo(win._editor_split)
    assert win._edit_preview.text_renders  # live preview rendered once
    assert win.focusWidget() is win._editor


def test_new_md_note_can_open_directly_in_explicit_office_route(make_window, tmp_path):
    win = make_window()
    note = tmp_path / "fresh-wysiwyg.md"
    note.write_text("", encoding="utf-8")

    win._on_browser_note_created(
        str(note), backend=edit_backend.WYSIWYG_BACKEND
    )

    assert win._current_kind == "markdown"
    assert win._view_mode == "edit"
    assert win._active_edit_backend == edit_backend.WYSIWYG_BACKEND
    assert win._stack.currentWidget() is win._wysiwyg_view


def test_new_txt_note_opens_plain_editor_not_split(make_window, tmp_path):
    win = make_window()
    note = tmp_path / "fresh.txt"
    note.write_text("", encoding="utf-8")

    win._on_browser_note_created(str(note))

    assert win._current_kind == "text"
    assert win._view_mode == "edit"
    assert win._editor._plain_text_mode is True
    assert not win._edit_preview.isVisibleTo(win._editor_split)


def test_opening_existing_md_stays_in_preview(make_window, md_files):
    first, _second = md_files
    win = make_window()
    win.open_path(str(first))
    assert win._view_mode == "preview"
    assert win._stack.currentWidget() is win._renderer


def test_new_note_split_not_reforced_after_user_switches(
    make_window, md_files, tmp_path
):
    first, _second = md_files
    win = make_window()
    win._edit_backend = edit_backend.SPLIT_BACKEND  # exercise the split-editor path
    note = tmp_path / "fresh2.md"
    note.write_text("# T\n", encoding="utf-8")

    win._on_browser_note_created(str(note))
    assert win._view_mode == "split"

    win._set_view_mode("edit")  # user drops the preview pane -> plain edit
    assert win._view_mode == "edit"

    win.open_path(str(first))  # tab switch resets editing to preview
    assert win._view_mode == "preview"

    win.open_path(str(note))  # each tab restores its own last editor mode
    assert win._view_mode == "edit"
    assert win._stack.currentWidget() is win._editor_split
