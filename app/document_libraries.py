"""Document library storage and Markdown file discovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import string
from uuid import uuid4

from PySide6.QtCore import QSettings, QStandardPaths

from .file_types import SUPPORTED_EXTENSIONS, document_kind

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "build",
    "dist",
    "pods",
    "deriveddata",
    "target",
    "out",
}

_ORG = "markdown-viewer"
_APP = "MarkdownViewer"
EXCLUDED_FOLDERS_KEY = "excluded_folders"


def _default_store_path() -> Path:
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base or ".") / "markdown-viewer" / "document_libraries.json"


@dataclass(frozen=True)
class DocumentLibrary:
    id: str
    name: str
    path: str

    @classmethod
    def from_dict(cls, raw: dict) -> "DocumentLibrary | None":
        try:
            lib_id = str(raw["id"]).strip()
            path = str(raw["path"]).strip()
        except (KeyError, TypeError):
            return None
        if not lib_id or not path:
            return None
        name = str(raw.get("name") or Path(path).name or path).strip()
        return cls(id=lib_id, name=name, path=path)


@dataclass(frozen=True)
class LibraryDocument:
    library_id: str
    library_name: str
    path: str
    relative_path: str
    kind: str
    modified_ns: int


class DocumentLibraryStore:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _default_store_path()

    def load(self) -> list[DocumentLibrary]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        libraries = []
        seen_paths = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            lib = DocumentLibrary.from_dict(item)
            if not lib:
                continue
            key = _path_key(lib.path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            libraries.append(lib)
        return libraries

    def save(self, libraries: list[DocumentLibrary]):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        data = [asdict(lib) for lib in libraries]
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self._path)

    def add(self, folder: str | Path, name: str | None = None) -> tuple[DocumentLibrary, bool]:
        path = _normalize_path(folder)
        libraries = self.load()
        existing = _find_by_path(libraries, path)
        if existing:
            return existing, False

        lib = DocumentLibrary(
            id=uuid4().hex,
            name=(name or Path(path).name or path),
            path=path,
        )
        self.save([*libraries, lib])
        return lib, True

    def remove(self, library_id: str):
        self.save([lib for lib in self.load() if lib.id != library_id])

    def rename(self, library_id: str, name: str):
        name = str(name).strip()
        if not name:
            return
        self.save(
            [
                DocumentLibrary(id=lib.id, name=name, path=lib.path)
                if lib.id == library_id
                else lib
                for lib in self.load()
            ]
        )


def scan_library_documents(
    libraries: list[DocumentLibrary],
    excluded_folders: list[str] | None = None,
) -> list[LibraryDocument]:
    documents: list[LibraryDocument] = []
    for lib in libraries:
        root = Path(lib.path)
        if not root.exists() or not root.is_dir():
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            relative_parent = Path(dirpath).relative_to(root)
            dirnames[:] = [
                name
                for name in dirnames
                if not should_skip_directory(
                    relative_parent / name, excluded_folders
                )
            ]
            for filename in filenames:
                if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                path = Path(dirpath) / filename
                try:
                    relative = str(path.relative_to(root))
                except ValueError:
                    relative = path.name
                try:
                    modified_ns = path.stat().st_mtime_ns
                except OSError:
                    modified_ns = 0
                documents.append(
                    LibraryDocument(
                        library_id=lib.id,
                        library_name=lib.name,
                        path=str(path),
                        relative_path=relative,
                        kind=document_kind(path),
                        modified_ns=modified_ns,
                    )
                )

    return sorted(
        documents,
        key=lambda doc: (
            doc.library_name.casefold(),
            doc.relative_path.casefold(),
        ),
    )


def discover_cloud_library_paths(home: str | Path | None = None) -> list[Path]:
    """Return common local sync roots for Google Drive, OneDrive, and Dropbox."""

    user_home = Path(home) if home else Path.home()
    candidates: list[Path] = [
        user_home / "Google Drive",
        user_home / "My Drive",
        user_home / "Dropbox",
    ]

    for env_name in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        env_path = os.environ.get(env_name)
        if env_path:
            candidates.append(Path(env_path))

    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:\\")
        candidates.extend(
            [
                drive / "My Drive",
                drive / "Shared drives",
                drive / "我的雲端硬碟",
                drive / "共用雲端硬碟",
            ]
        )

    found = []
    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        key = _path_key(resolved)
        if key in seen or not path.exists() or not path.is_dir():
            continue
        seen.add(key)
        found.append(resolved)
    return found


def _normalize_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _path_key(path: str | Path) -> str:
    return str(Path(path)).casefold()


def _find_by_path(
    libraries: list[DocumentLibrary], path: str
) -> DocumentLibrary | None:
    key = _path_key(path)
    for lib in libraries:
        if _path_key(lib.path) == key:
            return lib
    return None


def load_excluded_folders() -> list[str]:
    """Return normalized user exclusions stored as one entry per line."""
    raw = QSettings(_ORG, _APP).value(EXCLUDED_FOLDERS_KEY, "") or ""
    if isinstance(raw, (list, tuple)):
        values = raw
    else:
        values = str(raw).splitlines()
    return [value for value in (_normalize_exclusion(v) for v in values) if value]


def should_skip_directory(
    directory: str | Path,
    excluded_folders: list[str] | None = None,
) -> bool:
    """Match built-ins, user directory names, or relative path fragments."""
    normalized = str(directory).replace("\\", "/").strip("/")
    name = normalized.rsplit("/", 1)[-1]
    if name.startswith(".") or name.casefold() in _SKIP_DIRS:
        return True

    custom = load_excluded_folders() if excluded_folders is None else excluded_folders
    path_key = f"/{normalized.casefold()}/"
    for value in custom:
        entry = _normalize_exclusion(value)
        if not entry:
            continue
        if "/" not in entry:
            if name.casefold() == entry.casefold():
                return True
        elif f"/{entry.casefold()}/" in path_key:
            return True
    return False


def _normalize_exclusion(value) -> str:
    return str(value).strip().replace("\\", "/").strip("/")


# Backward-compatible private name for existing callers.
_should_skip_directory = should_skip_directory
