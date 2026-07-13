"""GUI tests for the colored, named tag-pill delegate in TagsPanel.

Each ``kind == "tag"`` node in the "標籤" tab renders as a filled pill (the
tag's color) carrying the tag name plus a muted "· N" count, matching the file
browser's pills. The "全部（清除篩選）" row and the lazily-loaded file children
keep the default look. These run offscreen via the shared ``qapp`` fixture
(see tests/conftest.py).
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyleOptionViewItem

from app.tags_panel import (
    _COUNT_ROLE,
    TagsPanel,
    _TagNodeDelegate,
    _pill_text_color,
    _relative_luminance,
)


def _color_for(tag: str) -> str:
    # Yellow stays a light fill; navy is a dark fill.
    return {"urgent": "#F5B70A", "review": "#12305a"}.get(tag, "#8B8D98")


def _top_items(panel):
    tree = panel._tree
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def _size_hint(delegate, tree, item):
    opt = QStyleOptionViewItem()
    index = tree.indexFromItem(item)
    delegate.initStyleOption(opt, index)
    return delegate.sizeHint(opt, index)


def test_tag_nodes_store_count_on_a_dedicated_role(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        tag_color_for=_color_for,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("urgent", 3), ("review", 1), ("draft", 0)])

    tag_items = _top_items(panel)[1:]  # skip the 全部 row
    counts = [it.data(0, _COUNT_ROLE) for it in tag_items]
    assert counts == [3, 1, 0]
    assert all(isinstance(c, int) for c in counts)


def test_tag_nodes_no_longer_carry_a_swatch_icon(qapp):
    # The pill now conveys the color, so the old dot icon must be gone.
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        tag_color_for=_color_for,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("urgent", 3)])
    urgent = _top_items(panel)[1]
    assert urgent.icon(0).isNull()


def test_tree_uses_tag_node_delegate(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        tag_color_for=_color_for,
        files_for_tag=lambda _t: [],
    )
    assert isinstance(panel._tree.itemDelegate(), _TagNodeDelegate)
    # Variable row heights so the taller pill row is not clipped.
    assert panel._tree.uniformRowHeights() is False


def test_delegate_reads_tag_and_count_only_for_tag_nodes(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        tag_color_for=_color_for,
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("urgent", 5)])
    delegate = panel._tree.itemDelegate()
    tree = panel._tree

    all_row, urgent = _top_items(panel)
    # Tag node resolves to (tag, count); the 全部 row does not.
    assert delegate._tag_node(tree.indexFromItem(urgent)) == ("urgent", 5)
    assert delegate._tag_node(tree.indexFromItem(all_row)) is None

    # File children keep the default look, too.
    urgent.setExpanded(True)
    child = urgent.child(0)
    assert delegate._tag_node(tree.indexFromItem(child)) is None


def test_tag_node_is_taller_than_the_all_row(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        tag_color_for=_color_for,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("urgent", 2)])
    delegate = panel._tree.itemDelegate()
    tree = panel._tree

    all_row, urgent = _top_items(panel)
    tag_h = _size_hint(delegate, tree, urgent).height()
    all_h = _size_hint(delegate, tree, all_row).height()
    # The pill row reserves extra height; the plain 全部 row stays default.
    assert tag_h >= all_h
    assert tag_h >= 24


def test_contrast_helper_picks_dark_on_light_and_white_on_dark(qapp):
    # Yellow is light enough to demand dark text (readability).
    assert _pill_text_color("#F5B70A") == QColor("#1a1a1a")
    # A dark navy demands white text.
    assert _pill_text_color("#12305a") == QColor("#ffffff")
    # Pure black / white sanity checks.
    assert _pill_text_color("#000000") == QColor("#ffffff")
    assert _pill_text_color("#ffffff") == QColor("#1a1a1a")
    # Luminance is monotone: light hex > dark hex.
    assert _relative_luminance(QColor("#F5B70A")) > _relative_luminance(
        QColor("#12305a")
    )
