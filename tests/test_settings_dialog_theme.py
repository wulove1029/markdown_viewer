from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import QTabWidget

from app.reading_style import WARM_DARK, WARM_LIGHT
from app.settings_dialog import SettingsDialog
from app.theme import DARK, LIGHT, app_stylesheet


def _pane_pixel(dialog):
    """Sample the tab pane as painted inside the whole dialog.

    Grabbing the page widget alone would fill it from its own palette and
    hide the unstyled pane the user actually sees.
    """
    tabs = dialog.findChild(QTabWidget)
    page = tabs.currentWidget()
    image = dialog.grab().toImage()
    point = page.mapTo(dialog, page.rect().bottomRight())
    return image.pixelColor(point.x() - 4, point.y() - 4)


def test_settings_dialog_tab_pane_follows_theme(qapp):
    for theme in (DARK, LIGHT, DARK):
        dialog = SettingsDialog(None, current_theme=theme.name, current_zoom=1.0)
        dialog.setStyleSheet(app_stylesheet(theme))
        dialog.show()
        qapp.processEvents()
        try:
            assert _pane_pixel(dialog) == QColor(theme.surface)
        finally:
            dialog.close()


def test_reading_page_fits_narrow_dialog_and_previews_both_paper_themes(qapp):
    # Windows' offscreen platform does not enumerate system fonts. Load the
    # same installed faces used by the real application before measuring text.
    if not QFontDatabase.families():
        for name in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
            path = Path("C:/Windows/Fonts") / name
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
    if not QFontDatabase.families():
        pytest.skip("Font metrics require at least one installed font")
    dialog = SettingsDialog(None)
    tabs = dialog.findChild(QTabWidget)
    tabs.setCurrentIndex(1)
    dialog.resize(480, 560)
    dialog.show()
    dialog._reading_paper_combo.setCurrentIndex(dialog._reading_paper_combo.findData("warm"))
    try:
        for theme, palette in ((LIGHT, WARM_LIGHT), (DARK, WARM_DARK)):
            dialog.setStyleSheet(app_stylesheet(theme))
            dialog._theme_combo.setCurrentIndex(dialog._theme_combo.findData(theme.name))
            qapp.processEvents()
            assert tabs.currentWidget().horizontalScrollBar().maximum() == 0
            sample = dialog._reading_sample.grab().toImage()
            assert sample.pixelColor(12, 12) == QColor(palette["page-bg"])
            assert dialog._reading_font_size_spin.accessibleName() == "內文字級"
    finally:
        dialog.close()
