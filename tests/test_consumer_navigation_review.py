"""Independent consumer-navigation acceptance checks using isolated sources."""

from pathlib import Path
import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QDialogButtonBox, QScrollArea, QTabWidget

from app import file_browser, settings_dialog
from app.annotations import DocumentAnnotations
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import FileBrowserView
from app.quick_open import QuickOpenDialog
from app.tag_index import TagIndex
from app.theme import DARK, LIGHT, app_stylesheet


@pytest.fixture(autouse=True)
def isolate_preferences(monkeypatch, tmp_path):
    monkeypatch.setattr(file_browser, "load_excluded_folders", lambda: [])
    monkeypatch.setattr(
        settings_dialog, "QSettings",
        lambda *_args: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
    )
    monkeypatch.setattr(
        settings_dialog, "DocumentLibraryStore",
        lambda: DocumentLibraryStore(tmp_path / "settings-libraries.json"),
    )


def make_browser(monkeypatch, tmp_path, *, background=False, tag_index=None):
    root = tmp_path / "vault"
    nested = root / "nested"
    nested.mkdir(parents=True)
    for path in (root / "one.md", nested / "two.markdown", nested / "three.txt"):
        path.write_text("# note", encoding="utf-8")
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save([DocumentLibrary("one", "Library One", str(root))])
    monkeypatch.setattr(file_browser, "DocumentLibraryStore", lambda: store)
    view = FileBrowserView(lambda _path: None, background_scan=background, tag_index=tag_index)
    return view, store, root


def wait_for_scan(qapp, view):
    deadline = time.monotonic() + 3
    while view.is_scanning() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert not view.is_scanning()


def test_inventory_survives_name_and_tag_filters(qapp, monkeypatch, tmp_path):
    tags = TagIndex(tmp_path / "tags.json")
    tags.update(tmp_path / "vault" / "one.md", DocumentAnnotations(doc_tags=["selected"]))
    view, _store, root = make_browser(monkeypatch, tmp_path, tag_index=tags)
    expected = {str(root / "one.md"), str(root / "nested" / "two.markdown"),
                str(root / "nested" / "three.txt")}
    assert {path for _name, path in view.quick_open_documents()} == expected
    view._filter.setText("two")
    view.set_tag_filter("selected")
    assert {path for _name, path in view.quick_open_documents()} == expected
    view.set_tag_filter("no-such-tag")
    assert {path for _name, path in view.quick_open_documents()} == expected
    view.close()


def test_background_inventory_updates_an_open_palette(qapp, monkeypatch, tmp_path):
    view, _store, root = make_browser(monkeypatch, tmp_path, background=True)
    quick = QuickOpenDialog([], LIGHT, loading=True)
    quick._input.setText("two")

    def update_palette():
        quick.set_candidates(view.quick_open_documents(),
                             locations=view.quick_open_locations(), loading=view.is_scanning())

    view.documents_changed.connect(update_palette)
    wait_for_scan(qapp, view)
    assert quick._input.text() == "two"
    assert quick._list.count() == 1
    assert quick._list.item(0).data(Qt.ItemDataRole.UserRole) == str(root / "nested" / "two.markdown")
    assert not quick._loading
    assert "Library One" in quick._list.item(0).text()
    view.close()
    quick.close()


def test_removing_last_source_while_scanning_clears_loading(qapp, monkeypatch, tmp_path):
    view, store, _root = make_browser(monkeypatch, tmp_path)
    quick = QuickOpenDialog(view.quick_open_documents(), LIGHT, loading=True)
    view.documents_changed.connect(lambda: quick.set_candidates(
        view.quick_open_documents(), loading=view.is_scanning()))
    # Model a refresh already in flight; removal cancels it before results apply.
    view._scan_inflight = True
    store.save([])
    view.refresh_libraries()
    assert not view.is_scanning()
    assert quick._list.count() == 0
    assert not quick._loading
    view.close()
    quick.close()


def test_scan_failure_clears_loading_in_open_palette(qapp, monkeypatch, tmp_path):
    view, _store, _root = make_browser(monkeypatch, tmp_path)
    quick = QuickOpenDialog(view.quick_open_documents(), LIGHT, loading=True)
    view.documents_changed.connect(lambda: quick.set_candidates(
        view.quick_open_documents(), loading=view.is_scanning()))
    view._scan_inflight = True
    view._on_scan_finished(view._scan_generation, None, None)
    assert not view.is_scanning()
    assert not quick._loading
    view.close()
    quick.close()


def test_duplicate_names_remain_distinct_after_refresh(qapp, tmp_path):
    first, second = str(tmp_path / "first" / "Meeting.md"), str(tmp_path / "second" / "Meeting.md")
    quick = QuickOpenDialog([("Meeting.md", first), ("Meeting.md", second)], LIGHT)
    texts = [quick._list.item(index).text() for index in range(2)]
    assert texts[0] != texts[1]
    quick._list.setCurrentRow(1)
    quick.set_candidates([("Meeting.md", second), ("Meeting.md", first)])
    assert quick._list.currentItem().data(Qt.ItemDataRole.UserRole) == second
    quick.close()


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_settings_at_540px_keep_actions_and_all_pages_reachable(qapp, theme):
    for filename in ("msjh.ttc", "segoeui.ttf"):
        font = Path("C:/Windows/Fonts") / filename
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))
    dialog = settings_dialog.SettingsDialog(current_theme=theme.name)
    dialog.setStyleSheet(app_stylesheet(theme))
    dialog.resize(680, 540)
    dialog.show()
    qapp.processEvents()
    assert dialog.height() == 540
    tabs = dialog.findChild(QTabWidget)
    actions = dialog.findChild(QDialogButtonBox)
    for index in range(tabs.count()):
        tabs.setCurrentIndex(index)
        qapp.processEvents()
        scroll = tabs.widget(index)
        assert isinstance(scroll, QScrollArea)
        for button in actions.buttons():
            assert button.isVisible()
            assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
        bar = scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        qapp.processEvents()
        assert scroll.widget().y() + scroll.widget().height() <= scroll.viewport().height()
        if theme.name == "dark":
            image = scroll.viewport().grab().toImage()
            assert image.pixelColor(3, 3).lightness() < 128
    dialog.close()
