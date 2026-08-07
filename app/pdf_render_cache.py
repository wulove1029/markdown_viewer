"""Memory-bounded render cache shared by PDF preview/tile compositors.

The cache deliberately knows nothing about ``QPixmap``.  Values may be widget
pixmaps today or GPU texture handles in a future compositor; callers provide the
estimated byte cost and immutable metadata used to find a preview for a page.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from math import log
from typing import Any, Hashable, Iterable


@dataclass(frozen=True)
class PdfRenderMeta:
    generation: int
    page: int
    kind: str  # "page", "preview", or "tile"
    dpr100: int
    # Whole-raster size for page/preview; full target page size for a tile.
    page_px: tuple[int, int]
    clip: tuple[int, int, int, int] | None = None  # physical page pixels


@dataclass
class _Entry:
    value: Any
    byte_size: int
    meta: PdfRenderMeta


class PdfRenderCache:
    """Least-recently-used cache with a hard byte budget."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max(1, int(max_bytes))
        self._items: OrderedDict[Hashable, _Entry] = OrderedDict()
        self._bytes_used = 0

    def __bool__(self) -> bool:
        return bool(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @property
    def bytes_used(self) -> int:
        return self._bytes_used

    def clear(self) -> None:
        self._items.clear()
        self._bytes_used = 0

    def get(self, key: Hashable, default=None):
        entry = self._items.get(key)
        if entry is None:
            return default
        self._items.move_to_end(key)
        return entry.value

    def meta(self, key: Hashable) -> PdfRenderMeta | None:
        entry = self._items.get(key)
        return entry.meta if entry is not None else None

    def put(
        self,
        key: Hashable,
        value: Any,
        byte_size: int,
        meta: PdfRenderMeta,
    ) -> bool:
        """Insert *value* and evict old entries; reject one item over budget."""
        byte_size = max(0, int(byte_size))
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes_used -= old.byte_size
        if byte_size > self.max_bytes:
            return False
        self._items[key] = _Entry(value, byte_size, meta)
        self._bytes_used += byte_size
        while self._bytes_used > self.max_bytes and self._items:
            _old_key, evicted = self._items.popitem(last=False)
            self._bytes_used -= evicted.byte_size
        return key in self._items

    def remove(self, key: Hashable) -> None:
        entry = self._items.pop(key, None)
        if entry is not None:
            self._bytes_used -= entry.byte_size

    def items_for_page(
        self,
        generation: int,
        page: int,
        kinds: Iterable[str] | None = None,
    ):
        allowed = set(kinds) if kinds is not None else None
        return [
            (key, entry.value, entry.meta)
            for key, entry in self._items.items()
            if entry.meta.generation == generation
            and entry.meta.page == page
            and (allowed is None or entry.meta.kind in allowed)
        ]

    def best_page_preview(
        self,
        generation: int,
        page: int,
        dpr100: int,
        target_page_px: tuple[int, int],
    ):
        """Return the closest cached whole-page image and mark it recently used."""
        target_w, target_h = target_page_px
        if target_w <= 0 or target_h <= 0:
            return None
        best = None
        best_score = float("inf")
        for key, entry in self._items.items():
            meta = entry.meta
            if (
                meta.generation != generation
                or meta.page != page
                or meta.kind not in ("page", "preview")
                or meta.clip is not None
            ):
                continue
            source_w, source_h = meta.page_px
            if source_w <= 0 or source_h <= 0:
                continue
            # Log-distance treats 0.5x and 2x symmetrically. Prefer the same
            # device pixel ratio when two cached resolutions are comparable.
            score = abs(log(source_w / target_w)) + abs(log(source_h / target_h))
            if meta.dpr100 != dpr100:
                score += 0.25
            if score < best_score:
                best = (key, entry.value, meta)
                best_score = score
        if best is not None:
            self._items.move_to_end(best[0])
        return best
