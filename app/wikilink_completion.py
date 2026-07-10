"""Pure helpers for building and filtering wiki-link completions."""

from __future__ import annotations

import re
from pathlib import Path


MAX_COMPLETIONS = 50
_ACTIVE_QUERY_RE = re.compile(r"\[\[([^\[\]\n|]*)$")


def completion_candidates(roots, files) -> list[str]:
    """Return unique root-relative ``.md`` paths without their extension.

    Roots are checked in order. This matters when the current directory is
    nested inside a document library: the library-relative label keeps the
    useful subfolder context instead of collapsing every entry to a basename.
    """
    normalized_roots = [Path(root).resolve() for root in roots]
    seen_paths: set[str] = set()
    seen_labels: set[str] = set()
    labels: list[str] = []

    for item in files:
        path = Path(item)
        if path.suffix.lower() != ".md":
            continue
        resolved = path.resolve()
        path_key = str(resolved).casefold()
        if path_key in seen_paths:
            continue

        relative = None
        for root in normalized_roots:
            try:
                relative = resolved.relative_to(root)
                break
            except ValueError:
                continue
        if relative is None:
            continue

        seen_paths.add(path_key)
        label = relative.with_suffix("").as_posix()
        label_key = label.casefold()
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        labels.append(label)

    return sorted(labels, key=lambda value: (value.casefold(), value))


def filter_completions(candidates, query: str, limit: int = MAX_COMPLETIONS) -> list[str]:
    """Return the best case-insensitive substring matches, capped at 50."""
    maximum = min(MAX_COMPLETIONS, max(0, int(limit)))
    if maximum == 0:
        return []

    needle = (query or "").strip().casefold()
    unique: dict[str, str] = {}
    for candidate in candidates:
        label = str(candidate)
        unique.setdefault(label.casefold(), label)

    ranked: list[tuple[tuple, str]] = []
    for folded, label in unique.items():
        basename = folded.rsplit("/", 1)[-1]
        if needle and needle not in folded:
            continue
        if not needle:
            score = (0, 0, len(folded), folded, label)
        elif folded == needle:
            score = (0, 0, len(folded), folded, label)
        elif basename == needle:
            score = (1, 0, len(folded), folded, label)
        elif folded.startswith(needle):
            score = (2, 0, len(folded), folded, label)
        elif basename.startswith(needle):
            score = (3, 0, len(folded), folded, label)
        else:
            position = folded.find(needle)
            at_word_start = position == 0 or folded[position - 1] in "/ _-"
            score = (
                4 if at_word_start else 5,
                position,
                len(folded),
                folded,
                label,
            )
        ranked.append((score, label))

    ranked.sort(key=lambda item: item[0])
    return [label for _score, label in ranked[:maximum]]


def active_query(text_before_cursor: str) -> str | None:
    """Return text typed after the active ``[[``, or None outside a wiki-link."""
    match = _ACTIVE_QUERY_RE.search(text_before_cursor or "")
    return match.group(1) if match else None
