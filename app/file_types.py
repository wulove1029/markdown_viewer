"""Supported document file type helpers."""

from __future__ import annotations

from pathlib import Path

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = MARKDOWN_EXTENSIONS | PDF_EXTENSIONS


def document_kind(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    return ""


def is_markdown(path: str | Path) -> bool:
    return document_kind(path) == "markdown"


def is_pdf(path: str | Path) -> bool:
    return document_kind(path) == "pdf"


def is_supported_document(path: str | Path) -> bool:
    return bool(document_kind(path))
