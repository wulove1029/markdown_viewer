"""Tag-to-color mapping with an EndNote-style 7-color palette (stored under AppData).

Colors are resolved in two layers: an explicit user-chosen mapping (persisted to
``tag_colors.json``) takes priority; otherwise a deterministic default is derived
from a STABLE hash of the tag name so the same tag always gets the same swatch
across processes and restarts (Python's builtin ``hash()`` is salted per-process,
so it must NOT be used here).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .atomic_io import atomic_write_text


def _default_colors_path() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base or ".") / "markdown-viewer" / "tag_colors.json"


def _stable_hash(tag: str) -> int:
    """Process-stable hash of *tag* (unlike builtin hash(), which is salted)."""
    return int.from_bytes(hashlib.sha1(tag.encode("utf-8")).digest()[:4], "big")


class TagColorStore:
    """Maps a tag name to a hex color, with a fixed palette and persistence."""

    # EndNote-style palette: (Traditional-Chinese name, hex). Exactly 7 entries.
    PALETTE: list[tuple[str, str]] = [
        ("紅", "#E5484D"),
        ("橙", "#F76B15"),
        ("黃", "#F5B70A"),
        ("綠", "#30A46C"),
        ("藍", "#0091FF"),
        ("紫", "#8E4EC6"),
        ("灰", "#8B8D98"),
    ]

    def __init__(self, path: Path | None = None, colors: dict[str, str] | None = None):
        self._path = Path(path) if path else _default_colors_path()
        self._colors: dict[str, str] = dict(colors or {})

    @classmethod
    def load(cls, path: Path | None = None) -> "TagColorStore":
        target = Path(path) if path else _default_colors_path()
        colors: dict[str, str] = {}
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
            stored = raw.get("colors", {}) if isinstance(raw, dict) else {}
            if isinstance(stored, dict):
                colors = {
                    str(k): str(v) for k, v in stored.items() if isinstance(v, str)
                }
        except (json.JSONDecodeError, OSError):
            colors = {}
        return cls(path=target, colors=colors)

    def _save(self) -> None:
        payload = {"schema": 1, "colors": self._colors}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self._path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def color_for(self, tag: str) -> str:
        """Return the hex color for *tag*: explicit mapping if set, else default."""
        explicit = self._colors.get(tag)
        if explicit:
            return explicit
        return self.PALETTE[_stable_hash(tag) % len(self.PALETTE)][1]

    def explicit_color(self, tag: str) -> str | None:
        """Return the user-set color for *tag*, or None if only a default applies."""
        return self._colors.get(tag)

    def set_color(self, tag: str, hex_color: str) -> None:
        """Persist an explicit tag -> color mapping (atomic save)."""
        self._colors[tag] = hex_color
        self._save()

    def known_tags(self) -> list[str]:
        """Return tags with an explicit color registration, sorted.

        Semantically these are the tags the user has *created* (via the
        create-tag flow), so they can surface in the tag panel even before
        being assigned to any file.
        """
        return sorted(self._colors.keys())

    def remove(self, tag: str) -> None:
        """Drop *tag*'s explicit color registration if present (atomic save)."""
        if tag in self._colors:
            del self._colors[tag]
            self._save()

    @classmethod
    def palette_hexes(cls) -> list[str]:
        """Return the 7 palette hex colors in order."""
        return [hex_color for _name, hex_color in cls.PALETTE]
