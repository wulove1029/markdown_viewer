"""Asynchronous PDF page/tile raster scheduler.

Each document generation owns a private ``QPdfDocument``.  Old generations may
finish their at-most-two in-flight requests without ever rendering from a newly
loaded file; their results are discarded by the view's generation/epoch guards.
This also isolates the future compositor (QWidget today, RHI later) from Qt PDF's
queue and lifetime details.
"""

from __future__ import annotations

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


class PdfRenderScheduler(QObject):
    """Keep a tiny Qt PDF queue and return QImages without blocking paintEvent."""

    rendered = Signal(object, object)  # PdfRenderSpec, QImage
    capacity_available = Signal()

    def __init__(self, parent=None, max_inflight: int = 2):
        super().__init__(parent)
        self.max_inflight = max(1, int(max_inflight))
        self._current_generation = -1
        self._sessions_by_generation: dict[int, _RenderSession] = {}
        self._sessions_by_renderer: dict[int, _RenderSession] = {}

    def begin_document(
        self,
        generation: int,
        path: Path,
        password: str = "",
    ) -> bool:
        """Open an immutable render snapshot for *generation*."""
        self._current_generation = int(generation)
        self._retire_idle_old_sessions()

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
        session = _RenderSession(generation, document, renderer)
        self._sessions_by_generation[generation] = session
        self._sessions_by_renderer[id(renderer)] = session
        return True

    def invalidate(self) -> None:
        """Stop accepting work for the current generation; in-flight work drains."""
        self._current_generation = -1
        self._retire_idle_old_sessions()

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
            and len(session.requests) < self.max_inflight
        )

    def request(self, spec: PdfRenderSpec) -> bool:
        session = self._sessions_by_generation.get(spec.generation)
        if (
            session is None
            or spec.generation != self._current_generation
            or spec.key in session.keys
            or len(session.requests) >= self.max_inflight
        ):
            return False

        options = QPdfDocumentRenderOptions()
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
            self._retire_session(session)
        self.capacity_available.emit()

    def _retire_idle_old_sessions(self) -> None:
        for session in list(self._sessions_by_generation.values()):
            if session.generation != self._current_generation and not session.requests:
                self._retire_session(session)

    def _retire_session(self, session: _RenderSession) -> None:
        self._sessions_by_generation.pop(session.generation, None)
        self._sessions_by_renderer.pop(id(session.renderer), None)
        session.renderer.pageRendered.disconnect(self._on_page_rendered)
        session.renderer.setDocument(None)
        session.document.close()
        session.renderer.deleteLater()
        session.document.deleteLater()
