"""QWebChannel bridge: JavaScript in the rendered page calls these slots."""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, Signal, Slot


class AnnotationBridge(QObject):
    added = Signal(str)            # full annotation payload (json, id included)
    changed = Signal(str, str)     # id, fields json
    removed = Signal(str)          # id
    clicked = Signal(str)          # id
    orphansReported = Signal(list)  # list[str] of ids
    taskToggled = Signal(int, bool)  # source line (0-based), new checked state

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
