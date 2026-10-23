"""Asynchronous PDF page/tile raster scheduler.

Each document generation owns a private ``QPdfDocument``.  Old generations may
finish their at-most-two in-flight requests without ever rendering from a newly
loaded file; their results are discarded by the view's generation/epoch guards.
This also isolates the future compositor (QWidget today, RHI later) from Qt PDF's
queue and lifetime details.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Hashable

from PySide6.QtCore import QObject, QRect, QSize, Signal
from PySide6.QtGui import QImage
from PySide6.QtPdf import (
    QPdfDocument,
    QPdfDocumentRenderOptions,
    QPdfPageRenderer,
)

from . import pdf_metadata_cache


@dataclass(frozen=True)
class PdfRenderSpec:
    key: Hashable
    generation: int
    layout_epoch: int
    page: int
    kind: str  # "page", "preview", or "tile"
    dpr100: int
    # Whole-raster size for page/preview; full target page size for a tile.
    page_px: tuple[int, int]
    content_rect: tuple[int, int, int, int] | None = None


@dataclass
class _RenderSession:
    generation: int
    document: QPdfDocument
    renderer: QPdfPageRenderer
    requests: dict[int, list[PdfRenderSpec]] = field(default_factory=dict)
    keys: set[Hashable] = field(default_factory=set)
    cache_key: tuple | None = None
    cache_cost: int = 0
    reusable: bool = True


class PdfRenderScheduler(QObject):
    """Keep a tiny Qt PDF queue and return QImages without blocking paintEvent."""

    rendered = Signal(object, object)  # PdfRenderSpec, QImage
    capacity_available = Signal()

    def __init__(
        self, parent=None, max_inflight: int = 2, *, reuse_documents: int = 0,
        reuse_budget_bytes: int = 32 * 1024 * 1024,
    ):
        super().__init__(parent)
        self.max_inflight = max(1, int(max_inflight))
        self._current_generation = -1
        self._sessions_by_generation: dict[int, _RenderSession] = {}
        self._sessions_by_renderer: dict[int, _RenderSession] = {}
        self._reuse_documents = max(0, int(reuse_documents))
        self._reuse_budget_bytes = max(0, int(reuse_budget_bytes))
        self._idle_sessions: OrderedDict[tuple, _RenderSession] = OrderedDict()
        self._shutting_down = False

    def begin_document(
        self,
        generation: int,
        path: Path,
        password: str = "",
    ) -> bool:
        """Open an immutable render snapshot for *generation*."""
        self._shutting_down = False
        self._current_generation = int(generation)
        signature = pdf_metadata_cache.signature(path)
        key = (signature, password or "") if signature is not None else None
        cached = self._idle_sessions.pop(key, None) if key is not None else None
        self._retire_idle_old_sessions()

        if cached is not None and cached.document.status() == QPdfDocument.Status.Ready:
            cached.generation = int(generation)
            self._sessions_by_generation[generation] = cached
            self._trim_idle_sessions()
            return True
        if cached is not None:
            self._retire_session(cached)

        document = QPdfDocument(self)
        document.setPassword(password or "")
        error = document.load(str(path))
        if error != QPdfDocument.Error.None_:
            document.deleteLater()
            return False

        renderer = QPdfPageRenderer(self)
        renderer.setDocument(document)
        renderer.setRenderMode(QPdfPageRenderer.RenderMode.MultiThreaded)
        renderer.pageRendered.connect(self._on_page_rendered)
        cost = (int(signature[2]) + document.pageCount() * 2048) if signature else 0
        session = _RenderSession(generation, document, renderer, cache_key=key, cache_cost=cost)
        self._sessions_by_generation[generation] = session
        self._sessions_by_renderer[id(renderer)] = session
        self._trim_idle_sessions()
        return True

    def invalidate(self) -> None:
        """Stop accepting work for the current generation; in-flight work drains."""
        self._current_generation = -1
        self._retire_idle_old_sessions()

    def invalidate_document(self, path: Path) -> None:
        """Prevent same-path reload/write/watcher invalidations from reusing stale PDFs."""
        normalized = pdf_metadata_cache.path_key(Path(path))
        for session in list(self._sessions_by_renderer.values()):
            if session.cache_key and session.cache_key[0][0] == normalized:
                session.reusable = False
                if session.generation != self._current_generation and not session.requests:
                    if session.cache_key in self._idle_sessions:
                        self._idle_sessions.pop(session.cache_key)
                    self._retire_session(session)

    def shutdown(self) -> None:
        """Release idle native documents; outstanding renderer requests drain safely."""
        self._shutting_down = True
        self._current_generation = -1
        for session in list(self._sessions_by_renderer.values()):
            session.reusable = False
            if not session.requests:
                self._retire_session(session)
        self._idle_sessions.clear()

    def pending_count(self, generation: int | None = None) -> int:
        generation = (
            self._current_generation if generation is None else int(generation)
        )
        session = self._sessions_by_generation.get(generation)
        return len(session.requests) if session is not None else 0

    def is_pending(self, generation: int, key: Hashable) -> bool:
        session = self._sessions_by_generation.get(int(generation))
        return session is not None and key in session.keys

    def has_capacity(self, generation: int | None = None) -> bool:
        generation = (
            self._current_generation if generation is None else int(generation)
        )
        session = self._sessions_by_generation.get(generation)
        return (
            session is not None
            and generation == self._current_generation
            and sum(len(item.requests) for item in self._sessions_by_generation.values())
            < self.max_inflight
        )

    def request(self, spec: PdfRenderSpec) -> bool:
        session = self._sessions_by_generation.get(spec.generation)
        if (
            session is None
            or spec.generation != self._current_generation
            or spec.key in session.keys
            or sum(len(item.requests) for item in self._sessions_by_generation.values())
            >= self.max_inflight
        ):
            return False

        options = QPdfDocumentRenderOptions()
        # Deliberately *not* RenderFlag.Annotations. PDFium paints Acrobat
        # annotations in ways a reader does not expect: it draws reply icons
        # that Acrobat itself keeps off the page, and it scales a /NoZoom
        # sticky-note icon with the zoom level, so a 24pt Comment icon grows
        # into a big coloured square over the text. PdfView draws embedded
        # annotations itself (see pdf_annotation_overlay) on top of this clean
        # raster instead, alongside the app's own highlight overlay.
        options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.None_)
        image_size = QSize(*spec.page_px)
        if spec.content_rect is not None:
            x, y, width, height = spec.content_rect
            image_size = QSize(width, height)
            options.setScaledSize(QSize(*spec.page_px))
            # Qt 6.11's PDFium clip matrix uses QRect::right() - left()
            # (and bottom() - top()), so a normal QRect loses the last pixel.
            # The +1 extent yields exactly width x height output while the
            # request image itself remains the nominal tile size.
            options.setScaledClipRect(QRect(x, y, width + 1, height + 1))

        request_id = int(
            session.renderer.requestPage(
                spec.page,
                image_size,
                options,
            )
        )
        if request_id == 0:
            return False
        session.keys.add(spec.key)
        session.requests.setdefault(request_id, []).append(spec)
        return True

    def _on_page_rendered(
        self,
        _page: int,
        _image_size: QSize,
        image: QImage,
        _options: QPdfDocumentRenderOptions,
        request_id: int,
    ) -> None:
        renderer = self.sender()
        session = self._sessions_by_renderer.get(id(renderer))
        if session is None:
            return
        specs = session.requests.pop(int(request_id), [])
        for spec in specs:
            session.keys.discard(spec.key)
            if spec.generation == self._current_generation:
                self.rendered.emit(spec, image)
        if session.generation != self._current_generation and not session.requests:
            self._park_or_retire_session(session)
        self.capacity_available.emit()

    def _retire_idle_old_sessions(self) -> None:
        for session in list(self._sessions_by_generation.values()):
            if session.generation != self._current_generation and not session.requests:
                self._park_or_retire_session(session)

    def _park_or_retire_session(self, session: _RenderSession) -> None:
        if (
            not self._shutting_down and self._reuse_documents
            and session.reusable and session.cache_key is not None
            and 0 < session.cache_cost <= self._reuse_budget_bytes
        ):
            self._sessions_by_generation.pop(session.generation, None)
            old = self._idle_sessions.pop(session.cache_key, None)
            if old is not None and old is not session:
                self._retire_session(old)
            self._idle_sessions[session.cache_key] = session
            self._trim_idle_sessions()
        else:
            self._retire_session(session)

    def _trim_idle_sessions(self) -> None:
        active = self._sessions_by_generation.get(self._current_generation)
        max_idle = max(0, self._reuse_documents - int(active is not None))
        active_cost = active.cache_cost if active is not None else 0
        while self._idle_sessions and (
            len(self._idle_sessions) > max_idle
            or active_cost + sum(item.cache_cost for item in self._idle_sessions.values())
            > self._reuse_budget_bytes
        ):
            _, session = self._idle_sessions.popitem(last=False)
            self._retire_session(session)

    def _retire_session(self, session: _RenderSession) -> None:
        self._sessions_by_generation.pop(session.generation, None)
        self._sessions_by_renderer.pop(id(session.renderer), None)
        if self._idle_sessions.get(session.cache_key) is session:
            self._idle_sessions.pop(session.cache_key)
        session.renderer.pageRendered.disconnect(self._on_page_rendered)
        session.renderer.setDocument(None)
        session.document.close()
        session.renderer.deleteLater()
        session.document.deleteLater()
