"""Page-anchored notes for PDFs, persisted in a sidecar next to the file.

These are page-level notes (annotate a page, tag it, jump back to it), reusing
the same crash-safe sidecar pattern as the Markdown annotations. Text-anchored
highlights — pinned to the exact selected glyphs — live separately in
``pdf_highlights.py``, made possible by the custom paged renderer in
``pdf_view.py`` that owns the widget->page coordinate transform.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path

from .atomic_io import atomic_write_text

SCHEMA_VERSION = 1
DEFAULT_COLOR = "#ffd54f"


@dataclass
class PdfNote:
    id: str
    page: int
    note: str = ""
    color: str = DEFAULT_COLOR
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(page: int, note: str = "", color: str = DEFAULT_COLOR, tags=None) -> "PdfNote":
        from uuid import uuid4
        now = datetime.now().isoformat(timespec="seconds")
        return PdfNote(
            id=uuid4().hex, page=int(page), note=note, color=color,
            tags=list(tags or []), created=now, updated=now,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "PdfNote":
        from uuid import uuid4
        return PdfNote(
            id=d.get("id") or uuid4().hex,
            page=int(d.get("page", 0)),
            note=d.get("note", ""),
            color=d.get("color", DEFAULT_COLOR),
            tags=list(d.get("tags", [])),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
        )


class PdfNoteStore:
    @staticmethod
    def sidecar_path(pdf_path) -> Path:
        p = Path(pdf_path)
        return p.with_name(p.name + ".notes.json")

    @classmethod
    def _read_sidecar(cls, pdf_path) -> dict:
        """Load the raw sidecar dict, quarantining a corrupt file to ``.bak``."""
        path = cls.sidecar_path(pdf_path)
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
            return {}

    @classmethod
    def load(cls, pdf_path) -> list[PdfNote]:
        data = cls._read_sidecar(pdf_path)
        notes = [PdfNote.from_dict(n) for n in data.get("notes", [])]
        notes.sort(key=lambda n: (n.page, n.created))
        return notes

    @classmethod
    def load_doc_tags(cls, pdf_path) -> list[str]:
        """Document-level tags for this PDF (missing key -> empty list)."""
        data = cls._read_sidecar(pdf_path)
        return list(data.get("doc_tags", []))

    @classmethod
    def save(cls, pdf_path, notes: list[PdfNote], doc_tags: list[str] | None = None) -> None:
        path = cls.sidecar_path(pdf_path)
        # Preserve any existing document-level tags when only notes are written.
        if doc_tags is None:
            doc_tags = cls.load_doc_tags(pdf_path)
        # Don't leave an empty sidecar behind: with no notes and no doc-tags
        # there is nothing to persist, so drop the file (and its .bak).
        if not notes and not doc_tags:
            for target in (path, path.with_name(path.name + ".bak")):
                try:
                    target.unlink()
                except OSError:
                    pass
            return
        payload = {
            "schema": SCHEMA_VERSION,
            "doc_tags": list(doc_tags),
            "notes": [n.to_dict() for n in notes],
        }
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=False, indent=2),
            backup=False,
            hidden=True,
        )

    @classmethod
    def save_doc_tags(cls, pdf_path, doc_tags: list[str]) -> None:
        """Persist document-level tags without disturbing existing notes."""
        notes = cls.load(pdf_path)
        cls.save(pdf_path, notes, doc_tags=list(doc_tags))
