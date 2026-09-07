from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

from app.theme import DARK, LIGHT, app_stylesheet, apply_combo_popup_theme, toolbar_stylesheet


def test_toolbar_popup_has_opaque_readable_surface_after_theme_switch(qapp):
    root = QWidget()
    toolbar = QWidget(root)
    toolbar.setObjectName("topToolbar")
    layout = QVBoxLayout(toolbar)
    combo = QComboBox()
    combo.addItems(["閱讀", "Markdown", "並排預覽", "Office 編輯"])
    layout.addWidget(combo)
    root.resize(400, 200)
    toolbar.resize(300, 56)
    root.show()
    for theme in (LIGHT, DARK, LIGHT):
        root.setStyleSheet(app_stylesheet(theme))
        toolbar.setStyleSheet(toolbar_stylesheet(theme))
        apply_combo_popup_theme(combo, theme)
        combo.showPopup()
        qapp.processEvents()
        view = combo.view()
        assert view.window() is not root
        assert view.window().isVisible()
        assert view.viewport().palette().color(QPalette.ColorRole.Base) == QColor(theme.surface)
        assert view.palette().color(QPalette.ColorRole.Text) == QColor(theme.text)
        pixels = view.viewport().grab().toImage()
        assert pixels.pixelColor(pixels.width() - 3, pixels.height() - 3) == QColor(theme.surface)
        combo.hidePopup()
    root.close()
