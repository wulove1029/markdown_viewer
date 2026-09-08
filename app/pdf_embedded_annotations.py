"""Read-only extraction of Adobe-style annotations embedded inside a PDF.

This is a **different** feature from ``pdf_notes.py`` / ``pdf_highlights.py``:
those store the app's own page notes and text highlights in sidecar JSON files
next to the PDF. This module instead reads annotations that already live
*inside* the PDF file itself — the kind Adobe Acrobat (or any other PDF editor)
writes directly into the document (Text/sticky notes, Highlight, FreeText,
Underline, StrikeOut, Squiggly, ...). The PDF is opened read-only and is never
modified, saved, or incrementally updated.

Pure data layer: no Qt here, so it is testable without constructing a widget.
Follows the same lazy-pymupdf-import pattern as ``pdf_view.extract_outline`` —
importing pymupdf costs real time on cold start, so it happens on first use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# PyMuPDF is optional at runtime (see pdf_view._pymupdf for the rationale).
_PYMUPDF_UNSET = object()
_pymupdf_module = _PYMUPDF_UNSET


def _pymupdf():
    """Return the pymupdf module, importing it lazily; None if unavailable."""
    global _pymupdf_module
    if _pymupdf_module is _PYMUPDF_UNSET:
        try:
            import pymupdf as _mod
        except Exception:  # pragma: no cover - import guard
            _mod = None
        _pymupdf_module = _mod
    return _pymupdf_module


# Annotation subtypes worth surfacing to a reader. Popup is intentionally
# excluded: pymupdf's ``Page.annots()`` already skips Popup/Link/Widget
# subtypes, and a Popup's note text lives in its *parent* annotation's
# ``info['content']`` anyway, so there is nothing extra to collect from it.
_INTERESTING_TYPES = {
    "Text",
    "Highlight",
    "FreeText",
    "Underline",
    "StrikeOut",
    "Squiggly",
}


@dataclass
class EmbeddedAnnotation:
    """One Acrobat-style annotation embedded inside a PDF page.

    ``rect`` is (x, y, w, h) in PDF page-point coordinates, top-left origin —
    the same convention ``pdf_highlights.Rect`` uses, so a caller can hand it
    straight to ``PdfView.reveal``. ``quads`` holds one (x, y, w, h) box per
    highlighted line/word when the underlying annotation exposes vertices
    (Highlight/Underline/StrikeOut/Squiggly typically do); otherwise empty.
    """

    page: int
    kind: str
    author: str = ""
    modified: str = ""
    content: str = ""
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    quads: list[tuple[float, float, float, float]] = field(default_factory=list)
    color: str | None = None


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return (
        float(rect.x0),
        float(rect.y0),
        float(rect.x1 - rect.x0),
        float(rect.y1 - rect.y0),
    )


def _quads_from_vertices(vertices) -> list[tuple[float, float, float, float]]:
    """Group a flat vertex quad-list into (x, y, w, h) boxes.

    PyMuPDF reports vertices as 4 points per quad (top-left, top-right,
    bottom-left, bottom-right), possibly several quads for a multi-line
    selection. Any malformed/odd-length input degrades to an empty list
    rather than raising.
    """
    if not vertices:
        return []
    try:
        points = [(float(x), float(y)) for x, y in vertices]
    except (TypeError, ValueError):
        return []
    quads = []
    for i in range(0, len(points) - 3, 4):
        xs = [p[0] for p in points[i:i + 4]]
        ys = [p[1] for p in points[i:i + 4]]
        quads.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))
    return quads


def _color_hex(annot) -> str | None:
    try:
        colors = annot.colors or {}
    except Exception:
        return None
    stroke = colors.get("stroke") or colors.get("fill")
    if not stroke:
        return None
    try:
        r, g, b = (max(0.0, min(1.0, float(c))) for c in stroke[:3])
    except (TypeError, ValueError):
        return None
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _extract_one_annotation(page, annot) -> EmbeddedAnnotation | None:
    try:
        type_name = str(annot.type[1])
    except Exception:
        return None
    if type_name not in _INTERESTING_TYPES:
        return None

    try:
        info = annot.info or {}
    except Exception:
        info = {}
    content = str(info.get("content") or "").strip()
    author = str(info.get("title") or "").strip()
    modified = str(info.get("modDate") or "").strip()

    try:
        rect = _rect_tuple(annot.rect)
    except Exception:
        rect = (0.0, 0.0, 0.0, 0.0)

    quads: list[tuple[float, float, float, float]] = []
    try:
        quads = _quads_from_vertices(annot.vertices)
    except Exception:
        quads = []

    # A markup annotation (Highlight/Underline/...) with no explicit note text
    # still has meaning: the underlying highlighted passage. Pull it from the
    # page so readers see *what* was marked, not an empty entry.
    if not content and type_name in {"Highlight", "Underline", "StrikeOut", "Squiggly"}:
        try:
            content = str(page.get_textbox(annot.rect) or "").strip()
        except Exception:
            content = ""

    return EmbeddedAnnotation(
        page=page.number,
        kind=type_name,
        author=author,
        modified=modified,
        content=content,
        rect=rect,
        quads=quads,
        color=_color_hex(annot),
    )


def extract_embedded_annotations(path, password: str = "") -> list[EmbeddedAnnotation]:
    """Return every embedded annotation in *path*, in page/document order.

    Never raises: an encrypted file with the wrong/no password, a corrupted or
    non-PDF file, or pymupdf being unavailable all degrade to an empty list.
    The file is opened read-only; nothing is ever written back to it.
    """
    mupdf = _pymupdf()
    if mupdf is None or not path:
        return []
    results: list[EmbeddedAnnotation] = []
    try:
        with mupdf.open(str(path)) as doc:
            if doc.needs_pass and not doc.authenticate(password or ""):
                return []
            for page in doc:
                try:
                    annots = list(page.annots())
                except Exception:
                    continue
                for annot in annots:
                    entry = _extract_one_annotation(page, annot)
                    if entry is not None:
                        results.append(entry)
    except Exception:
        logger.debug(
            "Failed to extract embedded annotations from %s", path, exc_info=True
        )
        return []
    return results
