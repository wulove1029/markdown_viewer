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
from pathlib import Path


def atomic_write_bytes(path: str | Path, data: bytes, *, backup: bool = True) -> None:
    """Write *data* to *path* atomically (temp file + ``os.replace``).

    The existing file, if any, is first copied to ``<name>.bak`` so a single
    previous version is always recoverable. The temp file is flushed and
    fsync'd before the rename, so a crash mid-write can never truncate the
    target — the rename either fully happens or it does not.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if backup and path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    os.replace(tmp, path)


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8", *, backup: bool = True) -> None:
    atomic_write_bytes(path, text.encode(encoding), backup=backup)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
