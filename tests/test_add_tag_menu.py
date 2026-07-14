"""GUI tests for the "加入標籤…" quick-tag entry points and the removal of the
標籤 tab's ＋ create-tag button.

Covers the 檔案 tab's file context menu ("加入標籤…" sits before "管理標籤…"),
the ``_selected_file_paths`` multi-select / fallback helper, the matching
file-child entry in the 標籤 tab, and that ``TagsPanel`` no longer builds any
create-tag button. Runs offscreen via the shared ``qapp`` fixture
(see tests/conftest.py); the modal QMenu.exec is stubbed.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton, QTreeWidgetItemIterator

from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import _IS_DIR_ROLE, _PATH_ROLE, FileBrowserView
from app.tags_panel import TagsPanel


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _make_browser(tmp_path, monkeypatch, root, **kwargs):
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save([DocumentLibrary("lib", "Vault", str(root))])
    monkeypatch.setattr("app.file_browser.DocumentLibraryStore", lambda: store)
    return FileBrowserView(lambda _p: None, **kwargs)


def _file_items(browser) -> dict[str, object]:
    """Map file-row name -> tree item (folders excluded)."""
    out: dict[str, object] = {}
    it = QTreeWidgetItemIterator(browser._tree)
    while it.value():
        item = it.value()
        if item.data(0, _IS_DIR_ROLE) is False:
            raw = item.data(0, _PATH_ROLE)
            if raw:
                out[Path(raw).name] = item
        it += 1
    return out


def _folder_item(browser, name: str):
    it = QTreeWidgetItemIterator(browser._tree)
    while it.value():
        item = it.value()
        if item.data(0, _IS_DIR_ROLE) and Path(str(item.data(0, _PATH_ROLE))).name == name:
            return item
        it += 1
    return None


# --------------------------------------------------------------------------
# 檔案 tab: context menu shape
# --------------------------------------------------------------------------
def test_file_menu_has_add_tag_before_manage_tags(tmp_path, monkeypatch, qapp):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# note", encoding="utf-8")

    browser = _make_browser(
        tmp_path,
        monkeypatch,
        root,
        on_add_tag=lambda _paths: None,
        on_manage_tags=lambda _paths: None,
    )
    try:
        file_item = _file_items(browser)["note.md"]
        menu = browser._build_context_menu(file_item)
        texts = [a.text() for a in menu.actions()]

        assert "加入標籤…" in texts
        assert "管理標籤…" in texts
        # 加入標籤… must sit immediately before 管理標籤….
        assert texts.index("加入標籤…") < texts.index("管理標籤…")
    finally:
        browser.close()


def test_file_menu_omits_add_tag_without_callback(tmp_path, monkeypatch, qapp):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# note", encoding="utf-8")

    # on_add_tag not wired -> the action must not appear.
    browser = _make_browser(
        tmp_path, monkeypatch, root, on_manage_tags=lambda _paths: None
    )
    try:
        file_item = _file_items(browser)["note.md"]
        menu = browser._build_context_menu(file_item)
        texts = [a.text() for a in menu.actions()]

        assert "加入標籤…" not in texts
        assert "管理標籤…" in texts
    finally:
        browser.close()


def test_file_menu_add_tag_forwards_selected_paths(tmp_path, monkeypatch, qapp):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("# a", encoding="utf-8")
    (root / "b.md").write_text("# b", encoding="utf-8")

    received: list[list[Path]] = []
    browser = _make_browser(
        tmp_path, monkeypatch, root, on_add_tag=lambda paths: received.append(paths)
    )
    try:
        items = _file_items(browser)
        # Multi-select both files; the right-click lands on a.md.
        items["a.md"].setSelected(True)
        items["b.md"].setSelected(True)

        menu = browser._build_context_menu(items["a.md"])
        {a.text(): a for a in menu.actions()}["加入標籤…"].trigger()

        assert len(received) == 1
        assert {p.name for p in received[0]} == {"a.md", "b.md"}
    finally:
        browser.close()


# --------------------------------------------------------------------------
# _selected_file_paths: multi-select / folder exclusion / fallback
# --------------------------------------------------------------------------
def test_selected_file_paths_multi_and_fallback(tmp_path, monkeypatch, qapp):
    root = tmp_path / "vault"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.md").write_text("# a", encoding="utf-8")
    (root / "b.md").write_text("# b", encoding="utf-8")
    (sub / "c.md").write_text("# c", encoding="utf-8")

    browser = _make_browser(tmp_path, monkeypatch, root)
    try:
        files = _file_items(browser)

        # No selection + fallback -> just the fallback path.
        fallback = Path(root / "a.md")
        assert browser._selected_file_paths(fallback=fallback) == [fallback]
        # No selection + no fallback -> empty.
        assert browser._selected_file_paths() == []

        # Two files selected -> both returned.
        files["a.md"].setSelected(True)
        files["b.md"].setSelected(True)
        picked = browser._selected_file_paths()
        assert {p.name for p in picked} == {"a.md", "b.md"}

        # A folder in the selection is excluded; only the file survives.
        browser._tree.clearSelection()
        folder = _folder_item(browser, "sub")
        assert folder is not None
        folder.setSelected(True)
        files["a.md"].setSelected(True)
        picked = browser._selected_file_paths(fallback=fallback)
        assert {p.name for p in picked} == {"a.md"}
    finally:
        browser.close()


# --------------------------------------------------------------------------
# 標籤 tab: file-child menu gains 加入標籤…; header has no ＋ button
# --------------------------------------------------------------------------
def _tag_file_child(panel, tag: str):
    node = None
    for i in range(panel._tree.topLevelItemCount()):
        item = panel._tree.topLevelItem(i)
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") == "tag" and data.get("tag") == tag:
            node = item
            break
    assert node is not None
    node.setExpanded(True)
    return node.child(0)


def test_tags_panel_file_menu_includes_add_tag(qapp):
    calls: dict[str, object] = {}
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_open_file=lambda _p: None,
        on_add_tag=lambda p: calls.__setitem__("add", p),
        on_manage_tags=lambda _p: None,
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("focus", 1)])
    child = _tag_file_child(panel, "focus")

    menu = panel._build_file_menu(child.data(0, Qt.ItemDataRole.UserRole)["path"])
    texts = [a.text() for a in menu.actions()]
    assert "加入標籤…" in texts
    assert texts.index("加入標籤…") < texts.index("管理標籤…")

    {a.text(): a for a in menu.actions()}["加入標籤…"].trigger()
    assert calls["add"] == [Path("/docs/a.md")]


def test_tags_panel_header_has_no_create_tag_button(qapp):
    panel = TagsPanel(on_tag_selected=lambda _t: None, files_for_tag=lambda _t: [])

    # The create-tag callback/button were removed entirely.
    assert not hasattr(panel, "_on_create_tag")
    assert not hasattr(panel, "_add_btn")
    # No QPushButton survives anywhere in the panel (the ＋ button is gone).
    assert panel.findChildren(QPushButton) == []
    # The removed kwarg must now be rejected.
    with pytest.raises(TypeError):
        TagsPanel(on_tag_selected=lambda _t: None, on_create_tag=lambda: None)
