"""Tests for renaming document-level tags.

Covers three surfaces added alongside the "Manage Tags" makeover:

* ``MainWindow._rename_tag`` -- the shared global rename operation. Exercised by
  calling the real (unbound) method against a lightweight stand-in ``self`` that
  supplies the tag index / color store / re-index hook, mirroring the data-plane
  style of ``test_tag_delete_merge`` while still driving the real code path.
* The 標籤-tab tag-node right-click menu, which now offers 重新命名標籤 next to
  刪除標籤.
* ``ManageTagsDialog`` rows, which now carry per-tag rename/delete buttons and
  apply an explicit light/dark theme so the list text stays legible.

GUI cases run offscreen via the shared ``qapp`` fixture (tests/conftest.py);
modal dialogs are monkeypatched.
"""

from pathlib import Path

from PySide6.QtWidgets import QListWidgetItem

from app import doc_tags
from app import window as window_mod
from app.annotations import DocumentAnnotations
from app.manage_tags_dialog import ManageTagsDialog
from app.tag_colors import TagColorStore
from app.tag_index import TagIndex
from app.tags_panel import TagsPanel
from app.theme import DARK


def _reindex(idx: TagIndex, path: Path) -> None:
    """Mirror MainWindow._index_doc_tags: push doc-level tags into the index."""
    doc = DocumentAnnotations(doc_tags=list(doc_tags.read_doc_tags(path)))
    idx.update(path, doc, front_tags=[], body_tags=[])


class _FakeWin:
    """Minimal stand-in for MainWindow to drive the real ``_rename_tag``."""

    def __init__(self, idx: TagIndex, colors: TagColorStore) -> None:
        self._tag_index = idx
        self._tag_color_store = colors
        self.changed: list[list[Path]] = []

    def _on_doc_tags_changed(self, paths) -> None:
        # Same contract as MainWindow._on_doc_tags_changed: persistence already
        # happened; here we only re-sync the shared index (+ record the call).
        self.changed.append([Path(p) for p in paths])
        for p in paths:
            _reindex(self._tag_index, Path(p))


def _setup(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("# a", encoding="utf-8")
    b.write_text("# b", encoding="utf-8")
    doc_tags.write_doc_tags(a, ["old", "keep"])
    doc_tags.write_doc_tags(b, ["old"])

    idx = TagIndex(path=tmp_path / "idx.json")
    _reindex(idx, a)
    _reindex(idx, b)
    colors = TagColorStore(path=tmp_path / "colors.json")
    colors.set_color("old", "#E5484D")
    return a, b, idx, colors


# --------------------------------------------------------------------------
# MainWindow._rename_tag data plane
# --------------------------------------------------------------------------
def test_rename_tag_migrates_files_and_color(tmp_path):
    a, b, idx, colors = _setup(tmp_path)
    assert len(idx.files_with_tag("old")) == 2

    win = _FakeWin(idx, colors)
    window_mod.MainWindow._rename_tag(win, "old", "new")

    # Both files carry "new" instead of "old"; the unrelated tag is untouched.
    assert doc_tags.read_doc_tags(a) == ["new", "keep"]
    assert doc_tags.read_doc_tags(b) == ["new"]
    # Index migrated old -> new.
    assert idx.files_with_tag("old") == []
    assert len(idx.files_with_tag("new")) == 2
    # Explicit color moved from old to new.
    assert colors.explicit_color("new") == "#E5484D"
    assert colors.explicit_color("old") is None
    # The re-index hook fired once with the affected files.
    assert len(win.changed) == 1
    assert {p.name for p in win.changed[0]} == {"a.md", "b.md"}


def test_rename_tag_merges_into_existing_target(tmp_path):
    a, b, idx, colors = _setup(tmp_path)
    # File a already carries "keep"; renaming "old" -> "keep" folds them (dedup).
    win = _FakeWin(idx, colors)
    window_mod.MainWindow._rename_tag(win, "old", "keep")

    assert doc_tags.read_doc_tags(a) == ["keep"]  # deduped, order preserved
    assert doc_tags.read_doc_tags(b) == ["keep"]
    assert idx.files_with_tag("old") == []
    assert len(idx.files_with_tag("keep")) == 2


def test_rename_tag_prompts_when_new_is_none(tmp_path, monkeypatch):
    a, b, idx, colors = _setup(tmp_path)
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("renamed", True)),
    )
    win = _FakeWin(idx, colors)
    window_mod.MainWindow._rename_tag(win, "old")  # new=None -> prompt
    assert len(idx.files_with_tag("renamed")) == 2
    assert idx.files_with_tag("old") == []


def test_rename_tag_noop_on_cancel_or_unchanged(tmp_path, monkeypatch):
    a, b, idx, colors = _setup(tmp_path)
    win = _FakeWin(idx, colors)

    # Cancelled prompt: nothing changes.
    monkeypatch.setattr(
        window_mod.QInputDialog,
        "getText",
        staticmethod(lambda *a, **k: ("whatever", False)),
    )
    window_mod.MainWindow._rename_tag(win, "old")
    assert len(idx.files_with_tag("old")) == 2

    # Unchanged name (new == old): nothing changes, no re-index call.
    window_mod.MainWindow._rename_tag(win, "old", "old")
    assert len(idx.files_with_tag("old")) == 2
    assert win.changed == []


# --------------------------------------------------------------------------
# 標籤-tab tag node right-click menu
# --------------------------------------------------------------------------
def test_tag_node_menu_has_rename_and_delete(qapp):
    calls: dict[str, str] = {}
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_delete_tag=lambda t: calls.__setitem__("delete", t),
        on_rename_tag=lambda t: calls.__setitem__("rename", t),
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 1)])

    menu = panel._build_tag_menu("focus")
    assert [a.text() for a in menu.actions()] == ["重新命名標籤", "刪除標籤"]

    by_text = {a.text(): a for a in menu.actions()}
    by_text["重新命名標籤"].trigger()
    by_text["刪除標籤"].trigger()
    assert calls == {"rename": "focus", "delete": "focus"}


def test_tag_node_menu_rename_only_when_wired(qapp):
    # Only delete wired -> menu keeps just 刪除標籤 (existing behavior intact).
    panel = TagsPanel(
        on_tag_selected=lambda _t: None,
        on_delete_tag=lambda _t: None,
        files_for_tag=lambda _t: [],
    )
    panel.set_tags([("focus", 1)])
    assert [a.text() for a in panel._build_tag_menu("focus").actions()] == [
        "刪除標籤"
    ]


# --------------------------------------------------------------------------
# ManageTagsDialog rows + theming
# --------------------------------------------------------------------------
def test_manage_dialog_rows_have_rename_delete_and_theme(qapp, tmp_path):
    a, _b, idx, colors = _setup(tmp_path)

    rename_calls: list[str] = []
    delete_calls: list[str] = []
    dlg = ManageTagsDialog(
        [a],
        idx,
        colors,
        on_changed=lambda _paths: None,
        theme=DARK,
        on_delete_tag=lambda t: delete_calls.append(t),
        on_rename_tag=lambda t: rename_calls.append(t),
    )
    try:
        # A checkbox + rename + delete button exists per tag.
        assert "old" in dlg._checkboxes
        assert "old" in dlg._rename_buttons
        assert "old" in dlg._delete_buttons
        # Dark theme applied its own stylesheet (fixes the invisible-text bug).
        assert "background" in dlg.styleSheet()

        # Row buttons route to the global operations; the rebuild is deferred
        # so clicking a row button never deletes itself mid-click.
        dlg._rename_buttons["old"].click()
        assert rename_calls == ["old"]
        dlg._delete_buttons["old"].click()
        assert delete_calls == ["old"]

        # Flush the deferred rebuilds -- must not crash.
        qapp.processEvents()
        assert isinstance(dlg._find_item("old"), (QListWidgetItem, type(None)))
    finally:
        dlg.close()


def test_manage_dialog_without_theme_does_not_crash(qapp, tmp_path):
    a, _b, idx, colors = _setup(tmp_path)
    # theme=None (backward compatible) -> no explicit stylesheet, still builds.
    dlg = ManageTagsDialog([a], idx, colors, on_changed=lambda _p: None)
    try:
        assert "old" in dlg._checkboxes
    finally:
        dlg.close()
