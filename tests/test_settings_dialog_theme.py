from PySide6.QtGui import QColor
from PySide6.QtWidgets import QTabWidget

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
