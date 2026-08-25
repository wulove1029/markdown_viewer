"""Crash-recovery snapshots for unsaved text-editor buffers.

Snapshots live under the application's data directory, never beside or in
place of the source document.  The UI integration owns debounce timing; this
module deliberately exposes small synchronous operations so they are easy to
call from a ``QTimer`` and easy to test without a running window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QStandardPaths

from .atomic_io import atomic_write_text, sha256_hex


SCHEMA_VERSION = 1
_RECOVERY_FOLDER = "recovery"
_VALID_NEWLINES = {"\n", "\r\n", "\r"}
_CAPTURE_CURRENT_SIGNATURE = object()


def _default_recovery_dir() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base or ".") / "markdown-viewer" / _RECOVERY_FOLDER


def _absolute_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def _path_identity(path: str | Path) -> str:
    """Return the same identity rules the host filesystem uses for a path."""
    return os.path.normcase(str(_absolute_path(path)))


def _snapshot_name(source_path: str | Path) -> str:
    identity = _path_identity(source_path).encode("utf-8")
    return f"{sha256_hex(identity)}.json"


def _source_signature(source_path: str | Path) -> tuple[int, int] | None:
    try:
        stat = Path(source_path).stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _normalize_signature(
    signature: tuple[int, int] | list[int] | None,
) -> tuple[int, int] | None:
    if signature is None:
        return None
    if not isinstance(signature, (tuple, list)) or len(signature) != 2:
        raise ValueError("source_signature must be an (mtime_ns, size) pair")
    mtime_ns, size = signature
    if (
        isinstance(mtime_ns, bool)
        or isinstance(size, bool)
        or not isinstance(mtime_ns, int)
        or not isinstance(size, int)
        or mtime_ns < 0
        or size < 0
    ):
        raise ValueError("source_signature values must be non-negative integers")
    return mtime_ns, size


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


@dataclass(frozen=True)
class RecoverySnapshot:
    """One recoverable editor buffer and the source revision it was based on.

    ``cursor`` and ``anchor`` intentionally retain the integration layer's
    coordinate system (Qt UTF-16 positions in the current editor).  They are
    not clamped to Python's code-point length, which would corrupt positions
    after emoji or other supplementary Unicode characters.
    """

    source_path: str
    draft: str
    source_signature: str | None
    source_mtime_ns: int | None
    source_size: int | None
    encoding: str
    newline: str
    cursor: int
    anchor: int
    scroll: int
    updated_at: str

    @property
    def signature_pair(self) -> tuple[int, int] | None:
        if self.source_mtime_ns is None or self.source_size is None:
            return None
        return self.source_mtime_ns, self.source_size

    def to_dict(self) -> dict:
        return {"schema": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, raw: object) -> "RecoverySnapshot | None":
        """Validate untrusted JSON, returning ``None`` for corrupt snapshots."""
        if not isinstance(raw, dict) or raw.get("schema") != SCHEMA_VERSION:
            return None

        source_path = raw.get("source_path")
        draft = raw.get("draft")
        encoding = raw.get("encoding")
        newline = raw.get("newline")
        updated_at = raw.get("updated_at")
        if not isinstance(source_path, str) or not source_path.strip():
            return None
        if not isinstance(draft, str):
            return None
        if not isinstance(encoding, str) or not encoding.strip():
            return None
        if newline not in _VALID_NEWLINES or not _valid_timestamp(updated_at):
            return None

        cursor = _nonnegative_int(raw.get("cursor"))
        anchor = _nonnegative_int(raw.get("anchor"))
        scroll = _nonnegative_int(raw.get("scroll"))
        if cursor is None or anchor is None or scroll is None:
            return None

        signature = raw.get("source_signature")
        mtime_ns = raw.get("source_mtime_ns")
        size = raw.get("source_size")
        if signature is None and mtime_ns is None and size is None:
            normalized_signature = None
        else:
            mtime_ns = _nonnegative_int(mtime_ns)
            size = _nonnegative_int(size)
            if mtime_ns is None or size is None:
                return None
            normalized_signature = f"{mtime_ns}:{size}"
            if signature != normalized_signature:
                return None

        return cls(
            source_path=source_path,
            draft=draft,
            source_signature=normalized_signature,
            source_mtime_ns=mtime_ns,
            source_size=size,
            encoding=encoding,
            newline=newline,
            cursor=cursor,
            anchor=anchor,
            scroll=scroll,
            updated_at=updated_at,
        )


class RecoveryStore:
    """Synchronous persistence API for recovery snapshots."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory else _default_recovery_dir()

    def snapshot_path(self, source_path: str | Path) -> Path:
        return self.directory / _snapshot_name(source_path)

    def save(
        self,
        source_path: str | Path,
        draft: str,
        *,
        encoding: str,
        newline: str,
        cursor: int,
        anchor: int,
        scroll: int,
        source_signature: tuple[int, int] | list[int] | None | object = (
            _CAPTURE_CURRENT_SIGNATURE
        ),
        updated_at: str | None = None,
    ) -> RecoverySnapshot:
        """Atomically save a recovery snapshot without writing the source file.

        When ``source_signature`` is omitted, the current source ``mtime_ns``
        and size are captured. An explicit ``None`` records that the source did
        not exist when editing began; this distinction prevents a later file
        created at the same path from being overwritten without a conflict.
        """
        if not isinstance(draft, str):
            raise TypeError("draft must be text")
        if not isinstance(encoding, str) or not encoding.strip():
            raise ValueError("encoding must be a non-empty string")
        if newline not in _VALID_NEWLINES:
            raise ValueError("newline must be \\n, \\r\\n, or \\r")
        positions = (cursor, anchor, scroll)
        if any(_nonnegative_int(value) is None for value in positions):
            raise ValueError("cursor, anchor, and scroll must be non-negative integers")

        source = _absolute_path(source_path)
        if source_signature is _CAPTURE_CURRENT_SIGNATURE:
            signature_pair = _source_signature(source)
        else:
            signature_pair = _normalize_signature(source_signature)
        mtime_ns, size = signature_pair if signature_pair is not None else (None, None)
        timestamp = updated_at or _utc_now()
        if not _valid_timestamp(timestamp):
            raise ValueError("updated_at must be an ISO-8601 timestamp")

        snapshot = RecoverySnapshot(
            source_path=str(source),
            draft=draft,
            source_signature=(
                f"{mtime_ns}:{size}" if signature_pair is not None else None
            ),
            source_mtime_ns=mtime_ns,
            source_size=size,
            encoding=encoding,
            newline=newline,
            cursor=cursor,
            anchor=anchor,
            scroll=scroll,
            updated_at=timestamp,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.snapshot_path(source),
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            backup=False,
        )
        return snapshot

    @staticmethod
    def _read(path: Path) -> RecoverySnapshot | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            return None
        return RecoverySnapshot.from_dict(raw)

    def load(self, source_path: str | Path) -> RecoverySnapshot | None:
        snapshot = self._read(self.snapshot_path(source_path))
        if snapshot is None:
            return None
        if _path_identity(snapshot.source_path) != _path_identity(source_path):
            return None
        return snapshot

    def list(self) -> list[RecoverySnapshot]:
        """Return valid snapshots newest-first, silently skipping corruption."""
        try:
            paths: Iterable[Path] = self.directory.glob("*.json")
            snapshots = [snapshot for path in paths if (snapshot := self._read(path))]
        except OSError:
            return []
        return sorted(snapshots, key=lambda item: item.updated_at, reverse=True)

    def discard(self, source_path: str | Path) -> bool:
        """Remove one recovery snapshot and any interrupted-write remnants."""
        path = self.snapshot_path(source_path)
        removed = False
        for target in (
            path,
            path.with_name(path.name + ".tmp"),
            path.with_name(path.name + ".bak"),
        ):
            try:
                target.unlink()
                removed = True
            except FileNotFoundError:
                continue
            except OSError:
                continue
        return removed

    def clear_after_save(self, source_path: str | Path) -> bool:
        """Clear recovery data after the integration confirms a source save."""
        return self.discard(source_path)


def save(source_path: str | Path, draft: str, **state) -> RecoverySnapshot:
    """Save through the default AppData recovery store."""
    return RecoveryStore().save(source_path, draft, **state)


def load(source_path: str | Path) -> RecoverySnapshot | None:
    """Load one snapshot from the default AppData recovery store."""
    return RecoveryStore().load(source_path)


def list_snapshots() -> list[RecoverySnapshot]:
    """List snapshots from the default AppData recovery store."""
    return RecoveryStore().list()


def discard(source_path: str | Path) -> bool:
    """Discard one snapshot from the default AppData recovery store."""
    return RecoveryStore().discard(source_path)


def clear_after_save(source_path: str | Path) -> bool:
    """Clear one snapshot after a successful source-document save."""
    return RecoveryStore().clear_after_save(source_path)
