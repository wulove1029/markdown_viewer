"""Tests for the second-line named tag-pill delegate in the file browser."""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem

from app.annotations import DocumentAnnotations
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import (
    _IS_DIR_ROLE,
    _PATH_ROLE,
    _TAGS_ROLE,
    FileBrowserView,
    _TagPillDelegate,
    _pill_text_color,
    _relative_luminance,
)
from app.tag_index import TagIndex


def _color_for(tag: str) -> str:
    # Yellow stays a light fill; navy is a dark fill.
    return {"focus": "#F5B70A", "deep": "#12305a"}.get(tag, "#888888")


def _make_view(tmp_path, monkeypatch, libraries, tag_index=None):
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save(libraries)
    monkeypatch.setattr("app.file_browser.DocumentLibraryStore", lambda: store)
    return FileBrowserView(
        lambda _path: None, tag_index=tag_index, tag_color_for=_color_for
    )


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


def test_delegate_reads_tags_from_role(qapp):
    tree = QTreeWidget()
    delegate = _TagPillDelegate(_color_for, tree)

    tagged = QTreeWidgetItem(["tagged.md"])
    tagged.setData(0, _IS_DIR_ROLE, False)
    tagged.setData(0, _TAGS_ROLE, ["deep", "focus"])
    tree.addTopLevelItem(tagged)

    untagged = QTreeWidgetItem(["plain.md"])
    untagged.setData(0, _IS_DIR_ROLE, False)
    untagged.setData(0, _TAGS_ROLE, [])
    tree.addTopLevelItem(untagged)

    folder = QTreeWidgetItem(["a folder"])
    folder.setData(0, _IS_DIR_ROLE, True)
    folder.setData(0, _TAGS_ROLE, ["deep"])  # folders never show pills
    tree.addTopLevelItem(folder)

    tagged_idx = tree.indexFromItem(tagged)
    untagged_idx = tree.indexFromItem(untagged)
    folder_idx = tree.indexFromItem(folder)

    assert delegate._tags(tagged_idx) == ["deep", "focus"]
    assert delegate._tags(untagged_idx) == []
    assert delegate._tags(folder_idx) == []


def test_delegate_without_color_callback_renders_no_pills(qapp):
    tree = QTreeWidget()
    delegate = _TagPillDelegate(None, tree)
    item = QTreeWidgetItem(["tagged.md"])
    item.setData(0, _IS_DIR_ROLE, False)
    item.setData(0, _TAGS_ROLE, ["focus"])
    tree.addTopLevelItem(item)
    # No color callback => behave like an untagged row (backward compatible).
    assert delegate._tags(tree.indexFromItem(item)) == []


def _size_hint(delegate, tree, item):
    opt = QStyleOptionViewItem()
    index = tree.indexFromItem(item)
    delegate.initStyleOption(opt, index)
    return delegate.sizeHint(opt, index)


def test_tagged_row_is_taller_than_untagged_row(qapp):
    tree = QTreeWidget()
    delegate = _TagPillDelegate(_color_for, tree)
    tree.setItemDelegate(delegate)

    tagged = QTreeWidgetItem(["tagged.md"])
    tagged.setData(0, _IS_DIR_ROLE, False)
    tagged.setData(0, _TAGS_ROLE, ["focus"])
    tree.addTopLevelItem(tagged)

    untagged = QTreeWidgetItem(["plain.md"])
    untagged.setData(0, _IS_DIR_ROLE, False)
    untagged.setData(0, _TAGS_ROLE, [])
    tree.addTopLevelItem(untagged)

    tagged_h = _size_hint(delegate, tree, tagged).height()
    untagged_h = _size_hint(delegate, tree, untagged).height()
    assert tagged_h > untagged_h


def test_view_uses_pill_delegate_and_non_uniform_rows(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    tagged = root / "tagged.md"
    plain = root / "plain.md"
    tagged.write_text("# tagged", encoding="utf-8")
    plain.write_text("# plain", encoding="utf-8")

    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(tagged, DocumentAnnotations(doc_tags=["focus", "deep"]))

    view = _make_view(
        tmp_path,
        monkeypatch,
        [DocumentLibrary("lib", "Vault", str(root))],
        tag_index=tag_index,
    )
    try:
        assert isinstance(view._tree.itemDelegate(), _TagPillDelegate)
        assert view._tree.uniformRowHeights() is False

        tagged_item = view._find_item(tagged)
        plain_item = view._find_item(plain)
        assert tagged_item is not None and plain_item is not None
        # The tagged file row carries its tags on the role for pill painting.
        assert tagged_item.data(0, _TAGS_ROLE) == ["deep", "focus"]
        assert not plain_item.data(0, _TAGS_ROLE)
    finally:
        view.close()


def test_update_file_tags_refreshes_only_affected_rows(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    a = root / "a.md"
    b = root / "b.md"
    a.write_text("# a", encoding="utf-8")
    b.write_text("# b", encoding="utf-8")

    tag_index = TagIndex(tmp_path / "tags.json")
    view = _make_view(
        tmp_path,
        monkeypatch,
        [DocumentLibrary("lib", "Vault", str(root))],
        tag_index=tag_index,
    )
    try:
        a_item = view._find_item(a)
        b_item = view._find_item(b)
        assert a_item is not None and b_item is not None
        assert not a_item.data(0, _TAGS_ROLE)

        # Assign a tag out-of-band, then incrementally update just that row.
        tag_index.update(a, DocumentAnnotations(doc_tags=["focus"]))
        view.update_file_tags([a])
        assert a_item.data(0, _TAGS_ROLE) == ["focus"]
        # The untouched row keeps its (empty) tags.
        assert not b_item.data(0, _TAGS_ROLE)

        # Removing the tag clears the pill role again.
        tag_index.update(a, DocumentAnnotations(doc_tags=[]))
        view.update_file_tags([a])
        assert not a_item.data(0, _TAGS_ROLE)

        # A path not in the tree is silently skipped (no raise, no-op).
        view.update_file_tags([root / "ghost.md"])
    finally:
        view.close()
