"""Save clipboard/dropped images next to a Markdown document and build links.

Design note: when the resulting relative path contains a space, it is wrapped
in angle brackets (``<path with space.png>``) rather than percent-encoded,
since that is valid Markdown link syntax and keeps the path human-readable.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QImage

ASSETS_DIR_NAME = "assets"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}


def is_image_file(path: str | Path) -> bool:
    """Return True if *path* has a recognized image file extension."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    """Return a path in *directory* for ``stem+suffix`` that does not exist yet.

    Collisions get ``-1``, ``-2``, ... appended to the stem.
    """
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _relative_posix(target: Path, doc_dir: Path) -> str:
    rel = os.path.relpath(target, doc_dir)
    return Path(rel).as_posix()


def save_clipboard_image(qimage: QImage, doc_path: str | Path) -> str | None:
    """Save *qimage* as a PNG under ``<doc_dir>/assets`` and return its path.

    The returned path is relative to *doc_path*'s directory, using forward
    slashes regardless of platform. Returns None (and writes nothing) if
    *qimage* is null or the PNG encode/write fails.
    """
    if qimage.isNull():
        return None
    doc_dir = Path(doc_path).resolve().parent
    assets_dir = doc_dir / ASSETS_DIR_NAME
    assets_dir.mkdir(parents=True, exist_ok=True)
    stem = f"image-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target = _unique_path(assets_dir, stem, ".png")
    if not qimage.save(str(target), "PNG"):
        return None
    return _relative_posix(target, doc_dir)


def import_image_file(src_path: str | Path, doc_path: str | Path) -> str:
    """Reference or copy *src_path* into the document's assets folder.

    If *src_path* already lives inside the document's directory tree, it is
    referenced in place (no copy). Otherwise it is copied into
    ``<doc_dir>/assets`` (renaming on collision) and the new relative path is
    returned.
    """
    src = Path(src_path).resolve()
    doc_dir = Path(doc_path).resolve().parent
    try:
        rel = os.path.relpath(src, doc_dir)
    except ValueError:
        rel = None  # different drive on Windows, must copy
    if rel is not None and not rel.startswith(".."):
        return Path(rel).as_posix()

    assets_dir = doc_dir / ASSETS_DIR_NAME
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(assets_dir, src.stem, src.suffix)
    shutil.copyfile(src, target)
    return _relative_posix(target, doc_dir)


def markdown_image_link(rel_path: str, alt: str = "") -> str:
    """Build a ``![alt](path)`` Markdown image link for *rel_path*."""
    path = f"<{rel_path}>" if " " in rel_path else rel_path
    return f"![{alt}]({path})"
