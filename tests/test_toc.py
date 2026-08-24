from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics

from app.theme import DARK
from app.toc import (
    _DEPTH_ROLE,
    _LEVEL_ROLE,
    _MAX_TOC_DEPTH,
    _TARGET_ROLE,
    _TOC_INDENT,
    _TOC_ROW_HEIGHT,
    TocView,
)


def test_markdown_toc_stores_raw_titles_levels_and_tooltips(qapp):
    clicked = []
    view = TocView(clicked.append)
    headings = [
        (1, "Document title", "title"),
        (2, "Account setup", "account-setup"),
        (20, "A very deep heading", "deep"),
    ]
    try:
        view.update_headings(headings)

        assert view._list.count() == 3
        assert [view._list.item(i).text() for i in range(3)] == [
            heading[1] for heading in headings
        ]
        assert [view._list.item(i).toolTip() for i in range(3)] == [
            heading[1] for heading in headings
        ]
        assert [view._list.item(i).data(_TARGET_ROLE) for i in range(3)] == [
            "title",
            "account-setup",
            "deep",
        ]
        assert [view._list.item(i).data(_LEVEL_ROLE) for i in range(3)] == [
            1,
            2,
            20,
        ]
        assert [view._list.item(i).data(_DEPTH_ROLE) for i in range(3)] == [
            0,
            1,
            _MAX_TOC_DEPTH,
        ]
        assert view._list.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert view._list.textElideMode() == Qt.TextElideMode.ElideRight
        assert view._list.wordWrap() is False

        view._list.itemClicked.emit(view._list.item(1))
        assert clicked == ["account-setup"]

        view.update_headings([])
        empty = view._list.item(0)
        assert empty.text() == "目前文件沒有標題"
        assert not (empty.flags() & Qt.ItemFlag.ItemIsEnabled)
    finally:
        view.close()


def test_pdf_first_outline_page_is_clickable(qapp):
    clicked = []
    view = TocView(clicked.append)
    try:
        view.update_outline([(1, "Cover", 0), (2, "Chapter", 4)])

        assert view._list.item(0).data(_TARGET_ROLE) == 0
        assert view._list.item(1).data(_TARGET_ROLE) == 4
        view._list.itemClicked.emit(view._list.item(0))
        assert clicked == [0]
    finally:
        view.close()


def test_toc_delegate_indents_elides_and_renders_in_narrow_panel(qapp):
    view = TocView(lambda _target: None)
    long_title = "A heading that is deliberately much wider than the sidebar"
    try:
        view.update_headings(
            [
                (1, "Title", "title"),
                (2, "Section", "section"),
                (3, "Subsection", "subsection"),
                (6, long_title, "deep"),
            ]
        )
        view.resize(180, 240)
        view.show()
        qapp.processEvents()

        rect = QRectF(view._list.visualItemRect(view._list.item(0)))
        text_rects = [
            view._delegate.text_rect(rect, depth)
            for depth in (0, 1, 2, _MAX_TOC_DEPTH)
        ]
        assert [
            text_rects[i + 1].left() - text_rects[i].left()
            for i in range(3)
        ] == [
            _TOC_INDENT,
            _TOC_INDENT,
            _TOC_INDENT * (_MAX_TOC_DEPTH - 2),
        ]
        assert all(text_rect.width() > 0 for text_rect in text_rects)
        rtl_rects = [
            view._delegate.text_rect(
                rect, depth, Qt.LayoutDirection.RightToLeft
            )
            for depth in (0, 1, 2, _MAX_TOC_DEPTH)
        ]
        assert [
            rtl_rects[i].right() - rtl_rects[i + 1].right()
            for i in range(3)
        ] == [
            _TOC_INDENT,
            _TOC_INDENT,
            _TOC_INDENT * (_MAX_TOC_DEPTH - 2),
        ]
        assert view._list.visualItemRect(view._list.item(0)).height() >= (
            _TOC_ROW_HEIGHT
        )

        deep_index = view._list.indexFromItem(view._list.item(3))
        deep_font = view._delegate._font_for(
            view._list.font(), view._delegate._depth(deep_index)
        )
        assert QFontMetrics(deep_font).horizontalAdvance(long_title) > (
            text_rects[-1].width()
        )
        assert view._list.viewport().grab().isNull() is False
    finally:
        view.close()


def test_active_anchor_and_theme_update_preserve_items_and_selection(qapp):
    clicked = []
    view = TocView(clicked.append)
    try:
        view.update_headings(
            [
                (1, "Title", "title"),
                (2, "Section", "section"),
                (3, "Step", "step"),
            ]
        )
        active_item = view._list.item(2)

        view.set_active_anchor("step")
        assert view._list.currentItem() is active_item
        assert active_item.isSelected() is True
        assert clicked == []

        view.apply_theme(DARK)
        assert view._list.item(2) is active_item
        assert view._list.currentItem() is active_item
        assert active_item.isSelected() is True
        assert view._delegate._theme == DARK
        assert view._delegate._guide_color.name() == QColor(DARK.border).name()
        assert view._delegate._guide_color.alpha() == 170

        view.set_active_anchor("missing")
        assert view._list.currentItem() is None
        assert view._list.selectedItems() == []

        view.update_outline([(1, "Cover", 0)])
        view.set_active_anchor(0)
        assert view._list.currentItem() is view._list.item(0)
        view.set_active_anchor("")
        assert view._list.currentItem() is None
    finally:
        view.close()
