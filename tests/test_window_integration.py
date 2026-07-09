import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QCloseEvent
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

    def find_text(self, _text):
        pass

    def scroll_to(self, _target):
        pass

    def scroll_to_ratio(self, _ratio):
        pass

    def select_annotation(self, _ann_id):
        pass

    def scroll_to_annotation(self, _ann_id):
        pass

    def render_markdown_text(self, *_args, **_kwargs):
        pass

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

    def add(self, path):
        self._paths.append(path)

    def paths(self):
        return list(self._paths)


class _FakePanel(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.close_btn = QPushButton()
        self.toc = _Noop()
        self.file_browser = _Noop()
        self.recent = _Recent()
        self.annotations = _Noop()
        self.backlinks = _Noop()
        self.pdf_notes = _Noop()
        self.pdf_highlights = _Noop()
        self.tags = _Noop()

    def apply_theme(self, _theme):
        pass

    def show_pdf_notes(self, _show):
        pass

    def set_annotations_enabled(self, _enabled):
        pass


class _FakeTagIndex:
    def all_tags(self):
        return []

    def tag_counts(self):
        return []

    def update(self, *_args, **_kwargs):
        pass

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
def _clean_settings():
    settings = QSettings(_ORG, _APP)
    keys = [
        "geometry",
        "open_tabs",
        "active_tab",
        "last_file",
        "content_zoom",
        "recent_files",
        "pdf_last_pages",
    ]
    backup = {key: settings.value(key) for key in keys}
    for key in keys:
        settings.remove(key)
    yield
    for key, value in backup.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)


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

    settings = QSettings(_ORG, _APP)
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


def test_open_path_ipc_entry_adds_to_existing_window(make_window, md_files):
    first, second = md_files
    win = make_window()

    win.open_path(str(first))
    win.open_path(str(second))
    win.open_path(str(second))

    assert win._tab_bar.count() == 2
    assert win._tab_bar.currentIndex() == 1
    assert [win._tab_bar.tabData(i) for i in range(2)] == [str(first), str(second)]
