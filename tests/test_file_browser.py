import os

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItemIterator

from app.annotations import DocumentAnnotations
from app.document_libraries import DocumentLibrary, DocumentLibraryStore
from app.file_browser import _IS_DIR_ROLE, _PATH_ROLE, FileBrowserView
from app.tag_index import TagIndex
from app.theme import DARK, LIGHT

_FILE_ATTRIBUTE_HIDDEN = 0x2


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


def test_tree_items_have_type_distinguishing_icons(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    sub = root / "sub"
    sub.mkdir(parents=True)
    md_file = root / "notes.md"
    pdf_file = root / "report.pdf"
    nested_md = sub / "deep.md"
    md_file.write_text("# notes", encoding="utf-8")
    pdf_file.write_bytes(b"%PDF-1.4 minimal")
    nested_md.write_text("# deep", encoding="utf-8")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        root_item = view._find_item(root)
        folder_item = view._find_item(sub)
        md_item = view._find_item(md_file)
        pdf_item = view._find_item(pdf_file)
        for item in (root_item, folder_item, md_item, pdf_item):
            assert item is not None
            # Every row is icon-tagged so folders and files read differently.
            assert item.icon(0).isNull() is False
    finally:
        view.close()


def test_apply_theme_updates_existing_icons_without_refreshing_tree(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    sub = root / "sub"
    sub.mkdir(parents=True)
    md_file = root / "notes.md"
    pdf_file = sub / "report.pdf"
    md_file.write_text("# notes", encoding="utf-8")
    pdf_file.write_bytes(b"%PDF-1.4 minimal")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        root_item = view._find_item(root)
        folder_item = view._find_item(sub)
        md_item = view._find_item(md_file)
        pdf_item = view._find_item(pdf_file)
        for item in (root_item, folder_item, md_item, pdf_item):
            assert item is not None

        view.navigate_to(sub)
        view.select_path(pdf_file)
        icon_keys = {
            "root": root_item.icon(0).cacheKey(),
            "folder": folder_item.icon(0).cacheKey(),
            "markdown": md_item.icon(0).cacheKey(),
            "pdf": pdf_item.icon(0).cacheKey(),
        }
        refresh_calls = []
        monkeypatch.setattr(
            view, "refresh_libraries", lambda: refresh_calls.append(True)
        )

        view.apply_theme(DARK)

        assert refresh_calls == []
        assert root_item.icon(0).cacheKey() != icon_keys["root"]
        assert folder_item.icon(0).cacheKey() != icon_keys["folder"]
        assert md_item.icon(0).cacheKey() != icon_keys["markdown"]
        assert pdf_item.icon(0).cacheKey() != icon_keys["pdf"]
        assert folder_item.isExpanded() is True
        assert view._tree.currentItem().data(0, _PATH_ROLE) == str(pdf_file)
        assert view._tag_delegate._text_color == QColor(DARK.text)
    finally:
        view.close()


def test_apply_theme_updates_explicit_tree_text_colors_without_refreshing(
    qapp, tmp_path, monkeypatch
):
    missing_root = tmp_path / "missing"
    view = _make_view(
        tmp_path,
        monkeypatch,
        [DocumentLibrary("missing", "Missing", str(missing_root))],
    )
    try:
        root_item = view._tree.topLevelItem(0)
        assert root_item.foreground(0).color() == QColor(LIGHT.text_muted)
        refresh_calls = []
        monkeypatch.setattr(
            view, "refresh_libraries", lambda: refresh_calls.append(True)
        )

        view.apply_theme(DARK)

        assert refresh_calls == []
        assert view._tree.topLevelItem(0) is root_item
        assert root_item.foreground(0).color() == QColor(DARK.text_muted)

        view._filter.setText("no-match")
        empty_item = view._tree.topLevelItem(0)
        assert empty_item.foreground(0).color() == QColor(DARK.text_subtle)

        view.apply_theme(LIGHT)

        assert refresh_calls == []
        assert view._tree.topLevelItem(0) is empty_item
        assert empty_item.foreground(0).color() == QColor(LIGHT.text_subtle)
    finally:
        view.close()


def test_construction_and_theme_changes_scan_document_library_once(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# note", encoding="utf-8")
    scans = []
    original_refresh = FileBrowserView._refresh_list

    def counted_refresh(view):
        scans.append(view)
        return original_refresh(view)

    monkeypatch.setattr(FileBrowserView, "_refresh_list", counted_refresh)
    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        assert scans == [view]
        view.apply_theme(DARK)
        view.apply_theme(LIGHT)
        assert scans == [view]
    finally:
        view.close()


def test_missing_library_root_still_shows_icon(qapp, tmp_path, monkeypatch):
    missing = tmp_path / "gone"  # never created on disk
    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Gone", str(missing))]
    )
    try:
        root_item = view._find_item(missing)
        assert root_item is not None
        assert root_item.icon(0).isNull() is False
    finally:
        view.close()


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


@pytest.mark.skipif(os.name != "nt", reason="hidden attribute is Windows-only")
def test_scan_hides_existing_sidecars(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# n", encoding="utf-8")
    side = root / "note.md.notes.json"
    side.write_text("{}", encoding="utf-8")
    hl = root / "note.pdf.highlights.json"
    hl.write_text("{}", encoding="utf-8")

    view = _make_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        # The scan tags sidecars hidden even though they never enter the tree.
        assert os.stat(side).st_file_attributes & _FILE_ATTRIBUTE_HIDDEN
        assert os.stat(hl).st_file_attributes & _FILE_ATTRIBUTE_HIDDEN
        assert view._find_item(side) is None
        assert view._find_item(hl) is None
    finally:
        view.close()


# ---------------- background (threaded) scanning ----------------

def _make_bg_view(tmp_path, monkeypatch, libraries, tag_index=None):
    store = DocumentLibraryStore(tmp_path / "libraries.json")
    store.save(libraries)
    monkeypatch.setattr("app.file_browser.DocumentLibraryStore", lambda: store)
    return FileBrowserView(
        lambda _path: None, tag_index=tag_index, background_scan=True
    )


def _wait_scan(qapp, view: FileBrowserView, timeout_s: float = 10.0):
    import time

    deadline = time.monotonic() + timeout_s
    while view.is_scanning():
        qapp.processEvents()
        assert time.monotonic() < deadline, "background scan never finished"
        time.sleep(0.005)
    qapp.processEvents()


def test_background_scan_builds_tree_off_the_ui_thread(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    sub = root / "inbox"
    sub.mkdir(parents=True)
    (root / "top.md").write_text("# top", encoding="utf-8")
    (sub / "nested.md").write_text("# nested", encoding="utf-8")
    (sub / "ignored.txt").write_text("x", encoding="utf-8")

    view = _make_bg_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        # Construction returns before the folder walk finishes.
        assert view.is_scanning() is True
        assert view._tree.topLevelItemCount() == 0
        _wait_scan(qapp, view)
        assert view.is_scanning() is False
        texts = _visible_texts(view)
        assert "Vault（2）" in texts
        assert "inbox" in texts
        assert "top.md" in texts
        assert "nested.md" in texts
        assert "ignored.txt" not in texts
        # Library roots open by default on the first build.
        assert view._tree.topLevelItem(0).isExpanded() is True
        assert "2 份文件" in view._status.text()
    finally:
        view.close()


def test_background_scan_matches_synchronous_scan(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    (root / "a" / "deep").mkdir(parents=True)
    (root / "b").mkdir()
    (root / ".git").mkdir()
    (root / "empty").mkdir()
    (root / "z.md").write_text("z", encoding="utf-8")
    (root / "a" / "deep" / "d.md").write_text("d", encoding="utf-8")
    (root / "a" / "deep" / "e.pdf").write_bytes(b"%PDF-1.4")
    (root / "b" / "B.md").write_text("b", encoding="utf-8")
    (root / ".git" / "hidden.md").write_text("h", encoding="utf-8")
    libs = [DocumentLibrary("lib", "Vault", str(root))]

    sync_view = _make_view(tmp_path, monkeypatch, libs)
    bg_view = _make_bg_view(tmp_path, monkeypatch, libs)
    try:
        _wait_scan(qapp, bg_view)
        assert _visible_texts(bg_view) == _visible_texts(sync_view)
        assert _visible_file_paths(bg_view) == _visible_file_paths(sync_view)
        assert bg_view._status.text() == sync_view._status.text()
    finally:
        sync_view.close()
        bg_view.close()


def test_restore_and_select_requested_during_scan_apply_after_it(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    sub = root / "projects"
    sub.mkdir(parents=True)
    (sub / "plan.md").write_text("# plan", encoding="utf-8")
    (root / "top.md").write_text("# top", encoding="utf-8")

    view = _make_bg_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        assert view.is_scanning() is True
        # Session restore runs right after construction, before the walk lands.
        view.restore_tree_state(
            {"expanded": [str(root), str(sub)], "selected": str(sub / "plan.md")}
        )
        # Even before the tree exists the state is remembered for a save.
        assert str(sub) in view.tree_state()["expanded"]
        view.navigate_to(sub)
        view.select_path(sub / "plan.md")
        _wait_scan(qapp, view)
        folder_item = view._find_item(sub)
        assert folder_item is not None
        assert folder_item.isExpanded() is True
        current = view._tree.currentItem()
        assert current is not None
        assert current.data(0, _PATH_ROLE) == str(sub / "plan.md")
        assert view.tree_state()["selected"] == str(sub / "plan.md")
    finally:
        view.close()


def test_newer_refresh_supersedes_running_scan(qapp, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "alpha.md").write_text("a", encoding="utf-8")
    (root / "beta.md").write_text("b", encoding="utf-8")

    view = _make_bg_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        # Type a filter while the first scan is still in flight: only the
        # latest request may build the tree.
        view._filter.setText("alp")
        view._filter.setText("bet")
        _wait_scan(qapp, view)
        paths = _visible_file_paths(view)
        assert paths == [str(root / "beta.md")]
        assert not view.is_scanning()
    finally:
        view.close()


def test_scan_folder_uses_directory_listing_types(tmp_path):
    """The walker relies on scandir entry types (no per-file stat calls)."""
    from app import file_browser as fb

    root = tmp_path / "vault"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "n.md").write_text("n", encoding="utf-8")
    (root / "top.pdf").write_bytes(b"%PDF-1.4")
    (root / "skip.txt").write_text("x", encoding="utf-8")
    request = fb._ScanRequest(
        libraries=[DocumentLibrary("lib", "Vault", str(root))],
        query="",
        allowed=None,
        excluded=[],
        transient=set(),
        tags_for=lambda _p: ["t"],
        filtering=False,
        active_tag="",
    )
    results = fb._scan_libraries(request)
    assert len(results) == 1
    scan = results[0]
    assert scan.exists is True
    assert scan.count == 2
    names = [(n.name, n.is_dir) for n in scan.children]
    assert names == [("sub", True), ("top.pdf", False)]
    assert scan.children[0].children[0].name == "n.md"
    assert scan.children[1].tags == ["t"]

    # A cancelled token aborts the walk instead of finishing it.
    token = fb._ScanToken()
    token.cancel()
    with pytest.raises(fb._ScanCancelled):
        fb._scan_libraries(request, token)


def test_failed_background_scan_reports_instead_of_blocking(
    qapp, tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("n", encoding="utf-8")

    def boom(request, token=None):
        raise RuntimeError("disk went away")

    monkeypatch.setattr("app.file_browser._scan_libraries", boom)
    view = _make_bg_view(
        tmp_path, monkeypatch, [DocumentLibrary("lib", "Vault", str(root))]
    )
    try:
        view.select_path(root / "note.md")  # queued while "scanning"
        _wait_scan(qapp, view)
        # No synchronous rescan on the UI thread; the user is told instead.
        assert "失敗" in view._status.text()
        assert view._tree.topLevelItemCount() == 0
        assert view._pending_select is None
    finally:
        view.close()
