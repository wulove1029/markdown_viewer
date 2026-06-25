"""Central tag cache for cross-file tag filtering (stored under AppData)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PyQt6.QtCore import QStandardPaths


def _default_index_path() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base or ".") / "markdown-viewer" / "tag_index.json"


class TagIndex:
    def __init__(self, path=None):
        self._path = Path(path) if path else _default_index_path()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self):
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)

    def update(self, md_path, doc):
        key = str(Path(md_path).resolve())
        annot_tags = sorted({t for a in doc.annotations for t in a.tags})
        if not doc.doc_tags and not doc.annotations:
            self._data.pop(key, None)
        else:
            self._data[key] = {
                "doc_tags": list(doc.doc_tags),
                "annot_tags": annot_tags,
                "count": len(doc.annotations),
            }
        self._save()

    def all_tags(self) -> list[str]:
        tags: set[str] = set()
        for entry in self._data.values():
            tags.update(entry.get("doc_tags", []))
            tags.update(entry.get("annot_tags", []))
        return sorted(tags)

    def files_with_tag(self, tag) -> list[str]:
        out = []
        for path, entry in self._data.items():
            if tag in entry.get("doc_tags", []) or tag in entry.get("annot_tags", []):
                out.append(path)
        return out

    def prune(self):
        for path in list(self._data.keys()):
            if not Path(path).exists():
                self._data.pop(path, None)
        self._save()
