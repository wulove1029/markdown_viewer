"""Portable records for resources recently inserted into Markdown notes.

Markdown links are relative to the note that owns them.  Persisting only the
rendered link therefore breaks when it is reused from a note in another
folder.  This module stores the resolved local file alongside its presentation
metadata so callers can import it again and generate a fresh relative link.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from .resource_links import decode_resource_path


_RESOURCE_RE = re.compile(
    r"^(?P<image>!)?\[(?P<label>(?:\\.|[^\]])*)\]"
    r"\((?:<(?P<angle>[^>]+)>|(?P<plain>[^)]+))\)$"
)
_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


@dataclass(frozen=True)
class RecentResource:
    """A reusable local resource, or a legacy Markdown link."""

    markdown_link: str
    kind: str
    label: str
    absolute_path: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        if self.absolute_path:
            return self.kind, os.path.normcase(self.absolute_path)
        return self.kind, self.markdown_link

    @property
    def display_text(self) -> str:
        icon = "圖片" if self.kind == "image" else "附件"
        name = self.label.strip()
        if not name and self.absolute_path:
            name = Path(self.absolute_path).name
        if not name:
            name = self.markdown_link
        if self.absolute_path:
            path = Path(self.absolute_path)
            location = f"{path.parent.name}/{path.name}"
        else:
            location = "舊版連結（僅原筆記位置可用）"
        return f"{icon}｜{name} — {location}"

    def to_dict(self) -> dict[str, str | None | int]:
        return {
            "schema": 1,
            "markdown_link": self.markdown_link,
            "kind": self.kind,
            "label": self.label,
            "absolute_path": self.absolute_path,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RecentResource | None":
        if not isinstance(value, dict) or value.get("schema") != 1:
            return None
        link = value.get("markdown_link")
        kind = value.get("kind")
        label = value.get("label")
        absolute_path = value.get("absolute_path")
        if not isinstance(link, str) or not link.strip():
            return None
        if kind not in {"image", "attachment"} or not isinstance(label, str):
            return None
        if absolute_path is not None and not isinstance(absolute_path, str):
            return None
        return cls(link, kind, label, absolute_path or None)


def resource_from_markdown(
    markdown_link: str, document_path: str | Path | None
) -> RecentResource | None:
    """Resolve one image/attachment link relative to its owning note."""

    link = str(markdown_link).strip()
    match = _RESOURCE_RE.fullmatch(link)
    if match is None:
        return None
    destination = decode_resource_path(
        (match.group("angle") or match.group("plain") or "").strip()
    )
    kind = "image" if match.group("image") else "attachment"
    label = match.group("label") or ""
    label = re.sub(r"\\([\\\[\]])", r"\1", label)
    absolute_path: str | None = None
    if (
        document_path
        and destination
        and not destination.startswith("#")
        and not _URI_RE.match(destination)
    ):
        candidate = Path(destination)
        if not candidate.is_absolute():
            candidate = Path(document_path).parent / candidate
        try:
            absolute_path = str(candidate.resolve(strict=False))
        except OSError:
            absolute_path = str(candidate.absolute())
    return RecentResource(link, kind, label, absolute_path)


def decode_recent_resources(raw: object) -> list[RecentResource]:
    """Decode QSettings JSON, including the former list-of-links format."""

    try:
        values = json.loads(str(raw)) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(values, list):
        return []
    result: list[RecentResource] = []
    for value in values:
        record = RecentResource.from_dict(value)
        if record is None and isinstance(value, str):
            record = resource_from_markdown(value, None)
        if record is not None and record.identity not in {
            item.identity for item in result
        }:
            result.append(record)
    return result


def encode_recent_resources(resources: list[RecentResource]) -> str:
    return json.dumps(
        [resource.to_dict() for resource in resources],
        ensure_ascii=False,
    )


def remember_recent_resource(
    resources: list[RecentResource], resource: RecentResource, limit: int = 10
) -> list[RecentResource]:
    """Move *resource* to the front and enforce a positive bounded history."""

    if limit <= 0:
        return []
    return [resource, *(
        item for item in resources if item.identity != resource.identity
    )][:limit]
