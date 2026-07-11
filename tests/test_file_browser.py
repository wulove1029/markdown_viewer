from PySide6.QtWidgets import QMessageBox, QTreeWidgetItemIterator

from app.annotations import DocumentAnnotations
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import _IS_DIR_ROLE, _PATH_ROLE, FileBrowserView
from app.tag_index import TagIndex


def _iter_items(view: FileBrowserView):
    iterator = QTreeWidgetItemIterator(view._tree)
    while iterator.value():
        yield iterator.value()
        iterator += 1


def _visible_file_paths(view: FileBrowserView) -> list[str]:
    return [
        item.data(0, _PATH_ROLE)
        for item in _iter_items(view)
        if item.data(0, _PATH_ROLE) and not item.data(0, _IS_DIR_ROLE)
    ]


def _visible_texts(view: FileBrowserView) -> list[str]:
    return [item.text(0) for item in _iter_items(view)]


def _make_view(tmp_path, monkeypatch, libraries, tag_index=None):
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save(libraries)
    monkeypatch.setattr("app.file_browser.DocumentLibraryStore", lambda: store)
    return FileBrowserView(lambda _path: None, tag_index=tag_index)


def test_tag_filter_keeps_matching_library_and_survives_refresh(
    qapp, tmp_path, monkeypatch
):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    tagged = first_root / "tagged.md"
    untagged = first_root / "untagged.md"
    other = second_root / "other.md"
    for path in (tagged, untagged, other):
        path.write_text(f"# {path.stem}", encoding="utf-8")

    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(tagged, DocumentAnnotations(doc_tags=["focus"]))

    view = _make_view(
        tmp_path,
        monkeypatch,
        [
            DocumentLibrary("first", "First", str(first_root)),
            DocumentLibrary("second", "Second", str(second_root)),
        ],
        tag_index=tag_index,
    )
    try:
        assert view.has_open_folder() is True

        view.set_tag_filter("focus")
        assert _visible_file_paths(view) == [str(tagged)]
        assert "First（1）" in _visible_texts(view)
        assert not any(
            text.startswith("Second（") for text in _visible_texts(view)
        )

        view.refresh_libraries()
        assert _visible_file_paths(view) == [str(tagged)]

        view.set_tag_filter("missing")
        assert _visible_file_paths(view) == []
        assert "沒有符合標籤的檔案" in _visible_texts(view)

        view.set_tag_filter("")
        assert set(_visible_file_paths(view)) == {
            str(tagged),
            str(untagged),
            str(other),
        }
        assert {"First（2）", "Second（1）"}.issubset(
            _visible_texts(view)
        )
    finally:
        view.close()


def test_tree_shows_folders_and_files_nested(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    sub = root / "inbox"
    sub.mkdir(parents=True)
    (root / "top.md").write_text("# top", encoding="utf-8")
    (sub / "nested.md").write_text("# nested", encoding="utf-8")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        texts = _visible_texts(view)
        assert "Vault（2）" in texts
        assert "inbox" in texts
        assert "top.md" in texts
        assert "nested.md" in texts

        folder_item = view._find_item(sub)
        assert folder_item is not None
        assert folder_item.data(0, _IS_DIR_ROLE) is True
        file_item = view._find_item(sub / "nested.md")
        assert file_item is not None
        assert file_item.parent() is folder_item
    finally:
        view.close()


def test_tree_prunes_empty_folders_but_keeps_deep_supported_files(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    empty = root / "backend" / "db"
    docs = root / "firmware" / "docs"
    empty.mkdir(parents=True)
    docs.mkdir(parents=True)
    (docs / "guide.md").write_text("# guide", encoding="utf-8")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        assert view._find_item(root) is not None
        assert view._find_item(root / "backend") is None
        assert view._find_item(empty) is None
        assert view._find_item(root / "firmware") is not None
        assert view._find_item(docs / "guide.md") is not None
    finally:
        view.close()


def test_tree_applies_user_directory_exclusions(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    visible = root / "docs"
    excluded = root / "app_flutter" / "ios"
    visible.mkdir(parents=True)
    excluded.mkdir(parents=True)
    (visible / "keep.md").write_text("keep", encoding="utf-8")
    (excluded / "hidden.md").write_text("hidden", encoding="utf-8")
    monkeypatch.setattr(
        "app.file_browser.load_excluded_folders",
        lambda: ["app_flutter/ios"],
    )

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        assert view._find_item(visible / "keep.md") is not None
        assert view._find_item(excluded) is None
    finally:
        view.close()


def test_empty_library_root_remains_visible(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        root_item = view._find_item(root)
        assert root_item is not None
        assert root_item.data(0, _IS_DIR_ROLE) is True
        assert root_item.text(0) == "Vault（0）"
    finally:
        view.close()


def test_new_empty_folder_stays_reachable_for_current_session(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    monkeypatch.setattr(
        "app.file_browser.QInputDialog.getText",
        staticmethod(lambda *args, **kwargs: ("drafts", True)),
    )
    try:
        view._create_folder_action(str(root))
        created = root / "drafts"
        item = view._find_item(created)
        assert created.is_dir()
        assert item is not None
        assert item.data(0, _IS_DIR_ROLE) is True

        (created / "note.md").write_text("# note", encoding="utf-8")
        view.refresh_libraries()
        assert view._find_item(created / "note.md") is not None
    finally:
        view.close()


def test_tree_state_round_trip_restores_expansion_and_selection(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    sub = root / "projects"
    sub.mkdir(parents=True)
    (sub / "plan.md").write_text("# plan", encoding="utf-8")
    (root / "top.md").write_text("# top", encoding="utf-8")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        view.navigate_to(sub)
        view.select_path(sub / "plan.md")
        state = view.tree_state()
        assert str(sub) in state["expanded"]
        assert state["selected"] == str(sub / "plan.md")
    finally:
        view.close()

    fresh = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        # Collapse everything first so the restore does the work.
        fresh._tree.collapseAll()
        fresh.restore_tree_state(state)
        folder_item = fresh._find_item(sub)
        assert folder_item is not None
        assert folder_item.isExpanded() is True
        current = fresh._tree.currentItem()
        assert current is not None
        assert current.data(0, _PATH_ROLE) == str(sub / "plan.md")
        assert fresh.tree_state()["selected"] == str(sub / "plan.md")
    finally:
        fresh.close()


def test_delete_action_removes_file_and_notifies(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    target = root / "gone.md"
    target.write_text("# gone", encoding="utf-8")
    sidecar = root / "gone.md.notes.json"
    sidecar.write_text("{}", encoding="utf-8")

    tag_index = TagIndex(tmp_path / "tags.json")
    tag_index.update(target, DocumentAnnotations(doc_tags=["x"]))
    assert tag_index.files_with_tag("x")

    view = _make_view(
        tmp_path,
        monkeypatch,
        [DocumentLibrary("lib", "Vault", str(root))],
        tag_index=tag_index,
    )
    deleted: list[list] = []
    view.on_paths_deleted = lambda paths: deleted.append(paths)
    monkeypatch.setattr(
        "app.file_browser.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    # Delete permanently in the test so nothing lands in the real trash.
    monkeypatch.setattr("app.file_ops._send2trash", None)
    monkeypatch.setattr("app.file_ops.HAS_SEND2TRASH", False)
    try:
        view._delete_file_action(str(target))
        assert not target.exists()
        assert not sidecar.exists()
        assert deleted == [[str(target)]]
        assert tag_index.files_with_tag("x") == []
        assert str(target) not in _visible_file_paths(view)
    finally:
        view.close()
