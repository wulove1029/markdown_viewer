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

    def __init__(self, parent=None):
        super().__init__(parent)
        # Inline preview editing needs answers, not notifications, so these go
        # through registered callables rather than signals: a QWebChannel slot
        # can only return a value synchronously.
        self._inline_edit_handlers: dict = {}

    def set_inline_edit_handlers(self, fetch=None, commit=None, paste_image=None):
        """Register the callables backing the inline preview editor.

        Each takes the slot's arguments and returns a JSON-serializable dict;
        passing None for one leaves that operation unavailable.
        """
        self._inline_edit_handlers = {
            "fetch": fetch,
            "commit": commit,
            "paste_image": paste_image,
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

    # ---- inline preview editing (json in, json out) -------------------------
    @Slot(int, int, result=str)
    def inlineEditFetch(self, start, end):
        """Raw Markdown for source lines *start*..*end* (inclusive)."""
        return self._dispatch("fetch", int(start), int(end))

    @Slot(int, int, str, str, result=str)
    def inlineEditCommit(self, start, end, original, new):
        """Write *new* over lines *start*..*end*, if they still hold *original*."""
        return self._dispatch("commit", int(start), int(end), original, new)

    @Slot(result=str)
    def inlineEditPasteImage(self):
        """Save the clipboard image and return the Markdown link for it."""
        return self._dispatch("paste_image")
