"""GUI tests for the tag tree in TagsPanel.

Each tag is an expandable node whose children are the files (MD + PDF) that
carry it, loaded lazily via *files_for_tag* the first time the node expands.
Clicking a tag toggles its files open in place; clicking a file opens it
through the shared open-file callback. These run offscreen via the shared
``qapp`` fixture (see tests/conftest.py).
"""

from pathlib import Path

from PySide6.QtCore import Qt

from app.tags_panel import TagsPanel


def _top_items(panel):
    tree = panel._tree
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def _data(item):
    return item.data(0, Qt.ItemDataRole.UserRole) or {}


def test_set_tags_builds_all_row_plus_one_node_per_tag(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 3), ("todo", 1)])

    items = _top_items(panel)
    # "全部（清除篩選）" + one node per tag.
    assert len(items) == 3
    assert _data(items[0])["kind"] == "all"
    assert _data(items[0])["tag"] == ""
    assert [_data(it)["tag"] for it in items[1:]] == ["focus", "todo"]
    assert all(_data(it)["kind"] == "tag" for it in items[1:])
    # The "全部" row is never expandable (no children).
    assert items[0].childCount() == 0


def test_expanding_a_tag_lazy_loads_its_files(qapp):
    files = {"focus": [Path("/docs/a.md"), Path("/docs/sub/report.pdf")]}
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        files_for_tag=lambda tag: files.get(tag, []),
    )
    panel.set_tags([("focus", 2)])

    focus = _top_items(panel)[1]
    # Collapsed: only the lazy-load placeholder is attached.
    assert focus.childCount() == 1
    assert _data(focus.child(0))["kind"] == "placeholder"

    focus.setExpanded(True)  # fires itemExpanded -> lazy load

    assert focus.childCount() == 2
    assert [_data(focus.child(i))["kind"] for i in range(2)] == ["file", "file"]
    # Child text shows the type prefix + file name; UserRole carries the Path.
    assert "MD" in focus.child(0).text(0)
    assert "a.md" in focus.child(0).text(0)
    assert "PDF" in focus.child(1).text(0)
    assert "report.pdf" in focus.child(1).text(0)
    assert _data(focus.child(1))["path"] == Path("/docs/sub/report.pdf")
    assert focus.child(1).toolTip(0) == str(Path("/docs/sub/report.pdf"))


def test_clicking_a_file_child_opens_it(qapp):
    opened: list[Path] = []
    target = Path("/docs/sub/report.pdf")
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_open_file=lambda path: opened.append(path),
        files_for_tag=lambda _t: [Path("/docs/a.md"), target],
    )
    panel.set_tags([("focus", 2)])
    focus = _top_items(panel)[1]
    focus.setExpanded(True)

    # Single click (matching the file browser) opens the file.
    panel._tree.itemClicked.emit(focus.child(1), 0)

    assert opened == [target]
    assert isinstance(opened[0], Path)


def test_clicking_a_tag_selects_and_toggles_expansion(qapp):
    selected: list[str] = []
    panel = TagsPanel(
        on_tag_selected=lambda tag: selected.append(tag),
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("focus", 1)])
    focus = _top_items(panel)[1]
    assert focus.isExpanded() is False

    panel._tree.itemClicked.emit(focus, 0)
    assert selected == ["focus"]
    assert focus.isExpanded() is True  # first click expands + loads children
    assert focus.childCount() == 1
    assert _data(focus.child(0))["kind"] == "file"

    panel._tree.itemClicked.emit(focus, 0)
    assert selected == ["focus", "focus"]
    assert focus.isExpanded() is False  # second click collapses


def test_clicking_all_row_clears_filter(qapp):
    selected: list[str] = []
    panel = TagsPanel(
        on_tag_selected=lambda tag: selected.append(tag),
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 1)])
    all_row = _top_items(panel)[0]

    panel._tree.itemClicked.emit(all_row, 0)
    assert selected == [""]


def test_set_active_highlights_matching_node(qapp):
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 1), ("todo", 1)])

    panel.set_active("todo")
    current = panel._tree.currentItem()
    assert _data(current)["tag"] == "todo"

    panel.set_active("")  # "" selects the 全部 row
    assert _data(panel._tree.currentItem())["kind"] == "all"


def test_rebuild_restores_previously_expanded_tag(qapp):
    calls: list[str] = []

    def files_for(tag):
        calls.append(tag)
        return [Path("/docs/a.md")]

    panel = TagsPanel(on_tag_selected=lambda _t: None, files_for_tag=files_for)
    panel.set_tags([("focus", 1)])
    _top_items(panel)[1].setExpanded(True)
    assert calls == ["focus"]

    # A count change rebuilds the tree; the open tag stays open and reloads.
    panel.set_tags([("focus", 2)])
    focus = _top_items(panel)[1]
    assert focus.isExpanded() is True
    assert focus.childCount() == 1
    assert _data(focus.child(0))["kind"] == "file"
    assert calls == ["focus", "focus"]


def test_set_tag_files_is_a_harmless_noop(qapp):
    # Backward-compat shim: old callers may still call it; it must not raise
    # and must not create any stray rows.
    panel = TagsPanel(on_tag_selected=lambda _t: None)
    panel.set_tags([("focus", 1)])
    before = panel._tree.topLevelItemCount()

    panel.set_tag_files([Path("/docs/a.md")])
    panel.set_tag_files([])

    assert panel._tree.topLevelItemCount() == before
