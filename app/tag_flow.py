"""Tag panel and document-level tag operations delegated from MainWindow."""

from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox

from . import doc_tags as doc_tags_facade
from .annotations import AnnotationStore, DocumentAnnotations
from .file_types import is_markdown, is_pdf, is_supported_document
from .manage_tags_dialog import ManageTagsDialog
from .md_converter import body_hashtags, front_matter_tags, parse_front_matter, read_text


def merged_tag_rows(
    tag_counts: list[tuple[str, int]],
    known_tags: list[str],
) -> list[tuple[str, int]]:
    """Merge indexed tag counts with user-created (known) tags for the panel.

    *known_tags* not present in *tag_counts* are merged in with count 0 so
    freshly created-but-unassigned tags still appear. The result keeps the
    ordering of TagIndex.tag_counts(): descending count, then tag name, so
    count-0 tags sort last, alphabetically.
    """
    counts: dict[str, int] = dict(tag_counts)
    for tag in known_tags:
        counts.setdefault(tag, 0)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

def index_doc_tags(self, path):
    """Push a file's document-level tags into the shared tag index.

    Type-neutral entry point. For PDF the index entry carries only
    doc_tags (front/body/annotation tags are markdown-only); the tags
    are read through the app.doc_tags facade which dispatches by file
    type. Markdown uses the richer update path elsewhere.
    """
    tags = doc_tags_facade.read_doc_tags(Path(path))
    doc = DocumentAnnotations(doc_tags=list(tags))
    self._tag_index.update(path, doc, front_tags=[], body_tags=[])

def update_front_tags(self):
    """Read front-matter/body tags from the current Markdown file."""
    self._current_front_tags = []
    self._current_body_tags = []
    if not self._current_file:
        return
    if is_markdown(self._current_file):
        result = read_text(self._current_file)
        if result:
            front, body = parse_front_matter(result[0])
            self._current_front_tags = front_matter_tags(front)
            self._current_body_tags = body_hashtags(body)
        self._tag_index.update(
            self._current_file,
            self._doc_annotations,
            front_tags=self._current_front_tags,
            body_tags=self._current_body_tags,
        )
    elif is_pdf(self._current_file):
        self._index_doc_tags(self._current_file)
    self._refresh_tags_panel()

def refresh_tags_panel(self):
    """Single entry point for pushing tag rows into the tag panel.

    The panel data is the union of indexed tag counts (tags actually
    assigned to files) and the color store's known tags (tags the user
    created but may not have assigned yet). Known-but-unassigned tags are
    merged in with count 0 so they appear immediately, EndNote-style.
    """
    merged = merged_tag_rows(
        self._tag_index.tag_counts(),
        self._tag_color_store.known_tags(),
    )
    self._panel.tags.set_tags(merged)

def open_manage_tags(self, paths):
    """Open the EndNote-style 管理標籤 popup for one or more files."""
    paths = [Path(p) for p in paths]
    if not paths:
        return
    ManageTagsDialog(
        paths,
        self._tag_index,
        self._tag_color_store,
        on_changed=self._on_doc_tags_changed,
        theme=self._theme,
        on_delete_tag=self._delete_tag,
        on_rename_tag=self._rename_tag,
        parent=self,
    ).exec()

def on_doc_tags_changed(self, paths):
    """Re-index each edited file's doc_tags and refresh the tag views.

    Persistence has already happened (via app.doc_tags) before this is
    called; here we only sync the shared index and the UI.
    """
    for path in paths:
        path = Path(path)
        if is_markdown(path):
            if (
                self._current_file
                and Path(self._current_file).resolve() == path.resolve()
            ):
                # Keep the in-memory markdown model authoritative for the
                # open file, then persist through the normal markdown path.
                self._doc_annotations.doc_tags = (
                    doc_tags_facade.read_doc_tags(path)
                )
                self._persist_annotations()
                continue
            doc = AnnotationStore.load(path)
            self._tag_index.update(path, doc, front_tags=[], body_tags=[])
        else:
            self._index_doc_tags(path)
            # If the manage-tags dialog edited the open PDF, refresh its
            # 文件標籤 field so the panel mirrors the new state.
            if (
                self._current_file
                and is_pdf(self._current_file)
                and Path(self._current_file).resolve() == path.resolve()
            ):
                self._set_pdf_panel_document(self._current_file)
    self._refresh_tags_panel()
    # Tags never change the folder structure, so update only the affected
    # file rows' pills incrementally instead of a full disk rescan (which
    # scaled with library size and caused the tag-edit stutter).
    self._panel.file_browser.update_file_tags(paths)

def delete_tag(self, tag: str) -> None:
    """Delete a tag from the panel: drop its doc-level assignments + color.

    Note: tags can also be *content-derived* (from MD front-matter, body
    #hashtags, or annotations). Deleting only strips the document-level
    assignment and the color registration; it deliberately does NOT edit
    file contents, so a content-derived tag may reappear on the next
    re-index. That is expected behavior.
    """
    answer = QMessageBox.question(
        self,
        "刪除標籤",
        f"確定要刪除標籤「{tag}」嗎？（僅移除標籤，不會刪除檔案）",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    affected = [Path(p) for p in self._tag_index.files_with_tag(tag)]
    for path in affected:
        new = [x for x in doc_tags_facade.read_doc_tags(path) if x != tag]
        doc_tags_facade.write_doc_tags(path, new)
    # Re-index the touched files and refresh the file views.
    self._on_doc_tags_changed(affected)
    # Drop the color registration so the tag stops appearing at count 0.
    self._tag_color_store.remove(tag)
    self._refresh_tags_panel()

def rename_tag(self, old: str, new: str | None = None) -> None:
    """Rename a document-level tag everywhere it is assigned.

    Prompts for *new* when not given. Rewrites every affected file's
    doc-level tags (old -> new, deduped, order preserved), migrates the
    explicit color registration from *old* to *new*, then re-indexes and
    refreshes the tag views via ``_on_doc_tags_changed`` (which already
    refreshes the panel + file views, so we do not refresh again here).

    Merge semantics: if *new* already exists, *old*'s files simply gain
    *new* (deduped) -- i.e. *old* is folded into *new*, which is acceptable.

    Note (same caveat as ``_delete_tag``): only document-level tags are
    touched. Content-derived tags (MD front-matter, body #hashtags,
    annotations) are not rewritten, so such a tag may reappear on re-index.
    """
    old = (old or "").strip()
    if not old:
        return
    if new is None:
        new, ok = QInputDialog.getText(
            self, "重新命名標籤", "新標籤名稱：", text=old
        )
        if not ok:
            return
    new = (new or "").strip()
    if not new or new == old:
        return

    affected = [Path(p) for p in self._tag_index.files_with_tag(old)]
    for path in affected:
        renamed: list[str] = []
        for t in doc_tags_facade.read_doc_tags(path):
            repl = new if t == old else t
            if repl not in renamed:  # dedupe (folds old into an existing new)
                renamed.append(repl)
        doc_tags_facade.write_doc_tags(path, renamed)

    # Migrate the explicit color old -> new (keep new's own color if it
    # already has one), then drop old's registration. Done before the
    # refresh below so the single _on_doc_tags_changed pass reflects it.
    col = self._tag_color_store.explicit_color(old)
    if col and not self._tag_color_store.explicit_color(new):
        self._tag_color_store.set_color(new, col)
    self._tag_color_store.remove(old)

    # Re-index touched files + refresh the tag panel and file views. This
    # runs even when *affected* is empty (old was known-but-unassigned), so
    # a rename of a colored-but-unused tag is still reflected in the panel.
    self._on_doc_tags_changed(affected)

def add_tag_to_paths(self, paths):
    """Quick-assign one tag to files from the 檔案 tab's "加入標籤…".

    Filters to supported documents, then shows a small editable combo that
    lists every known tag (indexed + colored-but-unassigned) yet still lets
    the user type a brand-new tag. A new tag auto-gets a deterministic color
    via the color store. The write itself reuses ``_assign_tag_to_paths`` so
    the tag index and every view refresh exactly as elsewhere.
    """
    paths = [Path(p) for p in paths if is_supported_document(Path(p))]
    if not paths:
        return
    items = sorted(
        set(self._tag_index.all_tags()) | set(self._tag_color_store.known_tags())
    )
    tag, ok = QInputDialog.getItem(
        self, "加入標籤", "選擇或輸入標籤：", items, 0, True
    )
    if not ok:
        return
    tag = tag.strip()
    if not tag:
        return
    self._assign_tag_to_paths(tag, paths)

def assign_tag_to_paths(self, tag: str, paths):
    """Add *tag* to each of *paths* (drag-onto-tag / quick assign)."""
    tag = (tag or "").strip()
    if not tag:
        return
    changed = []
    for path in paths:
        path = Path(path)
        if not is_supported_document(path):
            continue
        existing = doc_tags_facade.read_doc_tags(path)
        if tag in existing:
            changed.append(path)
            continue
        doc_tags_facade.write_doc_tags(path, existing + [tag])
        changed.append(path)
    if changed:
        self._on_doc_tags_changed(changed)

def on_tag_selected(self, tag: str):
    self._active_tag = tag or ""
    self._panel.tags.set_active(tag)
