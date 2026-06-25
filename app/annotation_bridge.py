"""QWebChannel bridge: JavaScript in the rendered page calls these slots."""

from __future__ import annotations

import json

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot


class AnnotationBridge(QObject):
    added = pyqtSignal(str)            # full annotation payload (json, id included)
    changed = pyqtSignal(str, str)     # id, fields json
    removed = pyqtSignal(str)          # id
    clicked = pyqtSignal(str)          # id
    orphansReported = pyqtSignal(list)  # list[str] of ids

    @pyqtSlot(str)
    def add(self, payload_json):
        self.added.emit(payload_json)

    @pyqtSlot(str, str)
    def update(self, ann_id, fields_json):
        self.changed.emit(ann_id, fields_json)

    @pyqtSlot(str)
    def remove(self, ann_id):
        self.removed.emit(ann_id)

    @pyqtSlot(str)
    def clickedAnnotation(self, ann_id):
        self.clicked.emit(ann_id)

    @pyqtSlot(str)
    def reportOrphans(self, ids_json):
        try:
            ids = json.loads(ids_json)
        except json.JSONDecodeError:
            ids = []
        self.orphansReported.emit(ids)
