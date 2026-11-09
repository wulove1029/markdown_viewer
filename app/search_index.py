"""Bounded, rebuildable content index for exact substring library searches.

Entries are keyed by canonical path and filesystem version, never by filename.
The cache is intentionally process-local: document text is not copied into an
unprotected persistent database, and restarting always rebuilds from source.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable


class SearchCancelled(Exception):
    """Stop a worker before it publishes results from an obsolete request."""


@dataclass(frozen=True, slots=True)
class FileVersion:
    size: int
    modified_ns: int
    changed_ns: int
    device: int
    inode: int


# Timestamps are only trusted once they are older than the cache by more than
# this window (racy-git rule). It must exceed the coarsest tick we may meet:
# NTFS clock ~15.6 ms, FAT/exFAT mtime 2 s. Only files modified inside the
# window are ever re-read or hashed; everything else stays metadata-only.
RACY_WINDOW_NS = 2_500_000_000


_FILETIME_EPOCH_NS = 116444736000000000 * 100


def _now_ns() -> int:
    return time.time_ns()


def is_racy(version: FileVersion, now_ns: int | None = None) -> bool:
    """True when a same-size rewrite could still carry this exact version."""
    now = _now_ns() if now_ns is None else now_ns
    changed = version.changed_ns
    if os.name == "nt":
        changed -= _FILETIME_EPOCH_NS  # NTFS ChangeTime counts from 1601
    return max(version.modified_ns, changed) + RACY_WINDOW_NS >= now


def file_digest(path: str | Path) -> bytes:
    digest = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation", ctypes.c_longlong),
            ("access", ctypes.c_longlong),
            ("write", ctypes.c_longlong),
            ("change", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
        ]

    class _DirectoryInfo(ctypes.Structure):
        _fields_ = [
            ("next_offset", wintypes.DWORD),
            ("index", wintypes.DWORD),
            ("creation", ctypes.c_longlong),
            ("access", ctypes.c_longlong),
            ("write", ctypes.c_longlong),
            ("change", ctypes.c_longlong),
            ("size", ctypes.c_longlong),
            ("allocation", ctypes.c_longlong),
            ("attributes", wintypes.DWORD),
            ("name_length", wintypes.DWORD),
            ("ea_size", wintypes.DWORD),
            ("reparse_tag", wintypes.DWORD),
            ("file_id", ctypes.c_ubyte * 16),
        ]

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE
    _get_info = _kernel.GetFileInformationByHandleEx
    _get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _get_info.restype = wintypes.BOOL
    _close_handle = _kernel.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL


def file_version(path: Path) -> FileVersion:
    """Include real change time, even when an external writer restores mtime.

    Python 3.13 reports creation time as Windows st_ctime. NTFS ChangeTime is
    therefore read explicitly. If attributes cannot be read, fail visibly
    instead of trusting a potentially stale entry.
    """
    stat = path.stat()
    changed_ns = stat.st_ctime_ns
    if os.name == "nt":
        handle = _create_file(str(path), 0x80, 0x7, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = _FileBasicInfo()
            if not _get_info(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            changed_ns = info.change * 100
        finally:
            _close_handle(handle)
    return FileVersion(stat.st_size, stat.st_mtime_ns, changed_ns, stat.st_dev, stat.st_ino)


def path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def directory_versions(
    directory: Path, should_cancel: Callable[[], bool]
) -> dict[str, FileVersion]:
    """Batch Windows change-time checks; unsupported providers use direct stat.

    Reparse points always use direct version checks against their target. All
    metadata is fetched for this search, never reused from a previous scan.
    """
    if os.name != "nt":
        return {}
    handle = _create_file(str(directory), 0x1, 0x7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        return {}
    try:
        device = directory.stat().st_dev
        buffer = ctypes.create_string_buffer(64 * 1024)
        versions: dict[str, FileVersion] = {}
        while True:
            if should_cancel():
                raise SearchCancelled()
            if not _get_info(handle, 19, buffer, len(buffer)):
                if ctypes.get_last_error() == 18:  # ERROR_NO_MORE_FILES
                    return versions
                return {}
            offset = 0
            while True:
                entry = _DirectoryInfo.from_buffer(buffer, offset)
                name_start = offset + ctypes.sizeof(_DirectoryInfo)
                if name_start + entry.name_length > len(buffer):
                    return {}
                name = ctypes.wstring_at(
                    ctypes.addressof(buffer) + name_start, entry.name_length // 2
                )
                if not entry.attributes & (0x10 | 0x400):
                    versions[name] = FileVersion(
                        entry.size,
                        (entry.write - 116444736000000000) * 100,
                        entry.change * 100,
                        device,
                        int.from_bytes(entry.file_id, "little"),
                    )
                if not entry.next_offset:
                    break
                offset += entry.next_offset
                if offset + ctypes.sizeof(_DirectoryInfo) > len(buffer):
                    return {}
    except OSError:
        return {}
    finally:
        _close_handle(handle)


@dataclass(frozen=True, slots=True)
class _Entry:
    version: FileVersion
    text: str
    cost: int
    racy: bool = False
    digest: bytes | None = None


class SearchIndex:
    """Thread-safe LRU of decoded text with a strict content-memory budget.

    Files exceeding the budget are still searched in full; they simply are not
    retained. A caller enumerates source paths on every search, so deletion,
    rename, new files, root changes and exclusions never depend on the cache.
    """

    def __init__(self, max_bytes: int = 64 * 1024 * 1024):
        self.max_bytes = max(0, int(max_bytes))
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()
        self.hits = 0
        self.reads = 0
        self.verifications = 0
        self.evictions = 0

    @property
    def retained_bytes(self) -> int:
        with self._lock:
            return self._bytes

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def invalidate(self, path: str | Path | None = None) -> None:
        """Forget a saved/external change, or rebuild the complete index."""
        with self._lock:
            if path is None:
                self._entries.clear()
                self._bytes = 0
            else:
                previous = self._entries.pop(path_key(path), None)
                if previous is not None:
                    self._bytes -= previous.cost

    def peek(self, path: str | Path, version: FileVersion) -> str | None:
        """Return cached text only for a freshly validated filesystem version.

        This never decodes source text. Only an entry whose timestamps are too
        recent to trust (racy) costs one hash of the file bytes, so call it
        from workers, not the GUI thread.
        """
        key = path_key(path)
        with self._lock:
            cached = self._entries.get(key)
            if cached is None or cached.version != version:
                return None
            if not cached.racy:
                self._entries.move_to_end(key)
                self.hits += 1
                return cached.text
            if cached.digest is None:
                return None
        # Racy entry: timestamps prove nothing, so compare content hashes.
        # Only files modified within the racy window reach this I/O.
        try:
            digest = file_digest(path)
        except OSError:
            return None
        with self._lock:
            self.verifications += 1
            if self._entries.get(key) is not cached or digest != cached.digest:
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return cached.text

    def read(
        self,
        path: Path,
        decoder: Callable[[Path], tuple[str, str, str] | None],
        should_cancel: Callable[[], bool],
        version: FileVersion | None = None,
    ) -> str | None:
        key = path_key(path)
        for _attempt in range(2):
            if should_cancel():
                raise SearchCancelled()
            try:
                before = version if version is not None and _attempt == 0 else file_version(path)
            except OSError:
                self.invalidate(path)
                # Some remote providers allow reading content but do not
                # expose reliable version metadata. Search without caching.
                decoded = decoder(path)
                with self._lock:
                    self.reads += 1
                if should_cancel():
                    raise SearchCancelled()
                return decoded[0] if decoded is not None else None
            with self._lock:
                cached = self._entries.get(key)
                if cached is not None and cached.version == before and not cached.racy:
                    self._entries.move_to_end(key)
                    self.hits += 1
                    return cached.text
            digest = None
            if is_racy(before):
                # Timestamps cannot prove this file unchanged. Hash the bytes
                # before decoding, so a later write can only make us re-read.
                try:
                    digest = file_digest(path)
                except OSError:
                    digest = None
                with self._lock:
                    self.verifications += 1
                    if (
                        digest is not None and cached is not None
                        and cached.version == before and cached.digest == digest
                    ):
                        if not is_racy(before):
                            cached = _Entry(cached.version, cached.text, cached.cost)
                            self._entries[key] = cached
                        self._entries.move_to_end(key)
                        self.hits += 1
                        return cached.text
            decoded = decoder(path)
            with self._lock:
                self.reads += 1
            if should_cancel():
                raise SearchCancelled()
            try:
                after = file_version(path)
            except OSError:
                self.invalidate(path)
                return decoded[0] if decoded is not None else None
            if before != after:
                self.invalidate(path)
                continue
            if decoded is None:
                self.invalidate(path)
                return None
            text = decoded[0]
            racy = is_racy(after)
            cost = sys.getsizeof(text) + sys.getsizeof(key) + sys.getsizeof(before) + 160
            with self._lock:
                previous = self._entries.pop(key, None)
                if previous is not None:
                    self._bytes -= previous.cost
                if cost <= self.max_bytes:
                    while self._entries and self._bytes + cost > self.max_bytes:
                        _old_key, old_entry = self._entries.popitem(last=False)
                        self._bytes -= old_entry.cost
                        self.evictions += 1
                    self._entries[key] = _Entry(after, text, cost, racy, digest)
                    self._bytes += cost
            return text
        raise OSError("搜尋期間文件持續變動，請重新搜尋")


default_index = SearchIndex()
