"""Bounded Markdown metadata shared by previews and source-only editors.

No Qt imports: the same extraction code runs in the killable render child.
``read_metadata`` is blocking and belongs on a worker, never the UI thread.
Preview callers use ``cached_metadata`` after the full render finishes; that
reuses its read/scan rather than scheduling another pass over the file.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
_CACHE_MAX = 64
_CACHE_BYTES = 4 * 1024 * 1024
_CACHE: OrderedDict[tuple, DocumentMetadata] = OrderedDict()
_LOCK = threading.RLock()
_IN_FLIGHT: dict[tuple, threading.Event] = {}


@dataclass(frozen=True)
class DocumentMetadata:
    front_tags: tuple[str, ...] = ()
    body_tags: tuple[str, ...] = ()
    signature: tuple | None = None


def signature(path: Path) -> tuple:
    from .md_converter import _body_signature
    return _body_signature(Path(path))


def _bytes(key: tuple, metadata: DocumentMetadata) -> int:
    return (sys.getsizeof(key) + sum(sys.getsizeof(item) for item in key)
            + sys.getsizeof(metadata) + sys.getsizeof(metadata.front_tags)
            + sys.getsizeof(metadata.body_tags)
            + sum(sys.getsizeof(tag) for tag in
                  metadata.front_tags + metadata.body_tags))


def cached_metadata(path: Path) -> DocumentMetadata | None:
    """Stat/cache lookup only; no source I/O, HTML parse or hashtag scan."""
    try:
        key = signature(path)
    except OSError:
        return None
    with _LOCK:
        result = _CACHE.pop(key, None)
        if result is not None:
            _CACHE[key] = result
        return result


def invalidate(path: Path | None = None) -> None:
    with _LOCK:
        if path is None:
            _CACHE.clear()
        else:
            resolved = str(Path(path).resolve())
            for key in list(_CACHE):
                if key[0] == resolved:
                    _CACHE.pop(key)


def _remember(key: tuple, metadata: DocumentMetadata) -> None:
    with _LOCK:
        for old in list(_CACHE):
            if old[0] == key[0]:
                _CACHE.pop(old)
        if _bytes(key, metadata) > _CACHE_BYTES:
            return
        _CACHE[key] = metadata
        while (len(_CACHE) > _CACHE_MAX or
               sum(_bytes(k, value) for k, value in _CACHE.items()) > _CACHE_BYTES):
            _CACHE.popitem(last=False)


def remember_rendered(path: Path, rendered, expected_signature: tuple) -> None:
    """Publish tags from an already complete render of the same file version."""
    try:
        if signature(path) != expected_signature:
            return
    except OSError:
        return
    _remember(expected_signature, DocumentMetadata(
        tuple(rendered.front_tags), tuple(rendered.body_tags), expected_signature))


def extract_metadata(text: str, *, cancel=None) -> DocumentMetadata:
    from .md_converter import (
        RenderCancelled,
        body_hashtags,
        front_matter_tags,
        parse_front_matter,
    )
    if cancel is not None and cancel.is_set():
        raise RenderCancelled()
    front, body = parse_front_matter(text)
    return DocumentMetadata(tuple(front_matter_tags(front)),
                            tuple(body_hashtags(body, cancel=cancel)))


def _read_uncached(path: Path, *, text: str | None, size: int, cancel):
    from . import md_converter, render_service
    if cancel is not None and cancel.is_set():
        raise md_converter.RenderCancelled()
    if size >= md_converter.SUBPROCESS_MIN_BYTES and render_service.service().enabled():
        try:
            metadata, error = render_service.metadata_remote(
                path=path if text is None else None, text=text, cancel=cancel)
            if error:
                raise OSError(error)
            if metadata is None:
                raise OSError("Metadata worker returned no result")
            return metadata
        except render_service.RenderWorkerCancelled:
            raise md_converter.RenderCancelled() from None
        except render_service.RenderWorkerError:
            # No silent empty tags on transport failure. The line scanner is
            # cancellable even when the optional child is unavailable.
            log.warning("Metadata worker failed; falling back to line scan", exc_info=True)
    if text is None:
        result = md_converter.read_text(path)
        if result is None:
            raise OSError(f"Unable to decode Markdown: {path.name}")
        text = result[0]
    return extract_metadata(text, cancel=cancel)


def read_metadata(path: Path, *, text: str | None = None, cancel=None) -> DocumentMetadata:
    """Extract source tags without rendering HTML; call only in a worker.

    A supplied editor buffer is authoritative and never enters the disk cache.
    Concurrent disk-only metadata readers share a single result. Changed or
    unreadable files raise instead of returning an empty tag set to the index.
    """
    from .md_converter import RenderCancelled
    path = Path(path)
    if cancel is not None and cancel.is_set():
        raise RenderCancelled()
    if text is not None:
        return _read_uncached(path, text=text, size=len(text), cancel=cancel)
    while True:
        key = signature(path)
        with _LOCK:
            cached = _CACHE.get(key)
            if cached is not None:
                _CACHE.move_to_end(key)
                return cached
            pending = _IN_FLIGHT.get(key)
            if pending is None:
                pending = _IN_FLIGHT[key] = threading.Event()
                break
        while not pending.wait(0.02):
            if cancel is not None and cancel.is_set():
                raise RenderCancelled()
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()
    try:
        result = _read_uncached(path, text=None, size=key[2], cancel=cancel)
        if cancel is not None and cancel.is_set():
            raise RenderCancelled()
        if signature(path) != key:
            raise OSError(f"Markdown changed while reading metadata: {path.name}")
        metadata = DocumentMetadata(result.front_tags, result.body_tags, key)
        _remember(key, metadata)
        return metadata
    finally:
        with _LOCK:
            _IN_FLIGHT.pop(key, None)
            pending.set()
