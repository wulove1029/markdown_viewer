"""Import arbitrary note attachments and build portable Markdown links."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .image_paste import ASSETS_DIR_NAME
from .resource_links import markdown_resource_link


def _unique_target(directory: Path, source: Path) -> Path:
    target = directory / source.name
    if not target.exists():
        return target
    index = 1
    while True:
        target = directory / f"{source.stem}-{index}{source.suffix}"
        if not target.exists():
            return target
        index += 1


def import_attachment_file(
    source_path: str | Path, document_path: str | Path
) -> str:
    """Reference a local file in-place or copy it into ``assets``.

    The returned path is relative to the Markdown document and always uses
    forward slashes. Files outside the document tree are copied with a
    collision-safe name; source files are never moved.
    """
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise OSError(f"Attachment is not a file: {source}")
    document = Path(document_path).resolve(strict=False)
    document_dir = document.parent
    if source == document:
        raise ValueError("A note cannot attach itself")
    try:
        relative = os.path.relpath(source, document_dir)
    except ValueError:
        relative = None
    if relative is not None and not relative.startswith(".."):
        return Path(relative).as_posix()

    assets = document_dir / ASSETS_DIR_NAME
    assets.mkdir(parents=True, exist_ok=True)
    target = _unique_target(assets, source)
    shutil.copy2(source, target)
    return Path(os.path.relpath(target, document_dir)).as_posix()


def markdown_attachment_link(relative_path: str, label: str | None = None) -> str:
    """Return a readable Markdown link for an imported attachment."""
    normalized = Path(relative_path).as_posix()
    return markdown_resource_link(
        normalized,
        label=label or Path(normalized).name,
        image=False,
    )
