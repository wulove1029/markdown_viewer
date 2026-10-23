"""Durable, scope-checked recovery for multi-file document relocations.

Only the original transaction writes a manifest. Startup recovery receives
trusted library roots from the caller, validates every path and file identity,
and never overwrites externally changed content. Temporary directories are
cleaned by exact recorded filenames, without recursive deletion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from PySide6.QtCore import QStandardPaths

from .atomic_io import atomic_write_bytes
from .settings_store import legacy_data_path

_VERSION = 1
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_ENTRIES = 10000
_SIDECARS = (
    "",
    ".bak",
    ".notes.json",
    ".notes.json.bak",
    ".highlights.json",
    ".highlights.json.bak",
)


def default_journal_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return legacy_data_path(base) / "markdown-viewer" / "relocations"


def _identity(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def fingerprint(path: Path) -> dict:
    """Capture a stable identity and digest, rejecting special files and links."""
    if path.is_symlink() or not path.is_file():
        raise OSError(f"不是可安全驗證的一般檔案：{path}")
    before = path.stat()
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    first = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    last = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if first != last:
        raise OSError(f"驗證期間檔案已變更：{path}")
    return {
        "device": after.st_dev,
        "inode": after.st_ino,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "sha256": digest,
    }


def matches_fingerprint(path: Path, expected: dict | None) -> bool:
    if not expected:
        return False
    try:
        return fingerprint(path) == expected
    except OSError:
        return False


class _JournalLock:
    """Use an OS-owned lock that is released even on forced process exit."""

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self._handle = (directory / ".recovery.lock").open("a+b")
        self._handle.seek(0, os.SEEK_END)
        if not self._handle.tell():
            self._handle.write(b"0")
            self._handle.flush()
        self._handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._handle.close()
            raise OSError("另一個文件搬移或復原工作正在執行，請稍後重試") from None

    def close(self):
        self._handle.close()


def _write_manifest(path: Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    if len(data) > _MAX_MANIFEST_BYTES:
        raise OSError("搬移復原紀錄過大，尚未提交搬移")
    atomic_write_bytes(path, data, backup=False)


class RelocationJournal:
    """Hold a transaction lock and persist enough evidence to replay a crash."""

    def __init__(
        self,
        entries,
        prepared_dir: Path,
        originals_dir: Path,
        old: Path,
        new: Path,
        *,
        journal_dir: Path | None = None,
    ):
        if not entries or len(entries) > _MAX_ENTRIES:
            raise OSError("搬移項目數超出安全範圍，尚未提交搬移")
        self.directory = Path(journal_dir) if journal_dir is not None else default_journal_dir()
        self._lock = _JournalLock(self.directory)
        self.path = self.directory / f"{uuid4().hex}.json"
        try:
            self.payload = {
                "version": _VERSION,
                "id": self.path.stem,
                "phase": "preparing",
                "source": str(old.absolute()),
                "target": str(new.absolute()),
                "prepared_dir": str(prepared_dir.absolute()),
                "originals_dir": str(originals_dir.absolute()),
                "prepared_identity": _identity(prepared_dir),
                "originals_identity": _identity(originals_dir),
                "entries": [
                    {
                        "source": str(entry["source"].absolute()),
                        "target": str(entry["target"].absolute()),
                        "inplace": bool(entry.get("inplace")),
                        "original_fingerprint": entry.get("fingerprint")
                        or fingerprint(entry["source"]),
                        "prepared_fingerprint": None,
                    }
                    for entry in entries
                ],
            }
            _write_manifest(self.path, self.payload)
        except BaseException:
            self._lock.close()
            raise

    def prepared(self) -> None:
        directory = Path(self.payload["prepared_dir"])
        for index, entry in enumerate(self.payload["entries"]):
            entry["prepared_fingerprint"] = fingerprint(directory / str(index))
        self.payload["phase"] = "prepared"
        _write_manifest(self.path, self.payload)

    def record_prepared(self, index: int) -> None:
        path = Path(self.payload["prepared_dir"]) / str(index)
        self.payload["entries"][index]["prepared_fingerprint"] = fingerprint(path)
        _write_manifest(self.path, self.payload)

    def committed(self) -> None:
        self.payload["phase"] = "committed"
        _write_manifest(self.path, self.payload)

    def discard(self) -> None:
        self.path.unlink(missing_ok=True)

    def close(self) -> None:
        self._lock.close()


@dataclass(frozen=True)
class RelocationRecoveryResult:
    manifest: Path
    status: str
    source: str = ""
    target: str = ""
    issues: tuple[str, ...] = ()


def _safe_path(value, roots: tuple[Path, ...]) -> Path:
    if not isinstance(value, str):
        raise ValueError("復原紀錄包含無效路徑")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("復原紀錄包含非絕對路徑或上層跳轉")
    resolved = path.resolve(strict=False)
    if path != resolved:
        raise ValueError(f"復原路徑含連結或接合點，已保留：{path}")
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError(f"復原路徑不在已授權文件庫範圍：{path}")
    return path


def _valid_fingerprint(value) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"device", "inode", "size", "mtime_ns", "sha256"}
        and all(
            type(value.get(key)) is int and value[key] >= 0
            for key in ("device", "inode", "size", "mtime_ns")
        )
        and isinstance(value.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
    )


def _validate_manifest(path: Path, payload, roots: tuple[Path, ...]) -> dict:
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload["version"] != _VERSION
    ):
        raise ValueError("不支援的搬移復原紀錄版本")
    if not re.fullmatch(r"[0-9a-f]{32}", path.stem) or payload.get("id") != path.stem:
        raise ValueError("搬移復原紀錄識別碼不一致")
    if payload.get("phase") not in {"preparing", "prepared", "committed"}:
        raise ValueError("搬移復原紀錄狀態無效")
    old = _safe_path(payload.get("source"), roots)
    new = _safe_path(payload.get("target"), roots)
    if old == new:
        raise ValueError("搬移復原的來源與目的不得相同")
    prepared = _safe_path(payload.get("prepared_dir"), roots)
    originals = _safe_path(payload.get("originals_dir"), roots)
    if (
        prepared.parent != new.parent
        or originals.parent != old.parent
        or not re.fullmatch(r"\.markdown-relocate-prepared-[a-zA-Z0-9_-]+", prepared.name)
        or not re.fullmatch(r"\.markdown-relocate-originals-[a-zA-Z0-9_-]+", originals.name)
    ):
        raise ValueError("搬移暫存資料夾不符合交易範圍")
    for directory, key in ((prepared, "prepared_identity"), (originals, "originals_identity")):
        expected = payload.get(key)
        if (
            not isinstance(expected, list)
            or len(expected) != 2
            or any(type(value) is not int for value in expected)
        ):
            raise ValueError("搬移暫存資料夾識別資料無效")
        if os.path.lexists(directory) and (
            not directory.is_dir() or _identity(directory) != expected
        ):
            raise ValueError(f"搬移暫存資料夾已被替換：{directory}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not 1 <= len(entries) <= _MAX_ENTRIES:
        raise ValueError("搬移復原項目數無效")
    seen_sources, seen_targets = set(), set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or type(entry.get("inplace")) is not bool:
            raise ValueError("搬移復原項目格式無效")
        source = _safe_path(entry.get("source"), roots)
        target = _safe_path(entry.get("target"), roots)
        if source in seen_sources or target in seen_targets:
            raise ValueError("搬移復原項目含重複路徑")
        seen_sources.add(source)
        seen_targets.add(target)
        if entry["inplace"]:
            if source != target or source == old:
                raise ValueError("反向連結復原路徑不一致")
        elif not any(
            source == old.with_name(old.name + suffix)
            and target == new.with_name(new.name + suffix)
            for suffix in _SIDECARS
        ):
            raise ValueError("搬移復原項目不屬於原文件或註記／備份")
        if index == 0 and (source != old or target != new or entry["inplace"]):
            raise ValueError("搬移復原主文件不一致")
        if not _valid_fingerprint(entry.get("original_fingerprint")):
            raise ValueError("原文件驗證資料無效")
        staged = entry.get("prepared_fingerprint")
        if staged is not None and not _valid_fingerprint(staged):
            raise ValueError("準備文件驗證資料無效")
        if payload["phase"] != "preparing" and staged is None:
            raise ValueError("已準備交易缺少文件驗證資料")
    return payload


def _exclusive_restore(source: Path, target: Path) -> None:
    if os.name == "nt":
        source.rename(target)
    else:
        os.link(source, target)
        source.unlink()


def _remove_owned_file(path: Path, expected: dict) -> None:
    if not matches_fingerprint(path, expected):
        raise OSError(f"檔案已被外部修改或無法驗證，已保留：{path}")
    path.unlink()


def _clean_directory(directory: Path, entries: list[dict], field: str) -> list[str]:
    errors = []
    if not directory.exists():
        return errors
    for index, entry in enumerate(entries):
        path = directory / str(index)
        if os.path.lexists(path):
            try:
                _remove_owned_file(path, entry[field])
            except OSError as exc:
                errors.append(str(exc))
    try:
        directory.rmdir()
    except OSError as exc:
        errors.append(f"搬移暫存仍有未確認內容，已保留 {directory}：{exc}")
    return errors


def _recover_one(path: Path, payload: dict) -> RelocationRecoveryResult:
    entries = payload["entries"]
    originals = Path(payload["originals_dir"])
    prepared = Path(payload["prepared_dir"])
    errors = []
    committed = payload["phase"] == "committed"
    if committed:
        for entry in entries:
            if not matches_fingerprint(Path(entry["target"]), entry["prepared_fingerprint"]):
                errors.append(
                    f"搬移已提交，但目的文件已變更或遺失；原始備份已保留：{entry['target']}"
                )
    elif payload["phase"] == "prepared":
        for index in range(len(entries) - 1, -1, -1):
            entry = entries[index]
            source, target = Path(entry["source"]), Path(entry["target"])
            original = originals / str(index)
            try:
                if os.path.lexists(original):
                    if not matches_fingerprint(original, entry["original_fingerprint"]):
                        raise OSError(f"保留的原始文件已變更，請人工檢查：{original}")
                    if (
                        entry["inplace"]
                        and os.path.lexists(source)
                        and not matches_fingerprint(source, entry["original_fingerprint"])
                    ):
                        _remove_owned_file(source, entry["prepared_fingerprint"])
                    if not os.path.lexists(source):
                        _exclusive_restore(original, source)
                    elif not matches_fingerprint(source, entry["original_fingerprint"]):
                        raise OSError(
                            f"原路徑已有其他文件，未覆寫；原始備份位於 {original}：{source}"
                        )
                elif not os.path.lexists(source):
                    raise OSError(f"原文件與原始備份皆無法找到，目的文件已保留：{source}")
                if not entry["inplace"] and os.path.lexists(target):
                    _remove_owned_file(target, entry["prepared_fingerprint"])
            except OSError as exc:
                errors.append(str(exc))
    # A preparing manifest is written before any originals can move. Files
    # lacking a completed staging fingerprint are retained for manual review.
    if not errors:
        errors.extend(_clean_directory(prepared, entries, "prepared_fingerprint"))
        errors.extend(_clean_directory(originals, entries, "original_fingerprint"))
    if not errors:
        path.unlink()
    return RelocationRecoveryResult(
        path,
        "needs_attention" if errors else ("committed" if committed else "rolled_back"),
        payload["source"],
        payload["target"],
        tuple(errors),
    )


def recover_pending_relocations(
    allowed_roots: Iterable[str | Path], *, journal_dir: str | Path | None = None
) -> tuple[RelocationRecoveryResult, ...]:
    """Recover abandoned moves only within caller-authorized filesystem roots.

    The caller should run this before restoring sessions, report all
    needs_attention results, and migrate saved paths for committed results.
    Recovery is idempotent, and an active transaction is never replayed.
    """
    directory = Path(journal_dir) if journal_dir is not None else default_journal_dir()
    if not directory.exists():
        return ()
    roots = tuple(Path(root).expanduser().resolve(strict=False) for root in allowed_roots)
    try:
        lock = _JournalLock(directory)
    except OSError as exc:
        return (RelocationRecoveryResult(directory, "active", issues=(str(exc),)),)
    results = []
    try:
        for path in sorted(directory.glob("*.json")):
            try:
                if path.is_symlink() or path.stat().st_size > _MAX_MANIFEST_BYTES:
                    raise ValueError("搬移復原紀錄不是可安全讀取的檔案")
                payload = json.loads(path.read_text(encoding="utf-8"))
                _validate_manifest(path, payload, roots)
                results.append(_recover_one(path, payload))
            except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
                results.append(
                    RelocationRecoveryResult(path, "needs_attention", issues=(str(exc),))
                )
    finally:
        lock.close()
    return tuple(results)
