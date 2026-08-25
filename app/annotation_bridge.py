"""QWebChannel bridge: JavaScript in the rendered page calls these slots."""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal, Slot

# Returned when the window has not registered an inline-edit handler (the split
# preview, for instance, never does) so the page always gets a well-formed reply
# instead of hanging on a callback that never fires.
_UNAVAILABLE = {"ok": False, "error": "unavailable"}


class AnnotationBridge(QObject):
    added = Signal(str)            # full annotation payload (json, id included)
    changed = Signal(str, str)     # id, fields json
    removed = Signal(str)          # id
    clicked = Signal(str)          # id
    orphansReported = Signal(list)  # list[str] of ids
    taskToggled = Signal(int, bool)  # source line (0-based), new checked state
    # True while the preview holds an open inline editor with unsaved text.
    # Pushed from the page rather than polled, because runJavaScript answers
    # asynchronously and the window needs the answer *before* it puts up a
    # modal reload prompt (see MainWindow._preview_editing).
    inlineEditStateChanged = Signal(bool)
    # The rendered page reports Escape only after its context-specific
    # handlers have had a chance to consume it.  MainWindow uses this as the
    # WebEngine equivalent of an unhandled QWidget key event (for example, to
    # close an open search bar without stealing Escape from inline editing).
    unhandledEscape = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Inline preview editing needs answers, not notifications, so these go
        # through registered callables rather than signals: a QWebChannel slot
        # can only return a value synchronously.
        self._inline_edit_handlers: dict = {}

    def set_inline_edit_handlers(
        self,
        fetch=None,
        commit=None,
        paste_image=None,
        commit_table=None,
        serialize_table=None,
        reload=None,
    ):
        """Register the callables backing the inline preview editor.

        Each takes the slot's arguments and returns a JSON-serializable dict;
        passing None for one leaves that operation unavailable.
        """
        self._inline_edit_handlers = {
            "fetch": fetch,
            "commit": commit,
            "paste_image": paste_image,
            "commit_table": commit_table,
            "serialize_table": serialize_table,
            "reload": reload,
        }

    def _dispatch(self, name: str, *args) -> str:
        handler = self._inline_edit_handlers.get(name)
        if handler is None:
            return json.dumps(_UNAVAILABLE)
        try:
            result = handler(*args)
        except Exception as exc:  # noqa: BLE001
            # A slot that raises leaves the page's callback pending forever;
            # always answer, even if only to say it failed.
            result = {"ok": False, "error": str(exc)}
        return json.dumps(result or _UNAVAILABLE, ensure_ascii=False)

    @Slot(str)
    def add(self, payload_json):
        self.added.emit(payload_json)

    @Slot(str, str)
    def update(self, ann_id, fields_json):
        self.changed.emit(ann_id, fields_json)

    @Slot(str)
    def remove(self, ann_id):
        self.removed.emit(ann_id)

    @Slot(str)
    def clickedAnnotation(self, ann_id):
        self.clicked.emit(ann_id)

    @Slot(str)
    def reportOrphans(self, ids_json):
        try:
            ids = json.loads(ids_json)
        except json.JSONDecodeError:
            ids = []
        self.orphansReported.emit(ids)

    @Slot(int, bool)
    def toggleTask(self, line, checked):
        self.taskToggled.emit(line, checked)

    @Slot(bool)
    def setInlineEditing(self, editing):
        """The preview opened (True) or closed (False) an inline editor."""
        self.inlineEditStateChanged.emit(bool(editing))

    @Slot(int)
    def reportUnhandledEscape(self, generation):
        """Forward an Escape key that no page-level tool consumed."""
        self.unhandledEscape.emit(int(generation))

    # ---- inline preview editing (json in, json out) -------------------------
    @Slot(int, int, result=str)
    def inlineEditFetch(self, start, end):
        """Raw Markdown for source lines *start*..*end* (inclusive)."""
        return self._dispatch("fetch", int(start), int(end))

    @Slot(int, int, str, str, str, result=str)
    def inlineEditCommit(self, start, end, original, new, sig):
        """Write *new* over lines *start*..*end*, if they still hold *original*.

        *sig* is the file signature ``inlineEditFetch`` handed out, echoed back
        untouched. Comparing text alone is not enough: two byte-identical
        blocks in one document make ``original`` match either of them, so a
        drifted line range can pass the check and write over the wrong one.
        """
        return self._dispatch("commit", int(start), int(end), original, new, sig)

    @Slot(int, int, str, str, str, result=str)
    def inlineEditCommitTable(self, start, end, original, model_json, sig):
        """Serialize *model_json* into a pipe table and write it over the block."""
        return self._dispatch(
            "commit_table", int(start), int(end), original, model_json, sig
        )

    @Slot(str, result=str)
    def inlineEditSerializeTable(self, model_json):
        """Render *model_json* as pipe-table Markdown, without writing it.

        The grid's "switch to source" button needs the text the grid *would*
        save, not the text the block was opened with, or everything typed
        into the cells is lost on the way to the textarea.
        """
        return self._dispatch("serialize_table", model_json)

    @Slot(result=str)
    def inlineEditReload(self):
        """Re-render the preview, on the page's own request.

        A refused write leaves the page deliberately un-reloaded so the user
        can rescue their text; this is the button that finishes the job once
        they have.
        """
        return self._dispatch("reload")

    @Slot(result=str)
    def inlineEditPasteImage(self):
        """Save the clipboard image and return the Markdown link for it."""
        return self._dispatch("paste_image")
