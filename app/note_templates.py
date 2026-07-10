"""Daily-note creation and Markdown template helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from .atomic_io import atomic_write_bytes


def render_template(text: str, title: str, now: datetime | None = None) -> str:
    """Replace the supported note-template variables in *text*.

    ``now`` is injectable so callers and tests can use a deterministic clock.
    """
    current = now or datetime.now()
    replacements = {
        "{{date}}": current.strftime("%Y-%m-%d"),
        "{{time}}": current.strftime("%H:%M"),
        "{{title}}": title,
    }
    for variable, value in replacements.items():
        text = text.replace(variable, value)
    return text


def default_subfolder(libraries: Iterable[object], name: str) -> Path | None:
    """Return *name* below the first document-library root, if available."""
    for library in libraries:
        root = str(getattr(library, "path", "") or "").strip()
        if root:
            return Path(root) / name
    return None


def find_templates(folder: str | Path) -> list[Path]:
    """Return all Markdown templates below *folder*, or ``[]`` if unavailable."""
    root = Path(folder)
    if not root.is_dir():
        return []
    try:
        templates = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".md"
        ]
    except OSError:
        return []
    return sorted(
        templates,
        key=lambda path: str(path.relative_to(root)).casefold(),
    )


def render_template_file(
    template_path: str | Path,
    title: str,
    now: datetime | None = None,
) -> str:
    """Read a UTF-8 Markdown template and replace its supported variables."""
    text = Path(template_path).read_text(encoding="utf-8")
    return render_template(text, title, now)


def open_or_create_daily_note(
    folder: str | Path,
    template_path: str | Path | None = None,
    now: datetime | None = None,
) -> tuple[Path, bool]:
    """Return today's exact note path, creating it atomically when absent."""
    current = now or datetime.now()
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{current:%Y-%m-%d}.md"
    if path.exists():
        return path, False

    content = ""
    if template_path:
        content = render_template_file(template_path, path.stem, current)
    atomic_write_bytes(path, content.encode("utf-8"), backup=False)
    return path, True
