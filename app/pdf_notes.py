"""Page-anchored notes for PDFs, persisted in a sidecar next to the file.

Qt's PDF widget exposes no widget->page coordinate transform or readable text
selection, so pixel-precise text highlights aren't possible without replacing
the renderer. Page-level notes give most of the studying value (annotate a
page, tag it, jump back to it) and reuse the same crash-safe sidecar pattern
as the Markdown annotations.
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
    def load(cls, pdf_path) -> list[PdfNote]:
        path = cls.sidecar_path(pdf_path)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            try:
                path.replace(path.with_suffix(path.suffix + ".bak"))
            except OSError:
                pass
            return []
        notes = [PdfNote.from_dict(n) for n in data.get("notes", [])]
        notes.sort(key=lambda n: (n.page, n.created))
        return notes

    @classmethod
    def save(cls, pdf_path, notes: list[PdfNote]) -> None:
        path = cls.sidecar_path(pdf_path)
        payload = {
            "schema": SCHEMA_VERSION,
            "notes": [n.to_dict() for n in notes],
        }
        atomic_write_text(
            path, json.dumps(payload, ensure_ascii=False, indent=2), backup=False
        )
