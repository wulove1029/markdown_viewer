"""Safe Markdown links for local note resources."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote


def encode_resource_path(path: str | Path) -> str:
    """Encode a relative local path so Markdown/QUrl cannot split fragments."""

    normalized = Path(path).as_posix()
    return quote(normalized, safe="/-._~")


def decode_resource_path(destination: str) -> str:
    """Decode a destination produced by :func:`encode_resource_path`."""

    return unquote(str(destination))


def escape_resource_label(label: str) -> str:
    """Escape label characters that terminate Markdown link text."""

    return (
        str(label)
        .replace("\\", r"\\")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def markdown_resource_link(
    relative_path: str | Path,
    *,
    label: str = "",
    image: bool = False,
) -> str:
    prefix = "!" if image else ""
    return (
        f"{prefix}[{escape_resource_label(label)}]"
        f"({encode_resource_path(relative_path)})"
    )
