"""Native PDF viewer (QtPdf) with outline, in-app search, and page memory.

Replaces the previous read-only WebEngine PDF plugin, which gave no control
over navigation, search, or the current page. QPdfView exposes a page
navigator, an (async) search model, and per-page signals, so the sidebar TOC,
the shared search bar, and "resume where you left off" all work for PDFs.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPointF, pyqtSignal
from PyQt6.QtPdf import QPdfDocument, QPdfSearchModel
from PyQt6.QtPdfWidgets import QPdfView

try:  # outline extraction (already a project dependency)
    import pymupdf
except Exception:  # pragma: no cover - import guard
    pymupdf = None


def extract_outline(path) -> list[tuple[int, str, int]]:
    """Return [(level, title, page0), ...] from a PDF's bookmarks.

    Pure function (no Qt) so it is testable without constructing a widget.
    """
    if pymupdf is None or not path:
        return []
    try:
        with pymupdf.open(str(path)) as doc:
            toc = doc.get_toc()  # [[level, title, page1based], ...]
    except Exception:
        return []
    return [
        (max(1, int(level)), str(title), max(0, int(page) - 1))
        for level, title, page in toc
    ]


class PdfView(QPdfView):
    page_changed = pyqtSignal(int)          # 0-based current page
    search_count_changed = pyqtSignal(int)  # number of matches

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc = QPdfDocument(self)
        self.setDocument(self._doc)
        self.setPageMode(QPdfView.PageMode.MultiPage)
        self.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        self._search = QPdfSearchModel(self)
        self._search.setDocument(self._doc)
        self.setSearchModel(self._search)
        self._search.countChanged.connect(self._on_search_count)

        self._path: Path | None = None
        self._result_index = -1
        self._pending_page: int | None = None

        self.pageNavigator().currentPageChanged.connect(self.page_changed)
        self._doc.statusChanged.connect(self._on_status)

    # --- loading -----------------------------------------------------
    def load(self, path) -> None:
        self._path = Path(path)
        self._result_index = -1
        self._search.setSearchString("")
        self._doc.load(str(path))

    def _on_status(self, status):
        if status == QPdfDocument.Status.Ready and self._pending_page is not None:
            page = self._pending_page
            self._pending_page = None
            self.jump_to_page(page)

    def restore_page(self, page0: int) -> None:
        """Jump to *page0* now if loaded, otherwise once the document is ready."""
        if page0 <= 0:
            return
        if self._doc.status() == QPdfDocument.Status.Ready:
            self.jump_to_page(page0)
        else:
            self._pending_page = page0

    # --- outline -----------------------------------------------------
    def outline(self) -> list[tuple[int, str, int]]:
        """Return [(level, title, page0), ...] from the PDF bookmarks."""
        return extract_outline(self._path)

    # --- navigation --------------------------------------------------
    def jump_to_page(self, page0: int) -> None:
        try:
            self.pageNavigator().jump(int(page0), QPointF(), 0)
        except Exception:
            pass

    def current_page(self) -> int:
        return self.pageNavigator().currentPage()

    def page_count(self) -> int:
        return self._doc.pageCount()

    # --- search ------------------------------------------------------
    def search(self, text: str) -> None:
        self._result_index = -1
        self._search.setSearchString(text or "")
        if not text:
            self.search_count_changed.emit(0)

    def _on_search_count(self):
        count = self._search.count()
        self.search_count_changed.emit(count)
        if count > 0 and self._result_index < 0:
            self._result_index = 0
            self.setCurrentSearchResultIndex(0)

    def search_next(self) -> None:
        count = self._search.count()
        if count <= 0:
            return
        self._result_index = (self._result_index + 1) % count
        self.setCurrentSearchResultIndex(self._result_index)

    def search_prev(self) -> None:
        count = self._search.count()
        if count <= 0:
            return
        self._result_index = (self._result_index - 1) % count
        self.setCurrentSearchResultIndex(self._result_index)

    def clear_search(self) -> None:
        self._result_index = -1
        self._search.setSearchString("")

    # --- zoom --------------------------------------------------------
    def set_zoom_factor(self, factor: float) -> None:
        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(max(0.25, min(5.0, factor)))
