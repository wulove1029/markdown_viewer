"""Shared bounded source reads and content-derived library data.

Disk text uses the search index's single memory budget. Parsed data stores no
source strings and is addressed by content, so rename/delete cannot leave a
path pointing at another document's links or tags.
"""

from __future__ import annotations

import hashlib
import sys
import threading
from collections import OrderedDict
from dataclasses import fields, is_dataclass
from pathlib import Path

from . import search_index
from .search_index import FileVersion, SearchCancelled, default_index, file_version, path_key

_READ_LOCK = threading.RLock()
_READING: dict[tuple, threading.Event] = {}


_RACY_LOCK = threading.Lock()
_RACY_HASHES: OrderedDict[tuple, tuple[bytes, int]] = OrderedDict()
_RACY_STICKY_NS = 30_000_000_000
_RACY_MAX = 256


def _racy_stamp(path: Path, base: tuple, version: FileVersion) -> bytes | None:
    """Content hash for files whose timestamps cannot prove they are unchanged.

    Only files modified within the racy window are hashed. A hash, once taken,
    is reused for the same metadata for a while so a snapshot taken inside the
    window still compares equal to one taken just after it (same content).
    """
    now = search_index._now_ns()
    with _RACY_LOCK:
        known = _RACY_HASHES.get(base)
        if known is not None and known[1] <= now:
            del _RACY_HASHES[base]
            known = None
    if known is None and not search_index.is_racy(version, now):
        return None
    digest = search_index.file_digest(path)
    if known is None or known[0] != digest:
        with _RACY_LOCK:
            _RACY_HASHES[base] = digest, now + _RACY_STICKY_NS
            _RACY_HASHES.move_to_end(base)
            while len(_RACY_HASHES) > _RACY_MAX:
                _RACY_HASHES.popitem(last=False)
    return digest


def source_signature(path: Path) -> tuple:
    path = Path(path)
    version = file_version(path)
    base = (str(path.resolve()), version.modified_ns, version.size,
            version.changed_ns, version.inode, version.device)
    return base + (_racy_stamp(path, base, version),)


def version_from_signature(signature: tuple) -> FileVersion:
    return FileVersion(signature[2], signature[1], signature[3], signature[5], signature[4])


def _decode(path):
    from .md_converter import read_text

    result = read_text(path)
    return None if result is None else (result[0], result[1], "\n")


def peek_source(path: Path, version: FileVersion) -> str | None:
    """Use only a freshly validated version.

    Normally no source I/O; a file inside the racy window is read in full
    to verify its content hash.
    """
    return default_index.peek(path, version)


def read_source(path: Path, *, should_cancel=lambda: False, version=None) -> str | None:
    """Coalesce library/metadata misses without holding a lock during decoding."""
    path = Path(path)
    while True:
        if should_cancel():
            raise SearchCancelled()
        try:
            current = version if version is not None else file_version(path)
        except OSError:
            return default_index.read(path, _decode, should_cancel)
        cached = peek_source(path, current)
        if cached is not None:
            return cached
        key = path_key(path), current
        with _READ_LOCK:
            pending = _READING.get(key)
            if pending is None:
                pending = _READING[key] = threading.Event()
                break
        while not pending.wait(0.02):
            if should_cancel():
                raise SearchCancelled()
        version = None
    try:
        return default_index.read(path, _decode, should_cancel, version=current)
    finally:
        with _READ_LOCK:
            _READING.pop(key, None)
            pending.set()


def content_key(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8", errors="surrogatepass"), digest_size=20).digest()


def _retained_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, (tuple, list, set, frozenset)):
        size += sum(_retained_size(item, seen) for item in value)
    elif is_dataclass(value):
        size += sum(_retained_size(getattr(value, field.name), seen) for field in fields(value))
    return size


class DerivedContentIndex:
    """One LRU budget for immutable parsed metadata and link targets."""

    def __init__(self, max_bytes=8 * 1024 * 1024, max_entries=16000):
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()

    @property
    def retained_bytes(self):
        with self._lock:
            return self._bytes

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def get(self, namespace, fingerprint):
        key = namespace, fingerprint
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry[0]
        return None

    def remember(self, namespace, fingerprint, value):
        key = namespace, fingerprint
        cost = _retained_size((key, value)) + 128
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            if cost > self.max_bytes or self.max_entries <= 0:
                return
            while self._entries and (
                self._bytes + cost > self.max_bytes or len(self._entries) >= self.max_entries
            ):
                _old_key, (_old_value, old_cost) = self._entries.popitem(last=False)
                self._bytes -= old_cost
            self._entries[key] = value, cost
            self._bytes += cost


derived_index = DerivedContentIndex()
