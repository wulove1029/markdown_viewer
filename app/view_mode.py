"""Pure state / mapping logic for the preview, edit, and split view modes.

Kept free of Qt imports so the mode state machine and the scroll-sync
mapping can be unit-tested headless (QWebEngineView tests skip offscreen).
"""

import time

PREVIEW = "preview"
EDIT = "edit"
SPLIT = "split"
MODES = (PREVIEW, EDIT, SPLIT)


def normalize(mode: str) -> str:
    """Coerce unknown values to the safe default (preview)."""
    return mode if mode in MODES else PREVIEW


def is_editing(mode: str) -> bool:
    """True when the editor owns the document buffer (edit or split)."""
    return mode in (EDIT, SPLIT)


def cycle_mode(mode: str) -> str:
    """Toolbar button: preview -> edit -> split -> preview."""
    mode = normalize(mode)
    return MODES[(MODES.index(mode) + 1) % len(MODES)]


def toggle_edit(mode: str) -> str:
    """Ctrl+E: preview -> edit; edit / split -> preview."""
    return PREVIEW if is_editing(mode) else EDIT


def toggle_split(mode: str) -> str:
    """Ctrl+Shift+E: jump straight into split; from split back to preview."""
    return PREVIEW if normalize(mode) == SPLIT else SPLIT


def editor_scroll_ratio(value: float, maximum: float) -> float:
    """Map an editor scrollbar position to a 0..1 preview scroll ratio.

    A document that fits entirely in the viewport has ``maximum == 0``;
    treat it (and any degenerate negative range) as the top.
    """
    if maximum is None or maximum <= 0:
        return 0.0
    ratio = float(value) / float(maximum)
    return max(0.0, min(1.0, ratio))


class ScrollSyncGuard:
    """Direction lock that prevents scroll-sync echo loops.

    When one surface drives the other programmatically it acquires the
    lock; scroll events reported by the *other* surface are ignored for a
    short cooldown window, so a programmatic scroll can never bounce back
    as a competing sync in the opposite direction.
    """

    def __init__(self, cooldown: float = 0.15, clock=time.monotonic):
        self._cooldown = cooldown
        self._clock = clock
        self._owner: str | None = None
        self._until = 0.0

    def try_acquire(self, source: str) -> bool:
        """Return True when *source* may drive a sync right now."""
        now = self._clock()
        if self._owner is not None and self._owner != source and now < self._until:
            return False
        self._owner = source
        self._until = now + self._cooldown
        return True
