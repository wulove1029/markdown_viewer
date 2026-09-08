"""Read-only extraction of Adobe-style annotations embedded inside a PDF.

This is a **different** feature from ``pdf_notes.py`` / ``pdf_highlights.py``:
those store the app's own page notes and text highlights in sidecar JSON files
next to the PDF. This module instead reads annotations that already live
*inside* the PDF file itself — the kind Adobe Acrobat (or any other PDF editor)
writes directly into the document (Text/sticky notes, Highlight, FreeText,
Underline, StrikeOut, Squiggly, Square, Circle, Line, Polygon, Ink, ...). The
PDF is opened read-only and is never modified, saved, or incrementally updated.

Pure data layer: no Qt here, so it is testable without constructing a widget.
Follows the same lazy-pymupdf-import pattern as ``pdf_view.extract_outline`` —
importing pymupdf costs real time on cold start, so it happens on first use.

Geometry is reported in **PDF page points with a top-left origin**, the same
convention ``pdf_highlights.Rect`` uses, so ``PdfView`` can project it with the
existing page-rect transform and paint the annotations itself. The app never
relies on PDFium's own annotation rasteriser: Acrobat does not paint reply
icons on the page at all, and PDFium scales a ``/NoZoom`` sticky-note icon with
the zoom level, which is exactly the "giant purple square" artefact this
overlay replaces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import logging
from pathlib import Path
import re

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
TEXT_MARKUP_TYPES = {"Highlight", "Underline", "StrikeOut", "Squiggly"}
SHAPE_TYPES = {"Square", "Circle", "Line", "Polygon", "PolyLine", "Ink"}
_INTERESTING_TYPES = (
    {"Text", "FreeText"} | TEXT_MARKUP_TYPES | SHAPE_TYPES
)

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")


@dataclass
class EmbeddedAnnotation:
    """One Acrobat-style annotation embedded inside a PDF page.

    ``rect`` is (x, y, w, h) in PDF page-point coordinates, top-left origin —
    the same convention ``pdf_highlights.Rect`` uses, so a caller can hand it
    straight to ``PdfView.reveal``. ``quads`` holds one (x, y, w, h) box per
    highlighted line/word when the underlying annotation exposes QuadPoints
    (Highlight/Underline/StrikeOut/Squiggly do); otherwise empty.

    ``vertices`` carries raw (x, y) points for Line/Polygon/PolyLine, and
    ``ink`` a list of stroke point-lists for Ink. ``in_reply_to`` is the xref
    of the annotation this one replies to (Acrobat's ``/IRT``) or ``None`` for
    a top-level annotation; the panel uses it to build discussion threads and
    the view uses it to keep reply icons off the page, exactly like Acrobat.
    """

    page: int
    kind: str
    author: str = ""
    modified: str = ""
    content: str = ""
    rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    quads: list[tuple[float, float, float, float]] = field(default_factory=list)
    color: str | None = None
    xref: int = 0
    in_reply_to: int | None = None
    opacity: float = 1.0
    subject: str = ""
    marked_text: str = ""
    icon: str = ""
    vertices: list[tuple[float, float]] = field(default_factory=list)
    ink: list[list[tuple[float, float]]] = field(default_factory=list)

    @property
    def is_reply(self) -> bool:
        return self.in_reply_to is not None

    @property
    def note_text(self) -> str:
        """The annotation's own note text, never the underlying page text."""
        return "" if self.content == self.marked_text else self.content


def _rect_tuple(rect) -> tuple[float, float, float, float]:
    return (
        float(rect.x0),
        float(rect.y0),
        float(rect.x1 - rect.x0),
        float(rect.y1 - rect.y0),
    )


def _points(seq) -> list[tuple[float, float]]:
    try:
        return [(float(x), float(y)) for x, y in seq]
    except (TypeError, ValueError):
        return []


def _quads_from_vertices(vertices) -> list[tuple[float, float, float, float]]:
    """Group a flat vertex quad-list into (x, y, w, h) boxes.

    PyMuPDF reports vertices as 4 points per quad (top-left, top-right,
    bottom-left, bottom-right), possibly several quads for a multi-line
    selection. Any malformed/odd-length input degrades to an empty list
    rather than raising.
    """
    if not vertices:
        return []
    points = _points(vertices)
    if not points:
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


def _opacity(annot) -> float:
    """The annotation's /CA, clamped to 0..1; fully opaque when unset (-1)."""
    try:
        value = float(annot.opacity)
    except (AttributeError, TypeError, ValueError):
        return 1.0
    if value < 0.0:
        return 1.0
    return max(0.0, min(1.0, value))


def _xref_key(doc, xref: int, key: str):
    """Return the raw value string of a PDF object key, or '' when absent."""
    try:
        kind, value = doc.xref_get_key(int(xref), key)
    except Exception:
        return ""
    if kind == "null":
        return ""
    return value or ""


def _pdf_string(raw: str) -> str:
    """Decode the ``xref_get_key`` form of a PDF string into plain text."""
    text = str(raw or "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    return text


def _rich_text_to_plain(raw: str) -> str:
    """Flatten Acrobat's ``/RC`` XHTML rich-text blob into plain text."""
    text = _pdf_string(raw)
    if not text:
        return ""
    # Paragraph/line breaks become newlines before the tags are stripped, so a
    # multi-paragraph comment does not collapse into one run-on line.
    text = re.sub(r"(?i)<\s*/\s*p\s*>|<\s*br\s*/?\s*>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _in_reply_to(doc, xref: int) -> int | None:
    """Parse ``/IRT`` (an indirect reference like ``367 0 R``) into an xref."""
    raw = str(_xref_key(doc, xref, "IRT") or "").strip()
    match = re.match(r"^(\d+)\s+\d+\s+R$", raw)
    if not match:
        return None
    parent = int(match.group(1))
    return parent if parent > 0 else None


def _popup_content(doc, xref: int) -> str:
    """Note text stored on the annotation's ``/Popup`` object, if any."""
    raw = str(_xref_key(doc, xref, "Popup") or "").strip()
    match = re.match(r"^(\d+)\s+\d+\s+R$", raw)
    if not match:
        return ""
    return _pdf_string(_xref_key(doc, int(match.group(1)), "Contents"))


def _marked_text(page, annot, quads) -> str:
    """The page text under a text-markup annotation's QuadPoints."""
    mupdf = _pymupdf()
    pieces: list[str] = []
    try:
        if quads and mupdf is not None:
            for x, y, w, h in quads:
                # A QuadPoints box typically overshoots its line by a fraction
                # of a point and clips the *next* line into the result, so the
                # box is inset vertically before the text under it is read.
                inset = min(h * 0.2, 2.0) if h > 0 else 0.0
                chunk = page.get_textbox(
                    mupdf.Rect(x, y + inset, x + w, y + h - inset)
                )
                chunk = str(chunk or "").strip()
                if chunk:
                    pieces.append(chunk)
        if not pieces:
            pieces = [str(page.get_textbox(annot.rect) or "").strip()]
    except Exception:
        return ""
    return " ".join(p for p in pieces if p).strip()


def _resolve_content(doc, page, annot, info, type_name, quads) -> tuple[str, str]:
    """Return (display_content, marked_text) for one annotation.

    Acrobat spreads a comment's text across several places depending on how it
    was authored, so they are tried in order: ``/Contents``, the ``/RC``
    rich-text blob, and finally the ``/Popup`` object's own ``/Contents``.
    ``/Subj`` is *not* folded in here — it is Acrobat's type label ("螢光標示")
    rather than user text, and is reported separately as ``subject``.
    """
    content = str(info.get("content") or "").strip()
    xref = int(getattr(annot, "xref", 0) or 0)
    if not content and xref:
        content = _rich_text_to_plain(_xref_key(doc, xref, "RC"))
    if not content and xref:
        content = _popup_content(doc, xref).strip()

    marked = ""
    if type_name in TEXT_MARKUP_TYPES:
        marked = _marked_text(page, annot, quads)
    # A markup annotation with no note text still has meaning: the underlying
    # passage. Fall back to it so a reader never sees an empty entry.
    if not content:
        content = marked
    return content, marked


def _extract_one_annotation(doc, page, annot) -> EmbeddedAnnotation | None:
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
    author = str(info.get("title") or "").strip()
    modified = str(info.get("modDate") or "").strip()
    subject = str(info.get("subject") or "").strip()
    icon = str(info.get("name") or "").strip()

    try:
        rect = _rect_tuple(annot.rect)
    except Exception:
        rect = (0.0, 0.0, 0.0, 0.0)

    try:
        raw_vertices = annot.vertices
    except Exception:
        raw_vertices = None

    quads: list[tuple[float, float, float, float]] = []
    vertices: list[tuple[float, float]] = []
    ink: list[list[tuple[float, float]]] = []
    if type_name == "Ink":
        # PyMuPDF reports Ink geometry as one point-list per stroke.
        for stroke in raw_vertices or []:
            stroke_points = _points(stroke)
            if len(stroke_points) >= 2:
                ink.append(stroke_points)
    elif type_name in TEXT_MARKUP_TYPES:
        quads = _quads_from_vertices(raw_vertices)
    elif type_name in {"Line", "Polygon", "PolyLine"}:
        vertices = _points(raw_vertices or [])

    xref = int(getattr(annot, "xref", 0) or 0)
    content, marked = _resolve_content(doc, page, annot, info, type_name, quads)

    return EmbeddedAnnotation(
        page=page.number,
        kind=type_name,
        author=author,
        modified=modified,
        content=content,
        rect=rect,
        quads=quads,
        color=_color_hex(annot),
        xref=xref,
        in_reply_to=_in_reply_to(doc, xref) if xref else None,
        opacity=_opacity(annot),
        subject=subject,
        marked_text=marked,
        icon=icon,
        vertices=vertices,
        ink=ink,
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
                    entry = _extract_one_annotation(doc, page, annot)
                    if entry is not None:
                        results.append(entry)
    except Exception:
        logger.debug(
            "Failed to extract embedded annotations from %s", path, exc_info=True
        )
        return []
    return _drop_orphan_reply_links(results)


def _drop_orphan_reply_links(entries: list[EmbeddedAnnotation]):
    """Clear ``in_reply_to`` when the parent is not among the extracted set.

    A reply whose parent was filtered out (an uninteresting subtype, or an
    annotation on another page) must still be listed and drawn on its own,
    never silently hidden as somebody's child.
    """
    known = {e.xref for e in entries if e.xref}
    for entry in entries:
        if entry.in_reply_to is not None and entry.in_reply_to not in known:
            entry.in_reply_to = None
    return entries


def build_annotation_threads(entries):
    """Group annotations into ``(parent, [replies...])`` discussion threads.

    Preserves document order for parents and for the replies inside each
    thread, so the panel can render an Acrobat-like threaded list.
    """
    entries = list(entries or [])
    by_xref = {e.xref: e for e in entries if e.xref}
    replies: dict[int, list] = {}
    threads: list[tuple] = []
    for entry in entries:
        parent_xref = entry.in_reply_to
        if parent_xref is not None and parent_xref in by_xref:
            replies.setdefault(parent_xref, []).append(entry)
        else:
            threads.append(entry)
    return [(parent, replies.get(parent.xref, [])) for parent in threads]


# --------------------------------------------------------------------------
# Presentation helpers (plain strings, no Qt) shared by the panel and the view
# --------------------------------------------------------------------------

# Traditional-Chinese names for the annotation kinds pymupdf reports.
KIND_LABELS = {
    "Text": "便利貼",
    "Highlight": "螢光標記",
    "FreeText": "文字方塊",
    "Underline": "底線",
    "StrikeOut": "刪除線",
    "Squiggly": "波浪底線",
    "Square": "方框",
    "Circle": "橢圓",
    "Line": "直線",
    "Polygon": "多邊形",
    "PolyLine": "折線",
    "Ink": "手繪",
}


def kind_label(entry) -> str:
    """Display name for an annotation's type.

    Acrobat shows its own localized type name ("螢光標示") in the comment list
    whenever an annotation carries no text of its own, and stores that name in
    ``/Subj``; prefer it so the app matches what the author saw in Acrobat.
    """
    return entry.subject or KIND_LABELS.get(entry.kind, entry.kind)


def summary_text(entry) -> str:
    """One-line gist of an annotation: its note text, else what it marks."""
    text = entry.note_text or entry.marked_text or entry.content
    return " ".join(str(text or "").split())


def tooltip_text(entry, replies=()) -> str:
    """Multi-line hover text: author, time, marked passage, note, replies."""
    lines = [f"第 {entry.page + 1} 頁　{kind_label(entry)}"]
    if entry.author:
        lines.append(f"作者：{entry.author}")
    if entry.modified:
        lines.append(f"修改時間：{entry.modified}")
    if entry.marked_text:
        lines.append(f"標記文字：{entry.marked_text}")
    if entry.note_text:
        lines.append(entry.note_text)
    elif not entry.marked_text and entry.content:
        lines.append(entry.content)
    for reply in replies or ():
        who = reply.author or "回覆"
        lines.append(f"↳ {who}：{summary_text(reply) or '（無文字內容）'}")
    return "\n".join(lines)
