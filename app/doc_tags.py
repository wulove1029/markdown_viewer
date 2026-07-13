"""Type-neutral facade for a document's document-level tags.

One API to read/write a file's DOCUMENT-LEVEL tags regardless of whether the
file is Markdown or PDF. Markdown tags live in the ``.notes.json`` sidecar via
``AnnotationStore``; PDF tags live in the ``.notes.json`` sidecar via
``PdfNoteStore``. Callers should not care which store backs a given path.
"""

from __future__ import annotations

from pathlib import Path

from .annotations import AnnotationStore
from .file_types import is_markdown, is_pdf
from .pdf_notes import PdfNoteStore


def _clean(tags) -> list[str]:
    """Dedupe, preserve order, strip empties/whitespace."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags or []:
        s = str(tag).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def read_doc_tags(path) -> list[str]:
    """Return the document-level tags for *path* (empty for unknown types)."""
    p = Path(path)
    if is_markdown(p):
        return list(AnnotationStore.load(p).doc_tags)
    if is_pdf(p):
        return PdfNoteStore.load_doc_tags(p)
    return []


def write_doc_tags(path, tags: list[str]) -> None:
    """Persist *tags* as the document-level tags for *path*.

    Tags are deduped (order preserved) and empties stripped before saving.
    Unknown file types are a safe no-op.
    """
    p = Path(path)
    cleaned = _clean(tags)
    if is_markdown(p):
        doc = AnnotationStore.load(p)
        doc.doc_tags = cleaned
        AnnotationStore.save(p, doc)
        return
    if is_pdf(p):
        PdfNoteStore.save_doc_tags(p, cleaned)
        return
    # other/unknown -> no-op for safety
    return
