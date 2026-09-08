"""Filesystem CRUD for the file tree (create / rename / move / delete).

Every operation that relocates a document also relocates its sidecar files
(`<name>.notes.json`, `<name>.highlights.json`) so annotations follow the
document. Callers receive an ``{old_path: new_path}`` mapping (plain path
strings, no resolution) they can use to migrate tabs, recents, and indexes.

All failures surface as ``OSError`` so UI callers can report them.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import warnings

from .atomic_io import atomic_write_bytes
from .document_relocation import rebase_markdown_bytes
from .file_types import is_markdown

try:  # optional dependency; delete_document falls back to permanent delete
    from send2trash import send2trash as _send2trash
except ImportError:  # pragma: no cover - depends on environment
    _send2trash = None

HAS_SEND2TRASH = _send2trash is not None

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
    """
    stem = name.strip()
    if stem.lower().endswith(suffix.lower()):
        stem = stem[: -len(suffix)].strip()
    if not is_valid_name(stem):
        raise OSError(f"無效的檔名：{name}")
    path = Path(folder) / f"{stem}{suffix}"
    if path.exists():
        raise OSError(f"已存在同名檔案：{path}")
    atomic_write_bytes(path, b"", backup=False)
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


def _cleanup_relocation_directory(directory: Path) -> None:
    """Delete only this transaction's empty files/directory, never recursively.

    Cleanup failure must not turn a committed move into a reported failed move:
    callers need its mapping to migrate open tabs. A warning names the retained
    originals, which are safer to leave behind than to force-delete.
    """
    try:
        for child in directory.iterdir():
            child.unlink()
        directory.rmdir()
    except OSError as exc:
        warnings.warn(f"文件搬移暫存清理失敗，保留資料位於 {directory}：{exc}", RuntimeWarning, stacklevel=2)


def rename_document(old: str | Path, new: str | Path) -> dict[str, str]:
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
        data = None
        if is_markdown(old) and suffix in {"", ".bak"}:
            original = source.read_bytes()
            rebased = rebase_markdown_bytes(original, old, new)
            if rebased != original:
                data = rebased
        if _signature(source) != signature:
            raise OSError(f"文件在準備搬移時已變更，請重試：{source}")
        entries.append({"source": source, "target": target, "signature": signature, "data": data})

    prepared_dir = Path(tempfile.mkdtemp(prefix=".markdown-relocate-prepared-", dir=new.parent))
    originals_dir = None
    rollback_failed = False
    try:
        originals_dir = Path(tempfile.mkdtemp(prefix=".markdown-relocate-originals-", dir=old.parent))
        for index, entry in enumerate(entries):
            entry["prepared"] = prepared_dir / str(index)
            entry["original"] = originals_dir / str(index)
            _stage_relocation_file(entry["source"], entry["prepared"], entry["data"])
        for entry in entries:
            if _signature(entry["source"]) != entry["signature"]:
                raise OSError(f"文件在準備搬移時已變更，請重試：{entry['source']}")
            if os.path.lexists(entry["target"]):
                raise OSError(f"目的檔案在準備搬移時已出現：{entry['target']}")
        for entry in entries:
            _rename_no_replace(entry["source"], entry["original"])
            entry["held"] = True
            _rename_no_replace(entry["prepared"], entry["target"])
            entry["published"] = True
            entry["published_signature"] = _signature(entry["target"])
    except OSError as exc:
        errors = []
        for entry in reversed(entries):
            if not entry.get("held"):
                continue
            try:
                _rename_no_replace(entry["original"], entry["source"])
            except OSError as rollback_error:
                errors.append(f"{entry['source']}：{rollback_error}")
            if entry.get("published"):
                try:
                    if not entry.get("published_signature"):
                        raise OSError("已發布的目的文件無法確認狀態，已保留")
                    if _signature(entry["target"]) != entry["published_signature"]:
                        raise OSError("目的文件已被其他程式修改，已保留")
                    entry["target"].unlink()
                except OSError as rollback_error:
                    errors.append(f"{entry['target']}：{rollback_error}")
        if errors:
            rollback_failed = True
            raise OSError(
                f"搬移失敗且部分原始資料無法自動回復：{exc}\n"
                f"請保留並檢查備份資料夾：{originals_dir}\n" + "\n".join(errors)
            ) from exc
        raise OSError(f"搬移失敗，原始文件與註記已保留：{exc}") from exc
    finally:
        _cleanup_relocation_directory(prepared_dir)
        if originals_dir is not None and not rollback_failed:
            _cleanup_relocation_directory(originals_dir)
    return {str(old): str(new)}


def move_document(path: str | Path, dest_folder: str | Path) -> dict[str, str]:
    path = Path(path)
    dest = Path(dest_folder) / path.name
    if str(dest) == str(path):
        return {}
    return rename_document(path, dest)


def rename_folder(old: str | Path, new_name: str) -> dict[str, str]:
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
    old.rename(new)
    mapping: dict[str, str] = {}
    for dirpath, _dirnames, filenames in os.walk(new):
        for filename in filenames:
            new_file = Path(dirpath) / filename
            old_file = old / new_file.relative_to(new)
            mapping[str(old_file)] = str(new_file)
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
