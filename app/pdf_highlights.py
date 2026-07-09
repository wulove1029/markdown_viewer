"""Text-anchored highlights for PDFs, persisted in a sidecar next to the file.

Unlike page-level notes (``pdf_notes.py``), a highlight pins to the actual text
geometry the user selected. The selection is captured as one rectangle per
visual line in **PDF page coordinates** (points, top-left origin, unscaled), so
the highlight re-projects correctly at any zoom/window size and survives reopen.
Persistence reuses the same crash-safe sidecar pattern as the page notes, with a
distinct ``.highlights.json`` suffix so it never collides with ``.notes.json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from pathlib import Path

from .atomic_io import atomic_write_text

SCHEMA_VERSION = 1
DEFAULT_COLOR = "#ffd54f"  # yellow — matches pdf_notes / markdown annotations


@dataclass
class Rect:
    """One highlight rectangle in PDF page-point coordinates (top-left origin)."""

    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Rect":
        return Rect(
            x=float(d.get("x", 0.0)),
            y=float(d.get("y", 0.0)),
            w=float(d.get("w", 0.0)),
            h=float(d.get("h", 0.0)),
        )


@dataclass
class PdfHighlight:
    id: str
    page: int
    rects: list[Rect] = field(default_factory=list)
    text: str = ""
    color: str = DEFAULT_COLOR
    note: str = ""
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""

    @staticmethod
    def new(page: int, rects, text: str = "", color: str = DEFAULT_COLOR,
            note: str = "", tags=None) -> "PdfHighlight":
        from uuid import uuid4
        now = datetime.now().isoformat(timespec="seconds")
        return PdfHighlight(
            id=uuid4().hex,
            page=int(page),
            rects=[r if isinstance(r, Rect) else Rect.from_dict(r) for r in (rects or [])],
            text=text,
            color=color,
            note=note,
            tags=list(tags or []),
            created=now,
            updated=now,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "page": self.page,
            "rects": [r.to_dict() for r in self.rects],
            "text": self.text,
            "color": self.color,
            "note": self.note,
            "tags": list(self.tags),
            "created": self.created,
            "updated": self.updated,
        }

    @staticmethod
    def from_dict(d: dict) -> "PdfHighlight":
        from uuid import uuid4
        return PdfHighlight(
            id=d.get("id") or uuid4().hex,
            page=int(d.get("page", 0)),
            rects=[Rect.from_dict(r) for r in d.get("rects", [])],
            text=d.get("text", ""),
            color=d.get("color", DEFAULT_COLOR),
            note=d.get("note", ""),
            tags=list(d.get("tags", [])),
            created=d.get("created", ""),
            updated=d.get("updated", ""),
        )


class PdfHighlightStore:
    @staticmethod
    def sidecar_path(pdf_path) -> Path:
        p = Path(pdf_path)
        return p.with_name(p.name + ".highlights.json")

    @classmethod
    def load(cls, pdf_path) -> list[PdfHighlight]:
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
        highlights = [PdfHighlight.from_dict(h) for h in data.get("highlights", [])]
        highlights.sort(key=lambda h: (h.page, h.created))
        return highlights

    @classmethod
    def save(cls, pdf_path, highlights: list[PdfHighlight]) -> None:
        path = cls.sidecar_path(pdf_path)
        if not highlights:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        payload = {
            "schema": SCHEMA_VERSION,
            "highlights": [h.to_dict() for h in highlights],
        }
        atomic_write_text(
            path, json.dumps(payload, ensure_ascii=False, indent=2), backup=False
        )
