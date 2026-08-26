"""Shared content-zoom levels and deterministic stepping helpers."""

from __future__ import annotations

import math


MIN_ZOOM_FACTOR = 0.5
MAX_ZOOM_FACTOR = 3.0

# Familiar browser-style stops make low zoom recover quickly and avoid the
# increasingly small relative change produced by repeatedly adding 0.1.
ZOOM_FACTORS: tuple[float, ...] = (
    0.5,
    0.67,
    0.8,
    0.9,
    1.0,
    1.1,
    1.25,
    1.5,
    1.75,
    2.0,
    2.5,
    3.0,
)

_EPSILON = 1e-6


def clamp_zoom_factor(factor: float) -> float:
    """Return a finite zoom factor within the supported content range."""
    try:
        value = float(factor)
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if not math.isfinite(value):
        return 1.0
    return max(MIN_ZOOM_FACTOR, min(MAX_ZOOM_FACTOR, value))


def step_zoom_factor(factor: float, steps: int) -> float:
    """Move *factor* by *steps* discrete zoom stops, clamped at the ends."""
    current = clamp_zoom_factor(factor)
    try:
        remaining = int(steps)
    except (TypeError, ValueError, OverflowError):
        return current

    if remaining > 0:
        candidates = [level for level in ZOOM_FACTORS if level > current + _EPSILON]
        if not candidates:
            return MAX_ZOOM_FACTOR
        return candidates[min(remaining, len(candidates)) - 1]

    if remaining < 0:
        candidates = [level for level in ZOOM_FACTORS if level < current - _EPSILON]
        if not candidates:
            return MIN_ZOOM_FACTOR
        candidates.reverse()
        return candidates[min(-remaining, len(candidates)) - 1]

    return current
