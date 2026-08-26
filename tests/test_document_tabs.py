"""Tests for readable scrolling document tabs and the all-tabs picker."""

import sys

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QTabBar

from app.document_tabs import (
    MAX_TAB_WIDTH,
    MIN_TAB_WIDTH,
    DocumentTabBar,
    DocumentTabStrip,
    disambiguated_tab_labels,
)
from app.theme import DARK, LIGHT


def _add_tab(bar: DocumentTabBar, label: str, path: str) -> int:
    index = bar.addTab(label)
    bar.setTabData(index, path)
    bar.setTabToolTip(index, path)
    return index


def _close_button(bar: DocumentTabBar, index: int):
    return bar.tabButton(index, QTabBar.ButtonPosition.RightSide) or bar.tabButton(
        index, QTabBar.ButtonPosition.LeftSide
    )


def test_duplicate_labels_use_shortest_unique_parent_suffix(tmp_path):
    unique = tmp_path / "notes" / "guide.md"
    first = tmp_path / "project-a" / "docs" / "README.md"
    second = tmp_path / "project-b" / "docs" / "README.md"

    assert disambiguated_tab_labels([unique]) == ["guide.md"]
    labels = disambiguated_tab_labels([first, second, unique])
    assert labels == [
        "README.md · project-a/docs",
        "README.md · project-b/docs",
        "guide.md",
    ]
    assert disambiguated_tab_labels(
        ["C:/project/docs/README.md", "D:/project/docs/README.md"]
    ) == [
        "README.md · C:/project/docs",
        "README.md · D:/project/docs",
    ]


def test_many_tabs_keep_readable_width_and_active_tab_visible(qapp):
    strip = DocumentTabStrip()
    try:
        for index in range(14):
            name = f"CoilSync_封閉LAN_需求分析_{index:02}.md"
            tab_index = _add_tab(strip.tab_bar, name, f"E:/outputs/{name}")
            strip.tab_bar.set_mode_badge(
                tab_index, "office" if index % 2 else "markdown"
            )

        strip.resize(900, 36)
        strip.show()
        qapp.processEvents()
        assert "tab-close-light.svg" in strip.styleSheet()

        widths = [
            strip.tab_bar.tabRect(index).width()
            for index in range(strip.tab_bar.count())
        ]
        assert all(MIN_TAB_WIDTH <= width <= MAX_TAB_WIDTH for width in widths)
        assert strip.tab_bar.has_overflow() is True
        assert strip.tab_bar.tabRect(13).right() > (
            strip.tab_bar.visible_tabs_rect().right()
        )
        assert strip.overflow_button.isVisible() is True

        strip.tab_bar.setCurrentIndex(13)
        qapp.processEvents()
        active = strip.tab_bar.tabRect(13)
        visible = strip.tab_bar.visible_tabs_rect()
        assert active.left() >= visible.left()
        assert active.right() <= visible.right()
        assert strip.tab_bar.tabAt(active.center()) == 13

        strip.tab_bar.setCurrentIndex(0)
        qapp.processEvents()
        assert strip.tab_bar.tabRect(0).left() >= 0

        # Moving the active tab does not emit currentChanged, so the tab bar
        # must explicitly update Qt's private scroll offset.
        strip.tab_bar.moveTab(0, strip.tab_bar.count() - 1)
        qapp.processEvents()
        assert strip.tab_bar.currentIndex() == strip.tab_bar.count() - 1
        active = strip.tab_bar.tabRect(strip.tab_bar.currentIndex())
        visible = strip.tab_bar.visible_tabs_rect()
        assert active.left() >= visible.left()
        assert active.right() <= visible.right()
        assert strip.grab().isNull() is False

        strip.apply_theme(DARK)
        qapp.processEvents()
        assert "tab-close-dark.svg" in strip.styleSheet()
        assert strip.grab().isNull() is False
    finally:
        strip.close()


def test_close_buttons_only_show_for_active_or_hovered_and_middle_click(qapp):
    bar = DocumentTabBar()
    closed = []
    bar.tabCloseRequested.connect(closed.append)
    try:
        for index in range(3):
            tab_index = _add_tab(
                bar, f"document-{index}.md", f"E:/notes/document-{index}.md"
            )
            bar.set_mode_badge(
                tab_index, "office" if index == 1 else "markdown"
            )

        bar.resize(620, 36)
        bar.show()
        qapp.processEvents()
        before = [bar.tabRect(index).width() for index in range(bar.count())]

        assert [_close_button(bar, index).isVisible() for index in range(3)] == [
            True,
            False,
            False,
        ]

        QTest.mouseMove(bar, bar.tabRect(1).center())
        qapp.processEvents()
        assert [_close_button(bar, index).isVisible() for index in range(3)] == [
            True,
            True,
            False,
        ]
        assert [bar.tabRect(index).width() for index in range(bar.count())] == before
        QTest.mouseClick(_close_button(bar, 1), Qt.MouseButton.LeftButton)
        assert closed == [1]

        QApplication.sendEvent(bar, QEvent(QEvent.Type.Leave))
        qapp.processEvents()
        assert [_close_button(bar, index).isVisible() for index in range(3)] == [
            True,
            False,
            False,
        ]

        QTest.mouseClick(
            bar,
            Qt.MouseButton.MiddleButton,
            Qt.KeyboardModifier.NoModifier,
            bar.tabRect(1).center(),
        )
        assert closed == [1, 1]
    finally:
        bar.close()


def test_mode_badges_are_colored_theme_aware_and_move_with_tabs(qapp):
    bar = DocumentTabBar()
    try:
        first = _add_tab(bar, "first.md", "E:/notes/first.md")
        second = _add_tab(bar, "second.md", "E:/notes/second.md")
        plain_width = bar.tabSizeHint(first).width()

        bar.set_mode_badge(first, "office")
        bar.set_mode_badge(second, "markdown")
        bar.resize(420, 36)
        bar.show()
        qapp.processEvents()

        assert bar.mode_badge(first) == "office"
        assert bar.mode_badge_text(first) == "Office"
        assert bar.mode_badge(second) == "markdown"
        assert bar.mode_badge_text(second) == "MD"
        assert bar.tabSizeHint(first).width() > plain_width
        assert bar.tabIcon(first).isNull() is False
        assert bar.tabIcon(second).isNull() is False
        assert bar.tabButton(first, QTabBar.ButtonPosition.LeftSide) is None
        assert _close_button(bar, first).objectName() == "documentTabCloseButton"
        assert bar.tabRect(first).width() == bar.tabSizeHint(first).width()

        office_light = bar.tabIcon(first).pixmap(bar.iconSize()).toImage()
        markdown_light = bar.tabIcon(second).pixmap(bar.iconSize()).toImage()
        assert office_light.pixelColor(23, 3).name() == LIGHT.accent_soft
        assert markdown_light.pixelColor(23, 3).name() == LIGHT.surface_alt
        office_light_colors = {
            office_light.pixelColor(x, y).name()
            for x in range(office_light.width())
            for y in range(office_light.height())
        }
        markdown_light_colors = {
            markdown_light.pixelColor(x, y).name()
            for x in range(markdown_light.width())
            for y in range(markdown_light.height())
        }
        assert LIGHT.accent in office_light_colors
        assert LIGHT.success in markdown_light_colors

        bar.apply_theme(DARK)
        office_dark = bar.tabIcon(first).pixmap(bar.iconSize()).toImage()
        markdown_dark = bar.tabIcon(second).pixmap(bar.iconSize()).toImage()
        assert office_dark.pixelColor(23, 3).name() == DARK.accent_soft
        assert markdown_dark.pixelColor(23, 3).name() == DARK.surface_alt
        office_dark_colors = {
            office_dark.pixelColor(x, y).name()
            for x in range(office_dark.width())
            for y in range(office_dark.height())
        }
        markdown_dark_colors = {
            markdown_dark.pixelColor(x, y).name()
            for x in range(markdown_dark.width())
            for y in range(markdown_dark.height())
        }
        assert DARK.accent in office_dark_colors
        assert DARK.success in markdown_dark_colors

        bar.moveTab(first, second)
        qapp.processEvents()
        assert [bar.tabData(i) for i in range(2)] == [
            "E:/notes/second.md",
            "E:/notes/first.md",
        ]
        assert [bar.mode_badge(i) for i in range(2)] == [
            "markdown",
            "office",
        ]

        bar.removeTab(0)
        assert bar.tabData(0) == "E:/notes/first.md"
        assert bar.mode_badge(0) == "office"

        bar.set_mode_badge(0, None)
        assert bar.mode_badge(0) is None
        assert bar.mode_badge_text(0) == ""
        assert bar.tabIcon(0).isNull() is True
    finally:
        bar.close()


def test_all_tabs_menu_searches_live_order_and_selects_by_path(qapp):
    strip = DocumentTabStrip()
    try:
        paths = []
        for index in range(6):
            name = f"document-{index}.md"
            path = f"E:/notes/{name}"
            paths.append(path)
            tab_index = _add_tab(strip.tab_bar, name, path)
            strip.tab_bar.set_mode_badge(
                tab_index, "office" if index % 2 else "markdown"
            )
        strip.tab_bar.setCurrentIndex(2)

        menu = strip.build_tabs_menu()
        assert menu.parent() is None
        tab_actions = [action for action in menu.actions() if action.data()]
        assert [action.data() for action in tab_actions] == paths
        assert [action.text() for action in tab_actions[:2]] == [
            "[MD] document-0.md",
            "[Office] document-1.md",
        ]
        assert [action.isChecked() for action in tab_actions] == [
            False,
            False,
            True,
            False,
            False,
            False,
        ]

        search = menu.findChild(QLineEdit, "documentTabsSearch")
        assert search is not None
        search.setText("document-4")
        qapp.processEvents()
        assert [action.isVisible() for action in tab_actions] == [
            False,
            False,
            False,
            False,
            True,
            False,
        ]

        # Menu actions retain paths rather than stale indexes after drag/reorder.
        strip.tab_bar.moveTab(4, 0)
        tab_actions[4].trigger()
        assert strip.tab_bar.tabData(strip.tab_bar.currentIndex()) == paths[4]
    finally:
        strip.close()


def test_transient_tab_menus_are_not_retained_by_strip(qapp):
    strip = DocumentTabStrip()
    try:
        _add_tab(strip.tab_bar, "first.md", "E:/notes/first.md")
        for _ in range(8):
            menu = strip.build_tabs_menu()
            assert menu.parent() is None
            menu.deleteLater()
        qapp.processEvents()
        assert strip.findChildren(QMenu) == []
    finally:
        strip.close()


def test_tab_popup_can_be_opened_and_deleted_without_delayed_callbacks(
    qapp, monkeypatch
):
    callback_errors = []
    monkeypatch.setattr(
        sys, "excepthook", lambda *error: callback_errors.append(error)
    )
    strip = DocumentTabStrip()
    try:
        _add_tab(strip.tab_bar, "first.md", "E:/notes/first.md")
        menu = strip.build_tabs_menu()
        menu.popup(strip.mapToGlobal(QPoint(0, 0)))
        menu.hide()
        menu.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        assert callback_errors == []
    finally:
        strip.close()


def test_tab_strip_collapses_when_empty(qapp):
    strip = DocumentTabStrip()
    try:
        assert strip.isHidden() is True
        first = _add_tab(strip.tab_bar, "first.md", "E:/notes/first.md")
        qapp.processEvents()
        assert first == 0
        assert strip.isHidden() is False
        assert strip.overflow_button.isHidden() is True

        _add_tab(strip.tab_bar, "second.md", "E:/notes/second.md")
        qapp.processEvents()
        assert strip.overflow_button.isHidden() is False

        strip.tab_bar.removeTab(1)
        strip.tab_bar.removeTab(0)
        qapp.processEvents()
        assert strip.isHidden() is True
    finally:
        strip.close()
