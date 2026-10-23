"""Filesystem CRUD for the file tree (create / rename / move / delete).

Every operation that relocates a document also relocates its sidecar files
(`<name>.notes.json`, `<name>.highlights.json`) so annotations follow the
document. Callers receive an ``{old_path: new_path}`` mapping (plain path
strings, no resolution) they can use to migrate tabs, recents, and indexes.

All failures surface as ``OSError`` so UI callers can report them.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import warnings
from pathlib import Path

from .atomic_io import atomic_write_bytes
from .document_relocation import rebase_markdown_bytes
from .file_types import is_markdown
from .relocation_journal import RelocationJournal, fingerprint, matches_fingerprint

try:  # optional dependency; delete_document falls back to permanent delete
    from send2trash import send2trash as _send2trash
except ImportError:  # pragma: no cover - depends on environment
    _send2trash = None

HAS_SEND2TRASH = _send2trash is not None


def show_in_explorer(path: str | Path) -> None:
    """Select a path in Explorer; its success may use exit status 1."""
    subprocess.run(["explorer", "/select,", str(Path(path))], check=False)

INVALID_NAME_CHARS = '<>:"/\\|?*'

_SIDECAR_SUFFIXES = (".notes.json", ".highlights.json")
_RELOCATION_SUFFIXES = ("", ".bak", ".notes.json", ".notes.json.bak", ".highlights.json", ".highlights.json.bak")


def is_valid_name(name: str) -> bool:
    name = name.strip()
    if not name or name in (".", ".."):
        return False
    return not any(ch in name for ch in INVALID_NAME_CHARS)


def sidecar_paths(path: str | Path) -> list[Path]:
    p = Path(path)
    return [p.with_name(p.name + suffix) for suffix in _SIDECAR_SUFFIXES]


def unique_child_path(folder: str | Path, stem: str, suffix: str) -> Path:
    """First non-existing ``folder/stem{ n}suffix`` (numbered from 2)."""
    folder = Path(folder)
    candidate = folder / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem} {counter}{suffix}"
        counter += 1
    return candidate


def create_note(folder: str | Path, name: str) -> Path:
    """Create ``name.md`` inside *folder* (auto-numbered when taken)."""
    stem = name.strip()
    if stem.lower().endswith(".md"):
        stem = stem[:-3].strip()
    if not is_valid_name(stem):
        raise OSError(f"無效的檔名：{name}")
    path = unique_child_path(folder, stem, ".md")
    atomic_write_bytes(path, f"# {stem}\n".encode("utf-8"), backup=False)
    return path


def create_document(folder: str | Path, name: str, suffix: str = ".md") -> Path:
    """Create an empty ``name{suffix}`` inside *folder* (UTF-8, no overwrite).

    Unlike :func:`create_note` this never auto-numbers: an existing target is
    an error so callers (the 新增筆記 dialog) can keep the user's input.

    The preflight ``exists()`` check only gives a fast, friendly error for the
    common case; the actual write uses an exclusive create (``open(..., "xb")``)
    so a file that another process creates in the gap between the check and
    the write still wins -- we never silently overwrite it.
    """
    stem = name.strip()
    if stem.lower().endswith(suffix.lower()):
        stem = stem[: -len(suffix)].strip()
    if not is_valid_name(stem):
        raise OSError(f"無效的檔名：{name}")
    path = Path(folder) / f"{stem}{suffix}"
    if path.exists():
        raise OSError(f"已存在同名檔案：{path}")
    try:
        with path.open("xb") as handle:
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise OSError(f"已存在同名檔案：{path}") from exc
    return path


def create_folder(parent: str | Path, name: str) -> Path:
    name = name.strip()
    if not is_valid_name(name):
        raise OSError(f"無效的資料夾名稱：{name}")
    path = Path(parent) / name
    if path.exists():
        raise OSError(f"已存在同名項目：{path}")
    path.mkdir(parents=False)
    return path


def _signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _rename_no_replace(source: Path, target: Path) -> None:
    """Publish a staged file without clobbering a destination created meanwhile."""
    if os.name == "nt":
        # Windows rename fails if the destination exists (including a new file
        # created after preflight). Both paths are on the same filesystem.
        source.rename(target)
    else:
        # POSIX rename replaces destinations. link gives us exclusive creation.
        os.link(source, target)
        try:
            source.unlink()
        except OSError:
            target.unlink()
            raise


def _stage_relocation_file(source: Path, target: Path, data: bytes | None) -> None:
    with target.open("xb") as output:
        if data is None:
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, output, 1024 * 1024)
        else:
            output.write(data)
        output.flush()
        os.fsync(output.fileno())
    shutil.copystat(source, target)


def _cleanup_relocation_directory(
    directory: Path, owned_names: set[str], expected: dict[str, dict] | None = None,
    identity: list[int] | None = None,
) -> bool:
    """Delete only this transaction's empty files/directory, never recursively.

    Cleanup failure must not turn a committed move into a reported failed move:
    callers need its mapping to migrate open tabs. A warning names the retained
    originals, which are safer to leave behind than to force-delete.
    """
    try:
        if directory.resolve() != directory.absolute():
            raise OSError(f"暫存資料夾包含連結或接合點，已保留：{directory}")
        stat = directory.stat()
        if identity is not None and [stat.st_dev, stat.st_ino] != identity:
            raise OSError(f"暫存資料夾已被替換，已保留：{directory}")
        for name in owned_names:
            child = directory / name
            if child.is_symlink():
                raise OSError(f"暫存檔案已被替換為連結，已保留：{child}")
            if child.exists():
                if not expected or name not in expected:
                    raise OSError(f"暫存檔案無法確認屬於本交易，已保留：{child}")
                if not matches_fingerprint(child, expected[name]):
                    raise OSError(f"暫存檔案已被外部修改，已保留：{child}")
            child.unlink(missing_ok=True)
        directory.rmdir()
        return True
    except OSError as exc:
        warnings.warn(f"文件搬移暫存清理失敗，保留資料位於 {directory}：{exc}", RuntimeWarning, stacklevel=2)
        return False


def rename_document(old: str | Path, new: str | Path, *, backlink_updates=None) -> dict[str, str]:
    """Relocate a document, existing backups and sidecars as one transaction.

    Relative Markdown resources (also in its .bak) retain their targets. Every
    destination is checked before mutation; prepared copies are fully written
    before originals leave their paths. On failure originals are restored. If
    the OS prevents rollback, the error names the retained recovery directory.
    """
    old, new = Path(old), Path(new)
    if str(old) == str(new):
        return {}
    if not old.is_file() or old.is_symlink():
        raise OSError(f"來源不是可安全搬移的文件：{old}")
    if not new.parent.is_dir():
        raise OSError(f"目的資料夾不存在：{new.parent}")
    entries = []
    for suffix in _RELOCATION_SUFFIXES:
        source = old.with_name(old.name + suffix)
        target = new.with_name(new.name + suffix)
        # A destination's orphan sidecar must not become attached to this note,
        # even when the source has no corresponding sidecar.
        if os.path.lexists(target):
            raise OSError(f"目的文件或註記／備份已存在：{target}")
        if not os.path.lexists(source):
            continue
        if not source.is_file() or source.is_symlink():
            raise OSError(f"來源註記／備份不是一般檔案：{source}")
        signature = _signature(source)
        source_fingerprint = fingerprint(source)
        data = None
        if is_markdown(old) and suffix in {"", ".bak"}:
            original = source.read_bytes()
            rebased = rebase_markdown_bytes(original, old, new)
            if rebased != original:
                data = rebased
        if _signature(source) != signature:
            raise OSError(f"文件在準備搬移時已變更，請重試：{source}")
        entries.append({"source": source, "target": target, "signature": signature,
                        "fingerprint": source_fingerprint, "data": data})

    for path, (before, after) in (backlink_updates or {}).items():
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            raise OSError(f"反向連結來源不是一般檔案：{path}")
        if path.stat().st_dev != old.stat().st_dev:
            raise OSError(f"目前無法將跨磁碟反向連結納入同一交易，尚未改名：{path}")
        signature = _signature(path)
        source_fingerprint = fingerprint(path)
        if path.read_bytes() != before or _signature(path) != signature:
            raise OSError(f"確認期間文件已變更，尚未改名：{path}")
        if path.resolve() == old.resolve():
            entries[0]["data"] = rebase_markdown_bytes(after, old, new)
            continue
        entries.append({"source": path, "target": path, "signature": signature,
                        "fingerprint": source_fingerprint,
                        "data": after, "inplace": True})

    prepared_dir = Path(tempfile.mkdtemp(prefix=".markdown-relocate-prepared-", dir=new.parent))
    originals_dir = None
    journal = None
    rollback_failed = False
    cleanup_safe = False
    owned_names = {str(index) for index in range(len(entries))}
    try:
        originals_dir = Path(tempfile.mkdtemp(prefix=".markdown-relocate-originals-", dir=old.parent))
        journal = RelocationJournal(entries, prepared_dir, originals_dir, old, new)
        for index, entry in enumerate(entries):
            entry["prepared"] = prepared_dir / str(index)
            entry["original"] = originals_dir / str(index)
            if entry.get("inplace"):
                atomic_write_bytes(entry["prepared"], entry["data"], backup=False)
                shutil.copystat(entry["source"], entry["prepared"])
            else:
                _stage_relocation_file(entry["source"], entry["prepared"], entry["data"])
            journal.record_prepared(index)
            entry["prepared_fingerprint"] = journal.payload["entries"][index]["prepared_fingerprint"]
        journal.prepared()
        for index, entry in enumerate(entries):
            entry["prepared_fingerprint"] = journal.payload["entries"][index]["prepared_fingerprint"]
            expected_hash = (hashlib.sha256(entry["data"]).hexdigest()
                             if entry["data"] is not None else entry["fingerprint"]["sha256"])
            if entry["prepared_fingerprint"]["sha256"] != expected_hash:
                raise OSError(f"準備的文件內容與來源不一致，尚未搬移：{entry['source']}")
            if (_signature(entry["source"]) != entry["signature"]
                    or not matches_fingerprint(entry["source"], entry["fingerprint"])):
                raise OSError(f"文件在準備搬移時已變更，請重試：{entry['source']}")
            if not entry.get("inplace") and os.path.lexists(entry["target"]):
                raise OSError(f"目的檔案在準備搬移時已出現：{entry['target']}")
        for entry in entries:
            if (_signature(entry["source"]) != entry["signature"]
                    or not matches_fingerprint(entry["source"], entry["fingerprint"])):
                raise OSError(f"文件在提交搬移時已變更：{entry['source']}")
            _rename_no_replace(entry["source"], entry["original"])
            entry["held"] = True
            _rename_no_replace(entry["prepared"], entry["target"])
            entry["published"] = True
            entry["published_signature"] = _signature(entry["target"])
        journal.committed()
        cleanup_safe = True
    except OSError as exc:
        errors = []
        for entry in reversed(entries):
            if not entry.get("held"):
                continue
            if not entry.get("inplace"):
                try:
                    _rename_no_replace(entry["original"], entry["source"])
                except OSError as rollback_error:
                    errors.append(f"{entry['source']}：{rollback_error}")
            if entry.get("published"):
                try:
                    if not entry.get("published_signature"):
                        raise OSError("已發布的目的文件無法確認狀態，已保留")
                    if (_signature(entry["target"]) != entry["published_signature"]
                            or not matches_fingerprint(
                                entry["target"], entry["prepared_fingerprint"]
                            )):
                        raise OSError("目的文件已被其他程式修改，已保留")
                    entry["target"].unlink()
                except OSError as rollback_error:
                    errors.append(f"{entry['target']}：{rollback_error}")
            if entry.get("inplace"):
                try:
                    _rename_no_replace(entry["original"], entry["source"])
                except OSError as rollback_error:
                    errors.append(f"{entry['source']}：{rollback_error}")
        if errors:
            rollback_failed = True
            raise OSError(
                f"搬移失敗且部分原始資料無法自動回復：{exc}\n"
                f"請保留並檢查備份資料夾：{originals_dir}\n" + "\n".join(errors)
            ) from exc
        cleanup_safe = True
        raise OSError(f"搬移失敗，原始文件與註記已保留：{exc}") from exc
    finally:
        try:
            if cleanup_safe:
                prepared_fingerprints = {str(index): entry["prepared_fingerprint"]
                                         for index, entry in enumerate(entries) if "prepared_fingerprint" in entry}
                original_fingerprints = {str(index): entry["fingerprint"] for index, entry in enumerate(entries)}
                cleaned = _cleanup_relocation_directory(
                    prepared_dir, owned_names, prepared_fingerprints,
                    journal.payload["prepared_identity"] if journal is not None else None,
                )
                if originals_dir is not None and not rollback_failed:
                    cleaned = _cleanup_relocation_directory(
                        originals_dir, owned_names, original_fingerprints,
                        journal.payload["originals_identity"] if journal is not None else None,
                    ) and cleaned
                if journal is not None and cleaned and not rollback_failed:
                    try:
                        journal.discard()
                    except OSError as exc:
                        warnings.warn(f"搬移復原紀錄尚未清理，將於下次啟動重試：{exc}", RuntimeWarning, stacklevel=2)
        finally:
            if journal is not None:
                journal.close()
    return {str(old): str(new)}


def move_document(path: str | Path, dest_folder: str | Path) -> dict[str, str]:
    path = Path(path)
    dest = Path(dest_folder) / path.name
    if str(dest) == str(path):
        return {}
    return rename_document(path, dest)


def rename_folder(old: str | Path, new_name: str, *, cancel=None) -> dict[str, str]:
    """Rename directory *old* to *new_name*; map every file old -> new."""
    old = Path(old)
    new_name = new_name.strip()
    if not is_valid_name(new_name):
        raise OSError(f"無效的資料夾名稱：{new_name}")
    new = old.with_name(new_name)
    if str(new) == str(old):
        return {}
    if new.exists():
        raise OSError(f"已存在同名項目：{new}")
    identity = old.stat()
    directory_signatures = {old: (identity.st_ino, identity.st_mtime_ns)}
    directory_entries = {}
    mapping: dict[str, str] = {}
    def check_cancel():
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Folder rename cancelled before commit")

    def scan_error(error):
        raise error

    for dirpath, dirnames, filenames in os.walk(old, onerror=scan_error):
        check_cancel()
        directory_entries[Path(dirpath)] = set(dirnames) | set(filenames)
        for dirname in dirnames:
            directory = Path(dirpath) / dirname
            metadata = directory.stat()
            directory_signatures[directory] = (metadata.st_ino, metadata.st_mtime_ns)
        for filename in filenames:
            check_cancel()
            old_file = Path(dirpath) / filename
            new_file = new / old_file.relative_to(old)
            mapping[str(old_file)] = str(new_file)
    check_cancel()
    current = old.stat()
    if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
        raise OSError("Folder changed during rename preparation")
    for directory, signature in directory_signatures.items():
        metadata = directory.stat()
        if (metadata.st_ino, metadata.st_mtime_ns) != signature:
            raise OSError("Folder contents changed during rename preparation")
    # Windows may defer directory timestamp updates. Compare names as well.
    for directory, names in directory_entries.items():
        check_cancel()
        with os.scandir(directory) as entries:
            if {entry.name for entry in entries} != names:
                raise OSError("Folder contents changed during rename preparation")
    if new.exists():
        raise FileExistsError(str(new))
    check_cancel()
    old.rename(new)
    return mapping


def delete_document(path: str | Path, use_trash: bool = True) -> bool:
    """Delete a document and its sidecars. True when sent to the trash."""
    path = Path(path)
    targets = [path] + [p for p in sidecar_paths(path) if p.exists()]
    trashed = bool(use_trash and _send2trash is not None)
    for target in targets:
        if trashed:
            _send2trash(str(target))
        else:
            target.unlink()
    return trashed
