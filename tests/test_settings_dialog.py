"""Unit tests for app.settings_dialog (pure-logic, no real GUI)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import settings_dialog as settings_dialog_mod
from app.settings_dialog import SettingsDialog, _bool_from_qsettings

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def _clean_settings(tmp_path, monkeypatch):
    """Use an isolated INI file instead of the user's registry."""
    settings_path = tmp_path / "settings.ini"

    def isolated_settings(*_args, **_kwargs):
        return QSettings(str(settings_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(settings_dialog_mod, "QSettings", isolated_settings)
    library_root = tmp_path / "Vault"

    class FakeStore:
        def load(self):
            return [type("Library", (), {"path": str(library_root)})()]

    monkeypatch.setattr(settings_dialog_mod, "DocumentLibraryStore", FakeStore)
    yield


# ── _bool_from_qsettings ───────────────────────────────────────────────

class TestBoolFromQSettings:
    def test_bool_true(self):
        assert _bool_from_qsettings(True) is True

    def test_bool_false(self):
        assert _bool_from_qsettings(False) is False

    def test_string_true(self):
        assert _bool_from_qsettings("1") is True
        assert _bool_from_qsettings("true") is True
        assert _bool_from_qsettings("yes") is True
        assert _bool_from_qsettings("on") is True

    def test_string_false(self):
        assert _bool_from_qsettings("0") is False
        assert _bool_from_qsettings("false") is False
        assert _bool_from_qsettings("no") is False
        assert _bool_from_qsettings("off") is False

    def test_string_other(self):
        # Any string not in the false-list is truthy
        assert _bool_from_qsettings("hello") is True


# ── SettingsDialog ──────────────────────────────────────────────────────

class TestSettingsDialogConstruction:
    def test_dialog_creates(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        assert dlg.windowTitle() == "偏好設定"

    def test_four_tabs(self, qapp):
        dlg = SettingsDialog(None, current_theme="dark", current_zoom=1.25)
        # Find the QTabWidget
        tab_widget = None
        for child in dlg.children():
            if hasattr(child, "count") and hasattr(child, "tabText"):
                tab_widget = child
                break
        assert tab_widget is not None
        assert tab_widget.count() == 4
        assert tab_widget.tabText(0) == "外觀"
        assert tab_widget.tabText(1) == "匯出"
        assert tab_widget.tabText(2) == "行為"
        assert tab_widget.tabText(3) == "關於"

    def test_theme_dark_selected(self, qapp):
        dlg = SettingsDialog(None, current_theme="dark", current_zoom=1.0)
        assert dlg._theme_combo.currentData() == "dark"

    def test_theme_light_selected(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        assert dlg._theme_combo.currentData() == "light"

    def test_zoom_125_selected(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.25)
        assert dlg._zoom_combo.currentData() == pytest.approx(1.25, abs=0.01)

    def test_note_folders_default_below_first_library(self, qapp, tmp_path):
        dlg = SettingsDialog(None)

        assert dlg._daily_notes_edit.text() == str(tmp_path / "Vault" / "Daily Notes")
        assert dlg._templates_folder_edit.text() == str(tmp_path / "Vault" / "Templates")


class TestSettingsDialogAccept:
    """Simulate clicking OK by calling accept() directly."""

    def test_accept_populates_results(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        # Switch to dark theme
        dlg._theme_combo.setCurrentIndex(1)  # dark
        # Pick 150% zoom
        idx_150 = next(
            i for i in range(dlg._zoom_combo.count())
            if round(dlg._zoom_combo.itemData(i) * 100) == 150
        )
        dlg._zoom_combo.setCurrentIndex(idx_150)
        # Set CSS path
        dlg._css_edit.setText("/some/test.css")
        # Uncheck update
        dlg._update_cb.setChecked(False)
        # Set PDF to Letter / landscape
        letter_idx = next(
            i for i in range(dlg._pdf_size_combo.count())
            if dlg._pdf_size_combo.itemData(i) == "Letter"
        )
        dlg._pdf_size_combo.setCurrentIndex(letter_idx)
        dlg._pdf_orient_combo.setCurrentIndex(1)  # landscape
        dlg._daily_notes_edit.setText("/notes/daily")
        dlg._daily_template_edit.setText("/templates/daily.md")
        dlg._templates_folder_edit.setText("/templates")

        dlg.accept()

        r = dlg.results
        assert r["theme"] == "dark"
        assert r["content_zoom"] == pytest.approx(1.5, abs=0.01)
        assert r["custom_css_path"] == "/some/test.css"
        assert r["update_check_enabled"] is False
        assert r["pdf_page_size"] == "Letter"
        assert r["pdf_orientation"] == "landscape"
        assert r["daily_notes_folder"] == "/notes/daily"
        assert r["daily_note_template"] == "/templates/daily.md"
        assert r["templates_folder"] == "/templates"

    def test_accept_persists_to_qsettings(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        dlg._theme_combo.setCurrentIndex(1)  # dark
        dlg.accept()

        s = settings_dialog_mod.QSettings(_ORG, _APP)
        assert s.value("theme") == "dark"

    def test_results_empty_before_accept(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        assert dlg.results == {}


class TestSettingsDialogPdfOrientation:
    """PDF orientation combo should disable when 'single' is selected."""

    def test_single_disables_orient(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        # Find the index for "single"
        single_idx = next(
            i for i in range(dlg._pdf_size_combo.count())
            if dlg._pdf_size_combo.itemData(i) == "single"
        )
        dlg._pdf_size_combo.setCurrentIndex(single_idx)
        assert not dlg._pdf_orient_combo.isEnabled()

    def test_a4_enables_orient(self, qapp):
        dlg = SettingsDialog(None, current_theme="light", current_zoom=1.0)
        # First set to single, then back to A4
        single_idx = next(
            i for i in range(dlg._pdf_size_combo.count())
            if dlg._pdf_size_combo.itemData(i) == "single"
        )
        dlg._pdf_size_combo.setCurrentIndex(single_idx)
        a4_idx = next(
            i for i in range(dlg._pdf_size_combo.count())
            if dlg._pdf_size_combo.itemData(i) == "A4"
        )
        dlg._pdf_size_combo.setCurrentIndex(a4_idx)
        assert dlg._pdf_orient_combo.isEnabled()
