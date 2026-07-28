"""Crash-safe atomic file writes shared across the app.

A bare ``Path.write_bytes`` truncates the target before the new content lands,
so a crash, power loss, full disk, or cloud-sync conflict mid-write can leave a
user's note empty or half-written. Every document/sidecar write goes through
``atomic_write_bytes`` instead: it writes a temp file, fsyncs it, keeps one
``.bak`` of the previous content, then atomically renames into place.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path


# QFileSystemWatcher, antivirus scanners, and sync clients can briefly hold a
# just-written file on Windows.  os.replace() then raises PermissionError even
# though the same operation succeeds a few milliseconds later.  Keep the total
# pause below 200 ms and retry only that transient class of failure; disk/full,
# bad-path, and other OSErrors still reach the caller immediately.
_WINDOWS_REPLACE_RETRY_DELAYS = (0.01, 0.025, 0.05, 0.1)


def _replace_file(source: Path, target: Path) -> None:
    """Replace *target*, tolerating a short-lived Windows sharing lock."""
    if os.name != "nt":
        os.replace(source, target)
        return
    for delay in _WINDOWS_REPLACE_RETRY_DELAYS:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(source, target)


def set_hidden(path: str | Path) -> None:
    """Best-effort: set the Windows *hidden* attribute on *path*.

    Sidecar files (``.notes.json`` / ``.highlights.json`` / their ``.bak``)
    are bookkeeping the user never edits by hand, so we hide them from
    Explorer's default view. No-op on non-Windows; every failure is swallowed
    because hiding a sidecar is cosmetic and must never break a save.
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        kernel32 = ctypes.windll.kernel32
        get_attrs = kernel32.GetFileAttributesW
        get_attrs.restype = ctypes.c_uint32
        target = str(path)
        attrs = get_attrs(target)
        if attrs == INVALID_FILE_ATTRIBUTES:
            return
        if not attrs & FILE_ATTRIBUTE_HIDDEN:
            kernel32.SetFileAttributesW(target, attrs | FILE_ATTRIBUTE_HIDDEN)
    except (OSError, AttributeError, ValueError):
        pass


def atomic_write_bytes(
    path: str | Path, data: bytes, *, backup: bool = True, hidden: bool = False
) -> None:
    """Write *data* to *path* atomically (temp file + ``os.replace``).

    The existing file, if any, is first copied to ``<name>.bak`` so a single
    previous version is always recoverable. The temp file is flushed and
    fsync'd before the rename, so a crash mid-write can never truncate the
    target — the rename either fully happens or it does not.

    When *hidden* is set (sidecar writes on Windows), the temp file is hidden
    **before** the rename so replacing an already-hidden target does not trip
    Windows' "cannot replace a hidden file with a non-hidden one" access error;
    any ``.bak`` is hidden too. No-op off Windows.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if hidden:
        set_hidden(tmp)
    if backup and path.exists():
        try:
            bak = path.with_name(path.name + ".bak")
            shutil.copy2(path, bak)
            if hidden:
                set_hidden(bak)
        except OSError:
            pass
    _replace_file(tmp, path)


def atomic_write_text(
    path: str | Path,
    text: str,
    encoding: str = "utf-8",
    *,
    backup: bool = True,
    hidden: bool = False,
) -> None:
    atomic_write_bytes(path, text.encode(encoding), backup=backup, hidden=hidden)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
