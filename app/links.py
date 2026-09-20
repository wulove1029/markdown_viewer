"""Wiki-link parsing and a forward/back link index across a note collection.

Supports Obsidian-style ``[[Note]]`` and ``[[Note|alias]]`` links. The index
resolves a link target to an actual file and inverts the forward links so each
note knows which other notes point at it (backlinks).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .document_libraries import load_excluded_folders, should_skip_directory
from .file_types import MARKDOWN_EXTENSIONS
from .md_converter import (
    body_hashtags,
    front_matter_tags,
    mask_markdown_code,
    parse_front_matter,
    read_text,
)

WIKILINK_RE = re.compile(
    r"\[\[[^\S\r\n]*([^\[\]|\r\n]+?)[^\S\r\n]*"
    r"(?:\|[^\S\r\n]*([^\[\]\r\n]+?)[^\S\r\n]*)?\]\]"
)

_MAX_FILES = 8000
_MAX_BYTES = 2 * 1024 * 1024


def _local_markdown_target(target: str) -> bool:
    try:
        url = urlsplit(target)
    except ValueError:
        return False
    path = unquote(url.path)
    return bool(not url.scheme and not url.netloc and path
                and not path.startswith(("/", "\\"))
                and Path(path).suffix.lower() in MARKDOWN_EXTENSIONS)


def _angle_markdown_link(state, silent):
    match = re.match(r"<([^<>\s]+)>", state.src[state.pos:])
    if not match or not _local_markdown_target(match[1]):
        return False
    if not silent:
        token = state.push("link_open", "a", 1)
        token.attrSet("href", match[1])
        state.push("link_close", "a", -1)
    state.pos += len(match[0])
    return True


def _link_parser() -> MarkdownIt:
    parser = MarkdownIt("commonmark")
    parser.inline.ruler.before("autolink", "local_markdown_link", _angle_markdown_link)
    return parser


def extract_markdown_links(text: str, *, _parser=None) -> list[str]:
    """Extract local Markdown destinations, excluding images and code."""
    parser = _parser or _link_parser()
    # Preserve CommonMark fence lengths, tilde fences and indented blocks.
    # The legacy wiki-link masker cannot represent all these contexts.
    return [child.attrGet("href") for token in parser.parse(text)
            for child in token.children or ()
            if child.type == "link_open" and _local_markdown_target(child.attrGet("href") or "")]


def extract_wikilinks(text: str) -> list[tuple[str, str | None]]:
    """Return [(target, alias_or_None), ...] for each wiki-link in *text*."""
    out: list[tuple[str, str | None]] = []
    for match in WIKILINK_RE.finditer(mask_markdown_code(text)):
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
    excluded_folders = load_excluded_folders()
    for root in roots:
        root = Path(root)
        if not root.exists() or not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            relative_parent = Path(dirpath).relative_to(root)
            dirnames[:] = [
                d
                for d in dirnames
                if not should_skip_directory(
                    relative_parent / d, excluded_folders
                )
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
        # Raw targets are retained for consumers such as Graph view.  The
        # existing forward/backward maps intentionally contain only resolved
        # files, while this map also preserves links to not-yet-created notes.
        self.raw_targets: dict[str, tuple[str, ...]] = {}
        self.typed_targets: dict[str, tuple[tuple[str, str], ...]] = {}
        self._by_path: dict[str, Path] = {}
        self.tags: dict[str, set[str]] = {}
        self.completion_candidates: list[str] = []

    def build(self, docs) -> None:
        """Build the index from an iterable of (path, text)."""
        docs = [(Path(p), t) for p, t in docs]
        self._by_name = {}
        self._by_path = {os.path.normcase(os.path.abspath(p)): p for p, _ in docs}
        for path, _text in docs:
            self._by_name.setdefault(path.stem.lower(), []).append(path)

        self.forward = {}
        self.backward = {}
        self.raw_targets = {}
        self.typed_targets = {}
        self.tags = {}
        parser = _link_parser()
        for path, text in docs:
            front, body = parse_front_matter(text)
            self.tags[str(path)] = set(front_matter_tags(front)) | set(body_hashtags(body))
            targets: set[str] = set()
            raw_targets = [target for target, _alias in extract_wikilinks(text)]
            self.raw_targets[str(path)] = tuple(raw_targets)
            typed = [(target, "wiki") for target in raw_targets]
            typed.extend((target, "markdown") for target in extract_markdown_links(text, _parser=parser))
            self.typed_targets[str(path)] = tuple(typed)
            for target, kind in typed:
                resolved = (self.resolve_markdown(target, path) if kind == "markdown"
                            else self.resolve(target, path))
                if resolved and str(resolved) != str(path):
                    targets.add(str(resolved))
            self.forward[str(path)] = targets
            for dest in targets:
                self.backward.setdefault(dest, set()).add(str(path))

    def resolve_markdown(self, target: str, from_file: str | Path) -> Path | None:
        """Resolve only the exact source-relative indexed file, never a basename."""
        relative = unquote(urlsplit(target).path)
        key = os.path.normcase(os.path.abspath(Path(from_file).parent / relative))
        return self._by_path.get(key)

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
