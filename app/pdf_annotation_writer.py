"""Write replies and edits back into a PDF's own annotations.

Everywhere else in this feature the PDF is read-only. This module is the one
exception, and it is deliberately narrow:

* only **incremental** saves — the original bytes are never rewritten, so a
  failure can never truncate or reflow a document somebody else authored;
* every write takes a copy of the file into a session temp directory first, so
  a half-finished save can be explained (and the copy pointed at) rather than
  merely reported;
* a file that is encrypted, missing, read-only, or that pymupdf refuses to
  append to is rejected **before** anything is touched, with a reason the UI
  can show;
* only annotations written by the current author can be edited or deleted.

No Qt here: the whole module is testable without a widget.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import itertools
import os
from pathlib import Path
import shutil
import tempfile

_PYMUPDF_UNSET = object()
_pymupdf_module = _PYMUPDF_UNSET

_session_backup_dir: Path | None = None
# Every write keeps its own copy, so a sequence of edits can each be recovered
# from the session directory rather than only the most recent one.
_backup_counter = itertools.count(1)

# Sticky notes this app writes are small and pinned beside the parent's mark.
_REPLY_SIZE = 20.0


class AnnotationWriteError(Exception):
    """A write was refused or failed; ``str(...)`` is shown to the user."""


def _pymupdf():
    global _pymupdf_module
    if _pymupdf_module is _PYMUPDF_UNSET:
        try:
            import pymupdf as _mod
        except Exception:  # pragma: no cover - import guard
            _mod = None
        _pymupdf_module = _mod
    return _pymupdf_module


def default_author() -> str:
    """Who new annotations are attributed to when nothing is configured."""
    for name in (os.environ.get("USERNAME"), os.environ.get("USER")):
        if name and name.strip():
            return name.strip()
    try:
        return os.getlogin()
    except OSError:
        return "User"


def session_backup_dir() -> Path:
    """A per-session temp directory holding pre-write copies of edited PDFs."""
    global _session_backup_dir
    if _session_backup_dir is None or not _session_backup_dir.exists():
        _session_backup_dir = Path(
            tempfile.mkdtemp(prefix="mdviewer-pdf-annot-")
        )
    return _session_backup_dir


def cleanup_session_backups() -> None:
    """Drop the session's backup copies; safe to call more than once."""
    global _session_backup_dir
    if _session_backup_dir is not None:
        shutil.rmtree(_session_backup_dir, ignore_errors=True)
        _session_backup_dir = None


def backup(path) -> Path | None:
    """Copy *path* into the session backup directory before it is modified.

    Each write gets its own numbered copy: a single ``{stem}-{pid}`` name would
    have every edit overwrite the previous snapshot, which defeats the point of
    keeping one at all.
    """
    source = Path(path)
    try:
        stamp = datetime.now().strftime("%H%M%S")
        target = session_backup_dir() / (
            f"{source.stem}-{os.getpid()}-{stamp}-{next(_backup_counter):03d}"
            f"{source.suffix}"
        )
        shutil.copy2(source, target)
        return target
    except OSError:
        return None


LOCKED_MESSAGE = "檔案正被其他程式使用，無法寫入"


def _is_locked(exc: BaseException) -> bool:
    """Whether *exc* means another program holds the file open exclusively."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, OSError) and getattr(exc, "winerror", None) in (32, 33)


@contextmanager
def _wrapped(what: str):
    """Turn any pymupdf failure inside the block into an AnnotationWriteError.

    The callers in ``MainWindow`` only handle ``AnnotationWriteError``; a raw
    MuPDF ``RuntimeError`` escaping from here would reach the reader as an
    unhandled traceback instead of a message.
    """
    try:
        yield
    except AnnotationWriteError:
        raise
    except Exception as exc:
        if _is_locked(exc):
            raise AnnotationWriteError(f"{LOCKED_MESSAGE}：{exc}") from exc
        raise AnnotationWriteError(f"{what}失敗：{exc}") from exc


def _pdf_now() -> str:
    return datetime.now().strftime("D:%Y%m%d%H%M%S")


def writable_reason(path, password: str = "") -> str | None:
    """Why annotations cannot be written to *path*, or None when they can.

    Checked up front so the UI can disable the reply box with a reason instead
    of letting the user type a comment that will be thrown away on save.
    """
    mupdf = _pymupdf()
    if mupdf is None:
        return "未安裝 PyMuPDF，無法寫入註解"
    if not path:
        return "尚未開啟 PDF"
    file_path = Path(path)
    if not file_path.exists():
        return "檔案不存在"
    if not os.access(file_path, os.W_OK):
        return "檔案為唯讀，無法寫入註解"
    try:
        with mupdf.open(str(file_path)) as doc:
            if doc.needs_pass or doc.is_encrypted:
                return "此 PDF 已加密，無法寫入註解"
            if not doc.can_save_incrementally():
                return "此 PDF 不支援增量儲存，無法安全寫入註解"
    except Exception as exc:
        # A file another program holds open is a different (and recoverable)
        # problem from a file this app cannot parse; say which one it is.
        if _is_locked(exc):
            return LOCKED_MESSAGE
        return "無法讀取此 PDF，無法寫入註解"
    return None


def _save_incremental(doc, path: Path) -> None:
    mupdf = _pymupdf()
    try:
        doc.save(
            str(path),
            incremental=True,
            encryption=mupdf.PDF_ENCRYPT_KEEP,
        )
    except Exception as exc:
        if _is_locked(exc):
            raise AnnotationWriteError(f"{LOCKED_MESSAGE}：{exc}") from exc
        raise AnnotationWriteError(f"儲存註解失敗：{exc}") from exc


def _find_annot(page, xref: int):
    for annot in page.annots():
        if int(annot.xref) == int(xref):
            return annot
    return None


def _locate(doc, xref: int):
    """Return (page, annot) for *xref* anywhere in the document."""
    for page in doc:
        annot = _find_annot(page, xref)
        if annot is not None:
            return page, annot
    return None, None


def _owned_by(annot, author: str) -> bool:
    try:
        title = str((annot.info or {}).get("title") or "").strip()
    except Exception:
        return False
    return bool(author) and title == author.strip()


def _open_for_write(path, password: str):
    reason = writable_reason(path, password)
    if reason:
        raise AnnotationWriteError(reason)
    mupdf = _pymupdf()
    try:
        return mupdf.open(str(path))
    except Exception as exc:
        raise AnnotationWriteError(f"無法開啟 PDF：{exc}") from exc


def add_reply(
    path,
    parent_xref: int,
    text: str,
    author: str,
    password: str = "",
) -> int:
    """Append a ``Text`` annotation replying to *parent_xref*; return its xref.

    The reply is written the way Acrobat writes one: a sticky note carrying
    ``/IRT`` to its parent, the parent's colour, and the author's name, saved
    incrementally so the rest of the file is untouched.
    """
    body = str(text or "").strip()
    if not body:
        raise AnnotationWriteError("回覆內容不可空白")
    file_path = Path(path)
    backup(file_path)
    doc = _open_for_write(file_path, password)
    try:
        with _wrapped("建立回覆"):
            page, parent = _locate(doc, int(parent_xref))
            if parent is None:
                raise AnnotationWriteError("找不到要回覆的註解")
            rect = parent.rect
            try:
                colors = parent.colors or {}
                stroke = colors.get("stroke") or colors.get("fill") or None
            except Exception:
                stroke = None
            reply = page.add_text_annot(
                (float(rect.x0), float(rect.y0)), body, icon="Comment"
            )
            reply.set_info(title=str(author or "").strip(), content=body)
            if stroke:
                try:
                    reply.set_colors(stroke=tuple(float(c) for c in stroke[:3]))
                except Exception:
                    pass
            reply.update()
            doc.xref_set_key(reply.xref, "IRT", f"{int(parent_xref)} 0 R")
            doc.xref_set_key(reply.xref, "M", f"({_pdf_now()})")
            new_xref = int(reply.xref)
        _save_incremental(doc, file_path)
        return new_xref
    finally:
        doc.close()


def edit_annotation(
    path,
    xref: int,
    text: str,
    author: str,
    password: str = "",
) -> None:
    """Replace the text of an annotation *author* wrote."""
    body = str(text or "").strip()
    if not body:
        raise AnnotationWriteError("註解內容不可空白")
    file_path = Path(path)
    backup(file_path)
    doc = _open_for_write(file_path, password)
    try:
        with _wrapped("編輯註解"):
            _page, annot = _locate(doc, int(xref))
            if annot is None:
                raise AnnotationWriteError("找不到要編輯的註解")
            if not _owned_by(annot, author):
                raise AnnotationWriteError("只能編輯自己建立的註解")
            annot.set_info(content=body)
            annot.update()
            doc.xref_set_key(int(xref), "M", f"({_pdf_now()})")
            # /RC would otherwise keep showing the old text in Acrobat.
            try:
                doc.xref_set_key(int(xref), "RC", "null")
            except Exception:
                pass
        _save_incremental(doc, file_path)
    finally:
        doc.close()


def delete_annotation(path, xref: int, author: str, password: str = "") -> None:
    """Remove an annotation *author* wrote."""
    file_path = Path(path)
    backup(file_path)
    doc = _open_for_write(file_path, password)
    try:
        with _wrapped("刪除註解"):
            page, annot = _locate(doc, int(xref))
            if annot is None:
                raise AnnotationWriteError("找不到要刪除的註解")
            if not _owned_by(annot, author):
                raise AnnotationWriteError("只能刪除自己建立的註解")
            page.delete_annot(annot)
        _save_incremental(doc, file_path)
    finally:
        doc.close()


def owns(entry, author: str) -> bool:
    """Whether *author* may edit/delete the extracted annotation *entry*."""
    return bool(author) and str(entry.author or "").strip() == str(author).strip()
