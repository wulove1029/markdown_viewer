"""Tests for the compact theme/update controls in the top-right toolbar."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame

from app.theme import DARK, LIGHT
from app.toolbar_utilities import (
    UPDATE_AVAILABLE,
    UPDATE_CHECKING,
    UPDATE_DOWNLOADING,
    UPDATE_ERROR,
    UPDATE_IDLE,
    UTILITY_BUTTON_HEIGHT,
    UTILITY_BUTTON_WIDTH,
    UTILITY_GROUP_HEIGHT,
    UTILITY_GROUP_WIDTH,
    UTILITY_ICON_SIZE,
    ToolbarUtilities,
)


def test_utility_group_has_compact_stable_geometry_and_metadata(qapp):
    controls = ToolbarUtilities(
        LIGHT, theme_name="light", current_version="1.25.0"
    )
    try:
        assert controls.objectName() == "toolbarUtilities"
        assert controls.size().width() == UTILITY_GROUP_WIDTH
        assert controls.size().height() == UTILITY_GROUP_HEIGHT

        theme = controls.theme_button
        update = controls.update_button
        assert theme.objectName() == "themeToggleButton"
        assert update.objectName() == "updateButton"
        assert theme.size().width() == update.size().width() == UTILITY_BUTTON_WIDTH
        assert theme.size().height() == update.size().height() == UTILITY_BUTTON_HEIGHT
        assert theme.iconSize().width() == update.iconSize().width() == UTILITY_ICON_SIZE
        assert theme.text() == update.text() == ""
        assert theme.property("iconName") == "moon"
        assert theme.toolTip() == theme.accessibleName() == "切換為深色模式"
        assert update.property("updateState") == UPDATE_IDLE
        assert update.property("iconName") == "circle-arrow-up"
        assert update.toolTip() == update.accessibleName()
        assert "v1.25.0" in update.toolTip()
        assert theme.icon().isNull() is False
        assert update.icon().isNull() is False
        assert theme.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert update.focusPolicy() & Qt.FocusPolicy.TabFocus

        controls.show()
        qapp.processEvents()
        divider = controls.findChild(QFrame, "toolbarUtilityDivider")
        assert divider is not None
        assert theme.geometry().right() < divider.geometry().left()
        assert divider.geometry().right() < update.geometry().left()
        assert theme.geometry().top() == update.geometry().top()
        assert theme.geometry().bottom() == update.geometry().bottom()
        assert theme.geometry().top() == (
            controls.height() - theme.height()
        ) // 2
    finally:
        controls.close()


def test_theme_and_update_states_survive_theme_refresh(qapp):
    controls = ToolbarUtilities(
        LIGHT, theme_name="light", current_version="1.25.0"
    )
    try:
        light_theme_icon = controls.theme_button.icon().cacheKey()
        controls.set_update_state(UPDATE_AVAILABLE, version="1.26.0")
        assert controls.update_state == UPDATE_AVAILABLE
        assert controls.update_button.property("badgeVisible") is True
        assert controls.update_button.isEnabled() is True
        assert "v1.26.0" in controls.update_button.toolTip()

        controls.apply_theme(DARK, theme_name="dark")
        assert controls.theme_button.property("iconName") == "sun"
        assert controls.theme_button.toolTip() == "切換為淺色模式"
        assert controls.theme_button.icon().cacheKey() != light_theme_icon
        assert controls.update_state == UPDATE_AVAILABLE
        assert controls.update_button.property("badgeVisible") is True
        assert "v1.26.0" in controls.update_button.toolTip()

        controls.set_update_state(UPDATE_CHECKING)
        assert controls.update_button.property("iconName") == "refresh"
        assert controls.update_button.isEnabled() is False
        assert controls.update_button.property("badgeVisible") is False
        assert controls.update_button.toolTip() == "正在檢查更新…"

        controls.set_update_state(UPDATE_DOWNLOADING)
        assert controls.update_button.property("iconName") == "file-down"
        assert controls.update_button.isEnabled() is False
        assert controls.update_button.toolTip() == "正在下載更新…"

        controls.set_update_state(UPDATE_ERROR)
        assert controls.update_button.isEnabled() is True
        assert controls.update_button.toolTip() == "上次檢查失敗 · 按一下重試"

        controls.set_update_state(UPDATE_IDLE)
        assert controls.update_button.isEnabled() is True
        assert "v1.25.0" in controls.update_button.toolTip()
        with pytest.raises(ValueError):
            controls.set_update_state("unknown")
    finally:
        controls.close()


def test_utility_buttons_are_keyboard_focusable_and_activate_once(qapp):
    controls = ToolbarUtilities(LIGHT, current_version="1.25.0")
    theme_clicks = []
    update_clicks = []
    controls.theme_button.clicked.connect(lambda: theme_clicks.append(True))
    controls.update_button.clicked.connect(lambda: update_clicks.append(True))
    try:
        controls.show()
        controls.activateWindow()
        controls.theme_button.setFocus()
        qapp.processEvents()

        QTest.keyClick(controls.theme_button, Qt.Key.Key_Space)
        assert theme_clicks == [True]
        QTest.keyClick(controls.theme_button, Qt.Key.Key_Tab)
        assert controls.update_button.hasFocus() is True
        QTest.keyClick(controls.update_button, Qt.Key.Key_Space)
        assert update_clicks == [True]
    finally:
        controls.close()


def test_utility_group_renders_in_light_and_dark(qapp):
    controls = ToolbarUtilities(LIGHT, current_version="1.25.0")
    try:
        controls.show()
        qapp.processEvents()
        assert controls.grab().isNull() is False

        controls.set_update_state(UPDATE_AVAILABLE, version="1.26.0")
        controls.apply_theme(DARK, theme_name="dark")
        qapp.processEvents()
        assert controls.grab().isNull() is False
    finally:
        controls.close()
