"""GUI tests for the file-child context menu in the 標籤 (tags) tab.

Expanding a tag node lists the files carrying it; those file children now get
the same right-click file operations as the 檔案 tab (開啟文件 / 管理標籤… /
重新命名 / 移動到… / 刪除 / 在檔案總管顯示). The rename/move/delete actions must
route back through ``FileBrowserView`` so the shared tag index and every view
stay consistent -- this module verifies both the menu shape and that routing.

Runs offscreen via the shared ``qapp`` fixture (see tests/conftest.py); modal
dialogs are monkeypatched.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from app.annotations import DocumentAnnotations
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import FileBrowserView
from app.tag_index import TagIndex
from app.tags_panel import TagsPanel

_FILE_ACTIONS = [
    "開啟文件",
    "管理標籤…",
    "重新命名",
    "移動到…",
    "刪除",
    "在檔案總管顯示",
]


def _make_browser(tmp_path, monkeypatch, root, tag_index):
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save([DocumentLibrary("lib", "Vault", str(root))])
    monkeypatch.setattr("app.file_browser.DocumentLibraryStore", lambda: store)
    return FileBrowserView(lambda _p: None, tag_index=tag_index)


def _file_child(panel, tag: str):
    """Expand *tag* and return its first file child item."""
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


def _child_path(child) -> Path:
    return child.data(0, Qt.ItemDataRole.UserRole)["path"]


# --------------------------------------------------------------------------
# (b) menu shape per node kind
# --------------------------------------------------------------------------
def test_file_child_menu_has_all_six_actions(qapp):
    calls: dict[str, object] = {}
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_open_file=lambda p: calls.__setitem__("open", p),
        on_manage_tags=lambda p: calls.__setitem__("manage", p),
        on_rename_file=lambda p: calls.__setitem__("rename", p),
        on_move_file=lambda p: calls.__setitem__("move", p),
        on_delete_file=lambda p: calls.__setitem__("delete", p),
        on_reveal_file=lambda p: calls.__setitem__("reveal", p),
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("focus", 1)])
    child = _file_child(panel, "focus")

    menu = panel._build_file_menu(_child_path(child))
    assert [a.text() for a in menu.actions()] == _FILE_ACTIONS

    # Each action forwards the file's Path to the matching callback.
    by_text = {a.text(): a for a in menu.actions()}
    by_text["重新命名"].trigger()
    by_text["移動到…"].trigger()
    by_text["刪除"].trigger()
    by_text["在檔案總管顯示"].trigger()
    by_text["開啟文件"].trigger()
    by_text["管理標籤…"].trigger()
    assert calls["rename"] == Path("/docs/a.md")
    assert calls["move"] == Path("/docs/a.md")
    assert calls["delete"] == Path("/docs/a.md")
    assert calls["reveal"] == Path("/docs/a.md")
    assert calls["open"] == Path("/docs/a.md")
    # 管理標籤… passes a list[Path].
    assert calls["manage"] == [Path("/docs/a.md")]


def test_file_child_menu_omits_missing_callbacks(qapp):
    # Only open + delete wired: the other four actions must not appear.
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_open_file=lambda _p: None,
        on_delete_file=lambda _p: None,
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("focus", 1)])
    child = _file_child(panel, "focus")

    menu = panel._build_file_menu(_child_path(child))
    assert [a.text() for a in menu.actions()] == ["開啟文件", "刪除"]


def test_tag_node_menu_is_delete_tag(qapp):
    deleted: list[str] = []
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_delete_tag=lambda tag: deleted.append(tag),
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 1)])

    menu = panel._build_tag_menu("focus")
    assert [a.text() for a in menu.actions()] == ["刪除標籤"]
    menu.actions()[0].trigger()
    assert deleted == ["focus"]


def test_tag_node_menu_empty_without_delete_callback(qapp):
    panel = TagsPanel(on_tag_selected=lambda _t: None, files_for_tag=lambda _t: [])
    panel.set_tags([("focus", 1)])
    assert panel._build_tag_menu("focus").actions() == []


def test_context_menu_dispatch_by_kind(qapp):
    """kind==tag -> delete menu, kind==file -> file menu, others -> no menu."""
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_delete_tag=lambda _t: None,
        on_open_file=lambda _p: None,
        on_rename_file=lambda _p: None,
        on_move_file=lambda _p: None,
        on_delete_file=lambda _p: None,
        on_reveal_file=lambda _p: None,
        on_manage_tags=lambda _p: None,
        files_for_tag=lambda _t: [Path("/docs/a.md")],
    )
    panel.set_tags([("focus", 1)])

    all_row = panel._tree.topLevelItem(0)
    assert (all_row.data(0, Qt.ItemDataRole.UserRole) or {})["kind"] == "all"
    assert panel._menu_for_item(all_row) is None  # 全部（清除篩選）: no menu
    assert panel._menu_for_item(None) is None

    tag_node = panel._tree.topLevelItem(1)
    tag_menu = panel._menu_for_item(tag_node)
    assert [a.text() for a in tag_menu.actions()] == ["刪除標籤"]

    child = _file_child(panel, "focus")
    file_menu = panel._menu_for_item(child)
    assert [a.text() for a in file_menu.actions()] == _FILE_ACTIONS


# --------------------------------------------------------------------------
# (a) file operations route through FileBrowserView + migrate the tag index
# --------------------------------------------------------------------------
def test_panel_rename_routes_through_browser_and_migrates_index(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "old.md"
    target.write_text("# old", encoding="utf-8")
    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(target, DocumentAnnotations(doc_tags=["focus"]))

    browser = _make_browser(tmp_path, monkeypatch, root, tag_index)
    monkeypatch.setattr(
        "app.file_browser.QInputDialog.getText",
        staticmethod(lambda *a, **k: ("new", True)),
    )
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_rename_file=browser.rename_file,  # mirrors window._rename_path
        files_for_tag=lambda _t: [target],
    )
    panel.set_tags([("focus", 1)])
    child = _file_child(panel, "focus")
    try:
        menu = panel._build_file_menu(_child_path(child))
        {a.text(): a for a in menu.actions()}["重新命名"].trigger()

        new_path = root / "new.md"
        assert new_path.exists()
        assert not target.exists()
        keys = tag_index.files_with_tag("focus")
        assert str(new_path.resolve()) in keys
        assert str(target.resolve()) not in keys
    finally:
        browser.close()


def test_panel_move_routes_through_browser_and_migrates_index(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    dest = root / "archive"
    dest.mkdir(parents=True)
    target = root / "note.md"
    target.write_text("# note", encoding="utf-8")
    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(target, DocumentAnnotations(doc_tags=["focus"]))

    browser = _make_browser(tmp_path, monkeypatch, root, tag_index)
    monkeypatch.setattr(
        "app.file_browser.QFileDialog.getExistingDirectory",
        staticmethod(lambda *a, **k: str(dest)),
    )
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_move_file=browser.move_file,  # mirrors window._move_path
        files_for_tag=lambda _t: [target],
    )
    panel.set_tags([("focus", 1)])
    child = _file_child(panel, "focus")
    try:
        menu = panel._build_file_menu(_child_path(child))
        {a.text(): a for a in menu.actions()}["移動到…"].trigger()

        moved = dest / "note.md"
        assert moved.exists()
        assert not target.exists()
        keys = tag_index.files_with_tag("focus")
        assert str(moved.resolve()) in keys
        assert str(target.resolve()) not in keys
    finally:
        browser.close()


def test_panel_delete_routes_through_browser_and_removes_from_index(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "gone.md"
    target.write_text("# gone", encoding="utf-8")
    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(target, DocumentAnnotations(doc_tags=["focus"]))
    assert tag_index.files_with_tag("focus")

    browser = _make_browser(tmp_path, monkeypatch, root, tag_index)
    monkeypatch.setattr(
        "app.file_browser.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    # Delete permanently so nothing lands in the real recycle bin.
    monkeypatch.setattr("app.file_ops._send2trash", None)
    monkeypatch.setattr("app.file_ops.HAS_SEND2TRASH", False)

    deleted: list[list] = []
    browser.on_paths_deleted = lambda paths: deleted.append(paths)
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_delete_file=browser.delete_file,  # mirrors window._delete_path
        files_for_tag=lambda _t: [target],
    )
    panel.set_tags([("focus", 1)])
    child = _file_child(panel, "focus")
    try:
        menu = panel._build_file_menu(_child_path(child))
        {a.text(): a for a in menu.actions()}["刪除"].trigger()

        assert not target.exists()
        assert tag_index.files_with_tag("focus") == []
        assert deleted == [[str(target)]]
    finally:
        browser.close()
