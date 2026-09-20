"""PDF orchestration delegated from MainWindow.

Window contract: owns _current_file/_current_kind, _pdf_* collections/view,
_pen_mode and _restoring_pdf_page; provides _panel, _stack, search widgets,
statusBar(), file signatures, page restoration, icon and tag refresh hooks.
The original window methods remain callbacks, so signals and test doubles
retain their existing names. QTextDocument and editor synchronization are
outside this module. Persistence remains in the existing PDF stores/writer.
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QInputDialog, QMessageBox

from . import pdf_annotation_writer
from .pdf_embedded_annotations import extract_embedded_annotations
from .pdf_highlights import DEFAULT_COLOR, PdfHighlight, PdfHighlightStore, Rect
from .pdf_notes import PdfNote, PdfNoteStore
from .settings_store import APP as _APP
from .settings_store import ORG as _ORG

_PDF_ANNOTATION_AUTHOR_KEY = "pdf_annotation_author"

def open_pdf(self, path: Path):
    # Capture the target page before replacing the shared viewer: changing
    # its layout/scrollbars can emit the old document's page for this path.
    try:
        page = max(0, int(self._pdf_pages_map().get(str(path), 0)))
    except (TypeError, ValueError):
        page = 0
    self._restoring_pdf_page = True
    try:
        # Password prompts (if any) belong above the PDF view.
        self._stack.setCurrentWidget(self._pdf_view)
        loaded = self._pdf_view.load(path)
        self._pdf_view.restore_page(page)
    finally:
        self._restoring_pdf_page = False
    # Drop the previous file's bookmarks immediately. PdfView starts the
    # current outline in the background only after visible content paints.
    self._panel.toc.update_outline([])
    if not loaded:
        if self._pdf_view.is_locked():
            self.statusBar().showMessage(
                "已取消開啟受密碼保護的 PDF；重新開啟可再次輸入密碼。", 6000
            )
        else:
            self.statusBar().showMessage(
                "無法開啟此 PDF：檔案可能已損毀或無法讀取。", 6000
            )
    # Page-anchored notes + text highlights live in the "標註" tab.
    self._pdf_notes = PdfNoteStore.load(path)
    self._pdf_highlights = PdfHighlightStore.load(path)
    self._pdf_view.set_highlights(self._pdf_highlights)
    self._panel.show_pdf_notes(True)
    self._panel.set_annotations_enabled(True)
    # Point the 文件標籤 field at this PDF and surface any tags it already
    # carries so they show up (with a count) in the 標籤 side panel/filters.
    self._set_pdf_panel_document(path)
    self._index_doc_tags(path)
    self._refresh_tags_panel()
    self._refresh_pdf_notes_panel()
    self._refresh_pdf_highlights_panel()
    # Embedded (Adobe-authored) annotations load in the background — see
    # PdfView.request_embedded_annotations(); clear the previous document's
    # list immediately so nothing stale lingers in the panel meanwhile.
    self._pdf_embedded_annotations = []
    self._refresh_pdf_embedded_annotations_panel()
    self._refresh_pdf_annotation_write_state()


def refresh_pdf_notes_panel(self):
    self._panel.pdf_notes.set_notes(self._pdf_notes)
    self._panel.pdf_notes.set_current_page(self._pdf_view.current_page())


def save_pdf_notes(self):
    if self._current_file and self._current_kind == "pdf":
        try:
            PdfNoteStore.save(self._current_file, self._pdf_notes)
        except OSError as exc:
            self.statusBar().showMessage(f"無法儲存 PDF 註記：{exc}", 4000)


def pdf_add_note(self):
    self._pdf_add_note_at_page(self._pdf_view.current_page())


def pdf_add_note_at_page(self, page: int):
    if self._current_kind != "pdf" or not self._current_file:
        return
    text, ok = QInputDialog.getMultiLineText(
        self, "新增頁面註記", f"第 {page + 1} 頁的註記：", ""
    )
    if not ok or not text.strip():
        return
    self._pdf_notes.append(PdfNote.new(page=page, note=text.strip()))
    self._pdf_notes.sort(key=lambda n: (n.page, n.created))
    self._save_pdf_notes()
    self._refresh_pdf_notes_panel()


def find_pdf_note(self, note_id):
    return next((n for n in self._pdf_notes if n.id == note_id), None)


def pdf_note_activated(self, note_id):
    note = self._find_pdf_note(note_id)
    if note:
        self._pdf_view.jump_to_page(note.page)


def pdf_edit_note(self, note_id):
    note = self._find_pdf_note(note_id)
    if not note:
        return
    text, ok = QInputDialog.getMultiLineText(
        self, "編輯註記", f"第 {note.page + 1} 頁：", note.note
    )
    if not ok:
        return
    note.note = text.strip()
    note.updated = datetime.now().isoformat(timespec="seconds")
    self._save_pdf_notes()
    self._refresh_pdf_notes_panel()


def pdf_delete_note(self, note_id):
    self._pdf_notes = [n for n in self._pdf_notes if n.id != note_id]
    self._save_pdf_notes()
    self._refresh_pdf_notes_panel()


def pdf_find(self):
    if self._current_kind != "pdf":
        return
    self._search_bar.show()
    self._set_search_escape_enabled(True)
    self._search_input.setFocus()
    self._search_input.selectAll()


def on_pdf_pen_mode_changed(self, on: bool):
    self._pen_mode = on
    self._refresh_icons()


def toggle_pen_mode(self):
    self._pen_mode = not self._pen_mode
    self._pdf_view.set_pen_mode(self._pen_mode)
    self._refresh_icons()
    if self._pen_mode:
        self.statusBar().showMessage("螢光筆模式：拖曳選取文字即可標記", 3000)


def refresh_pdf_highlights_panel(self):
    self._panel.pdf_highlights.set_highlights(self._pdf_highlights)


def save_pdf_highlights(self):
    if self._current_file and self._current_kind == "pdf":
        try:
            PdfHighlightStore.save(self._current_file, self._pdf_highlights)
        except OSError as exc:
            self.statusBar().showMessage(f"無法儲存螢光標記：{exc}", 4000)


def on_pdf_highlight_requested(self, payload):
    if self._current_kind != "pdf" or not self._current_file:
        return
    rects = [Rect(x=x, y=y, w=w, h=h) for (x, y, w, h) in payload.get("rects", [])]
    if not rects:
        return
    highlight = PdfHighlight.new(
        page=int(payload.get("page", 0)),
        rects=rects,
        text=payload.get("text", ""),
        color=payload.get("color", DEFAULT_COLOR),
    )
    self._pdf_highlights.append(highlight)
    self._pdf_highlights.sort(key=lambda h: (h.page, h.created))
    self._save_pdf_highlights()
    self._pdf_view.set_highlights(self._pdf_highlights)
    self._refresh_pdf_highlights_panel()


def find_pdf_highlight(self, hid):
    return next((h for h in self._pdf_highlights if h.id == hid), None)


def pdf_highlight_activated(self, hid):
    highlight = self._find_pdf_highlight(hid)
    if not highlight:
        return
    if highlight.rects:
        r = highlight.rects[0]
        self._pdf_view.reveal(highlight.page, r.x, r.y, r.w, r.h)
    else:
        self._pdf_view.jump_to_page(highlight.page)


def pdf_highlight_recolor(self, hid, color):
    highlight = self._find_pdf_highlight(hid)
    if not highlight:
        return
    highlight.color = color
    highlight.updated = datetime.now().isoformat(timespec="seconds")
    self._pdf_view.set_pen_color(color)
    self._save_pdf_highlights()
    self._pdf_view.set_highlights(self._pdf_highlights)
    self._refresh_pdf_highlights_panel()


def pdf_highlight_edit_note(self, hid):
    highlight = self._find_pdf_highlight(hid)
    if not highlight:
        return
    text, ok = QInputDialog.getMultiLineText(
        self, "螢光標記備註", f"第 {highlight.page + 1} 頁：", highlight.note
    )
    if not ok:
        return
    highlight.note = text.strip()
    highlight.updated = datetime.now().isoformat(timespec="seconds")
    self._save_pdf_highlights()
    self._refresh_pdf_highlights_panel()


def pdf_highlight_edit_text(self, hid, text):
    from dataclasses import replace

    if self._current_kind != "pdf" or not self._current_file:
        return False
    if self._find_pdf_highlight(hid) is None:
        return False
    changed = [
        replace(h, text=text, updated=datetime.now().isoformat(timespec="seconds"))
        if h.id == hid else h for h in self._pdf_highlights
    ]
    try:
        PdfHighlightStore.save(self._current_file, changed)
    except OSError as exc:
        self.statusBar().showMessage(f"無法儲存螢光文字：{exc}", 5000)
        return False
    self._pdf_highlights = changed
    self._pdf_view.set_highlights(changed)
    self._refresh_pdf_highlights_panel()
    return True


def pdf_highlight_delete(self, hid):
    if not self._find_pdf_highlight(hid):
        return
    self._pdf_highlights = [h for h in self._pdf_highlights if h.id != hid]
    self._save_pdf_highlights()
    self._pdf_view.set_highlights(self._pdf_highlights)
    self._refresh_pdf_highlights_panel()


def refresh_pdf_embedded_annotations_panel(self):
    self._panel.pdf_embedded_annotations.set_annotations(
        self._pdf_embedded_annotations
    )
    self._panel.set_embedded_annotation_count(
        len(self._pdf_embedded_annotations)
    )
    # The view paints these itself (the raster is rendered without PDFium's
    # annotation layer), so it needs the same list the panel shows.
    self._pdf_view.set_embedded_annotations(self._pdf_embedded_annotations)


def pdf_embedded_annotation_activated(self, entry):
    x, y, w, h = entry.rect
    if w > 0 or h > 0:
        self._pdf_view.reveal(entry.page, x, y, w, h)
    else:
        self._pdf_view.jump_to_page(entry.page)
    # A reply has no mark of its own on the page; flash the annotation it
    # answers so the click still points somewhere visible.
    target = entry.in_reply_to if entry.in_reply_to is not None else entry.xref
    self._pdf_view.flash_embedded_annotation(target)
    # Open the same card the on-page marker opens, so the comment text is
    # readable next to the passage instead of only in the list.
    self._pdf_view.show_annotation_card(entry)


def pdf_annotation_author(self) -> str:
    configured = str(
        QSettings(_ORG, _APP).value(_PDF_ANNOTATION_AUTHOR_KEY, "") or ""
    ).strip()
    return configured or pdf_annotation_writer.default_author()


def edit_pdf_annotation_author(self):
    name, ok = QInputDialog.getText(
        self,
        "PDF 註解作者",
        "新增 PDF 註解時使用的作者名稱：",
        text=self._pdf_annotation_author(),
    )
    if not ok:
        return
    name = name.strip()
    if not name:
        return
    QSettings(_ORG, _APP).setValue(_PDF_ANNOTATION_AUTHOR_KEY, name)
    self._pdf_view.set_annotation_author(name)
    self.statusBar().showMessage(f"PDF 註解作者已設為「{name}」", 3000)


def refresh_pdf_annotation_write_state(self):
    """Tell the view whether this PDF can take new annotations, and why not.

    Checked when the document opens so the reply box is disabled with a
    reason rather than swallowing a comment that could never be saved.
    """
    self._pdf_view.set_annotation_author(self._pdf_annotation_author())
    if self._current_kind != "pdf" or self._current_file is None:
        self._pdf_view.set_annotation_write_blocked("尚未開啟 PDF")
        return
    reason = pdf_annotation_writer.writable_reason(self._current_file) or ""
    self._pdf_view.set_annotation_write_blocked(reason)


def perform_pdf_annotation_write(self, action, focus_xref) -> bool:
    """Run one write, then re-read the file and re-open the card.

    Nothing is echoed into the UI before the save succeeds: on failure the
    reply simply never appears, and the reason (with the session backup
    that was taken first) is reported instead.
    """
    if self._current_kind != "pdf" or self._current_file is None:
        return False
    path = self._current_file
    try:
        action(path, self._pdf_annotation_author())
    except pdf_annotation_writer.AnnotationWriteError as exc:
        QMessageBox.warning(self, "無法寫入 PDF 註解", str(exc))
        self.statusBar().showMessage(f"註解未寫入：{exc}", 6000)
        return False
    # Our own save: keep the file watcher from offering to reload.
    self._loaded_signature = self._file_signature(path)
    self._pdf_embedded_annotations = extract_embedded_annotations(path)
    self._refresh_pdf_embedded_annotations_panel()
    self._pdf_view.annotation_card().clear_reply_input()
    entry = next(
        (e for e in self._pdf_embedded_annotations if e.xref == focus_xref),
        None,
    )
    if entry is not None:
        self._pdf_view.show_annotation_card(entry)
    self.statusBar().showMessage("已寫入 PDF 註解", 3000)
    return True


def pdf_annotation_reply(self, entry, text: str) -> bool:
    return self._perform_pdf_annotation_write(
        lambda path, author: pdf_annotation_writer.add_reply(
            path, entry.xref, text, author
        ),
        entry.xref,
    )


def pdf_annotation_edit(self, entry, text: str) -> bool:
    focus = entry.in_reply_to if entry.in_reply_to is not None else entry.xref
    return self._perform_pdf_annotation_write(
        lambda path, author: pdf_annotation_writer.edit_annotation(
            path, entry.xref, text, author
        ),
        focus,
    )


def annotation_replies_to(self, entry) -> list:
    return [
        e
        for e in self._pdf_embedded_annotations
        if e.in_reply_to == entry.xref
    ]


def confirm_annotation_delete(self, entry, replies) -> bool:
    """Warn before a delete that takes other people's replies with it.

    MuPDF removes an annotation's whole ``/IRT`` chain, so deleting a
    comment of your own silently deletes every reply underneath it —
    including replies somebody else wrote.
    """
    if not replies:
        return True
    author = self._pdf_annotation_author()
    others = sum(
        1 for r in replies if str(r.author or "").strip() != author.strip()
    )
    message = f"將一併刪除 {len(replies)} 則回覆"
    if others:
        message += f"（其中 {others} 則為他人）"
    message += "。確定要刪除嗎？"
    return (
        QMessageBox.question(
            self,
            "刪除 PDF 註解",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        == QMessageBox.StandardButton.Yes
    )


def pdf_annotation_delete(self, entry) -> bool:
    replies = self._annotation_replies_to(entry)
    if not self._confirm_annotation_delete(entry, replies):
        return False
    focus = entry.in_reply_to if entry.in_reply_to is not None else entry.xref
    return self._perform_pdf_annotation_write(
        lambda path, author: pdf_annotation_writer.delete_annotation(
            path, entry.xref, author
        ),
        focus,
    )


def on_pdf_embedded_annotation_clicked(self, entry):
    """Mirror an on-page annotation click into the sidebar list.

    Only when the panel is already open: a click on the page must not
    expand a sidebar the reader deliberately collapsed.
    """
    if entry is None or not self._panel.isVisible():
        return
    self._panel.show_pdf_embedded_annotations()
    self._panel.pdf_embedded_annotations.select_annotation(entry)


def set_pdf_panel_document(self, path):
    """Point the PDF markup panel's 文件標籤 field at *path* (or None).

    Accessed defensively so the injected test panel double (which omits
    the PDF markup sub-panel) stays compatible.
    """
    panel = getattr(self._panel, "pdf_markup", None)
    if panel is not None:
        panel.set_pdf_document(path)
