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

from .atomic_io import atomic_write_bytes

try:  # optional dependency; delete_document falls back to permanent delete
    from send2trash import send2trash as _send2trash
except ImportError:  # pragma: no cover - depends on environment
    _send2trash = None

HAS_SEND2TRASH = _send2trash is not None

INVALID_NAME_CHARS = '<>:"/\\|?*'

_SIDECAR_SUFFIXES = (".notes.json", ".highlights.json")


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


def create_folder(parent: str | Path, name: str) -> Path:
    name = name.strip()
    if not is_valid_name(name):
        raise OSError(f"無效的資料夾名稱：{name}")
    path = Path(parent) / name
    if path.exists():
        raise OSError(f"已存在同名項目：{path}")
    path.mkdir(parents=False)
    return path


def rename_document(old: str | Path, new: str | Path) -> dict[str, str]:
    """Rename/move a document plus its sidecars. Returns the path mapping."""
    old = Path(old)
    new = Path(new)
    if new.exists():
        raise OSError(f"已存在同名檔案：{new}")
    old.rename(new)
    for sidecar in sidecar_paths(old):
        if sidecar.exists():
            target = new.with_name(new.name + sidecar.name[len(old.name):])
            try:
                sidecar.rename(target)
            except OSError:
                pass  # document already moved; a stranded sidecar is non-fatal
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
