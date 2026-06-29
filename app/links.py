"""Wiki-link parsing and a forward/back link index across a note collection.

Supports Obsidian-style ``[[Note]]`` and ``[[Note|alias]]`` links. The index
resolves a link target to an actual file and inverts the forward links so each
note knows which other notes point at it (backlinks).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .file_types import MARKDOWN_EXTENSIONS
from .md_converter import read_text

WIKILINK_RE = re.compile(r"\[\[\s*([^\[\]|]+?)\s*(?:\|\s*([^\[\]]+?)\s*)?\]\]")

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules",
    ".venv", "venv", ".obsidian",
}
_MAX_FILES = 8000
_MAX_BYTES = 2 * 1024 * 1024


def extract_wikilinks(text: str) -> list[tuple[str, str | None]]:
    """Return [(target, alias_or_None), ...] for each wiki-link in *text*."""
    out: list[tuple[str, str | None]] = []
    for match in WIKILINK_RE.finditer(text or ""):
        target = match.group(1).strip()
        alias = match.group(2).strip() if match.group(2) is not None else None
        if target:
            out.append((target, alias))
    return out


def _target_basename(target: str) -> tuple[str, str]:
    """Return (basename_key_lower, normalized_path) for a link target.

    Drops a ``#heading`` suffix and a trailing ``.md``; normalizes slashes.
    """
    raw = target.strip().split("#", 1)[0].strip().replace("\\", "/")
    if raw.lower().endswith(".md"):
        raw = raw[:-3]
    name = raw.rsplit("/", 1)[-1]
    return name.lower(), raw.lower()


def collect_markdown_files(roots) -> list[Path]:
    """Walk *roots*, returning Markdown files (skipping VCS/build dirs)."""
    seen: set[str] = set()
    files: list[Path] = []
    for root in roots:
        root = Path(root)
        if not root.exists() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS
            ]
            for filename in filenames:
                if Path(filename).suffix.lower() not in MARKDOWN_EXTENSIONS:
                    continue
                path = Path(dirpath) / filename
                key = str(path).casefold()
                if key in seen:
                    continue
                seen.add(key)
                files.append(path)
                if len(files) >= _MAX_FILES:
                    return files
    return files


def read_docs(files) -> list[tuple[Path, str]]:
    """Read each file to (path, text), skipping oversized/unreadable ones."""
    docs: list[tuple[Path, str]] = []
    for path in files:
        path = Path(path)
        try:
            if path.stat().st_size > _MAX_BYTES:
                docs.append((path, ""))
                continue
        except OSError:
            continue
        result = read_text(path)
        docs.append((path, result[0] if result else ""))
    return docs


class LinkIndex:
    def __init__(self):
        self._by_name: dict[str, list[Path]] = {}
        self.forward: dict[str, set[str]] = {}
        self.backward: dict[str, set[str]] = {}

    def build(self, docs) -> None:
        """Build the index from an iterable of (path, text)."""
        docs = [(Path(p), t) for p, t in docs]
        self._by_name = {}
        for path, _text in docs:
            self._by_name.setdefault(path.stem.lower(), []).append(path)

        self.forward = {}
        self.backward = {}
        for path, text in docs:
            targets: set[str] = set()
            for target, _alias in extract_wikilinks(text):
                resolved = self.resolve(target, path)
                if resolved and str(resolved) != str(path):
                    targets.add(str(resolved))
            self.forward[str(path)] = targets
            for dest in targets:
                self.backward.setdefault(dest, set()).add(str(path))

    def resolve(self, target: str, from_file=None) -> Path | None:
        """Resolve a link target to a file path, or None if unknown."""
        name_key, normalized = _target_basename(target)
        if not name_key:
            return None
        candidates = self._by_name.get(name_key, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        # Folder-qualified target (e.g. "sub/Note"): prefer a path that ends with it.
        if "/" in normalized:
            for cand in sorted(candidates, key=lambda c: len(str(c))):
                tail = str(cand).replace("\\", "/").lower()
                if tail.endswith(".md"):
                    tail = tail[:-3]
                if tail.endswith(normalized):
                    return cand

        # Otherwise prefer a file in the same folder as the linking note.
        if from_file is not None:
            parent = Path(from_file).parent
            same = [c for c in candidates if c.parent == parent]
            if same:
                return sorted(same, key=lambda c: len(str(c)))[0]

        return sorted(candidates, key=lambda c: len(str(c)))[0]

    def backlinks(self, path) -> list[str]:
        """Paths of notes that link to *path*, sorted by name."""
        sources = self.backward.get(str(Path(path)), set())
        return sorted(sources, key=lambda p: (Path(p).name.casefold(), p.casefold()))
