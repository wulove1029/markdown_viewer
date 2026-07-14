"""Annotation data model and sidecar (.notes.json) persistence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .atomic_io import atomic_write_text

SCHEMA_VERSION = 1
DEFAULT_COLOR = "#ffd54f"


@dataclass
class Annotation:
    id: str
    exact: str
    prefix: str = ""
    suffix: str = ""
    textPosition: int = 0
    color: str = DEFAULT_COLOR
    note: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(exact, prefix="", suffix="", textPosition=0,
            color=DEFAULT_COLOR, note="", tags=None):
        now = datetime.now().isoformat(timespec="seconds")
        return Annotation(
            id=uuid.uuid4().hex, exact=exact, prefix=prefix, suffix=suffix,
            textPosition=int(textPosition), color=color, note=note,
            tags=list(tags or []), created=now, updated=now,
        )

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return Annotation(
            id=d.get("id") or uuid.uuid4().hex,
            exact=d.get("exact", ""),
            prefix=d.get("prefix", ""),
            suffix=d.get("suffix", ""),
            textPosition=int(d.get("textPosition", 0)),
            color=d.get("color", DEFAULT_COLOR),
            note=d.get("note", ""),
            tags=list(d.get("tags", [])),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
        )


@dataclass
class DocumentAnnotations:
    doc_tags: list[str] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)


class AnnotationStore:
    @staticmethod
    def sidecar_path(md_path) -> Path:
        p = Path(md_path)
        return p.with_name(p.name + ".notes.json")

    @classmethod
    def load(cls, md_path) -> DocumentAnnotations:
        path = cls.sidecar_path(md_path)
        if not path.exists():
            return DocumentAnnotations()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
            return DocumentAnnotations()
        anns = [Annotation.from_dict(a) for a in data.get("annotations", [])]
        return DocumentAnnotations(
            doc_tags=list(data.get("doc_tags", [])), annotations=anns
        )

    @staticmethod
    def _remove_sidecar(path: Path) -> None:
        """Delete the sidecar and any quarantined ``.bak`` (best-effort)."""
        for target in (path, path.with_name(path.name + ".bak")):
            try:
                target.unlink()
            except OSError:
                pass

    @classmethod
    def save(cls, md_path, doc: DocumentAnnotations) -> None:
        path = cls.sidecar_path(md_path)
        # Don't leave an empty sidecar behind: with no doc-tags and no
        # annotations there is nothing worth persisting, so drop the file
        # (and its .bak) if present rather than writing an empty shell.
        if not doc.doc_tags and not doc.annotations:
            cls._remove_sidecar(path)
            return
        payload = {
            "schema": SCHEMA_VERSION,
            "doc_tags": list(doc.doc_tags),
            "annotations": [a.to_dict() for a in doc.annotations],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        atomic_write_text(path, text, backup=False, hidden=True)
