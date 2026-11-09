"""Right-side Markdown renderer (QWebEngineView wrapper)."""

import logging

log = logging.getLogger(__name__)

import html as html_module
import json
import math
import threading
import time
import urllib.parse
import weakref
from collections import OrderedDict
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QFile,
    QIODevice,
    QMarginsF,
    QObject,
    QRunnable,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QPageLayout, QPageSize
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlScheme,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QLabel, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from . import background_jobs, md_converter, render_service
from .annotation_bridge import AnnotationBridge
from .attachment_security import attachment_open_policy
from .file_types import is_markdown, is_pdf, is_supported_document
from .html_loading import HtmlPreparation
from .md_converter import RenderCancelled, convert, convert_text, state_page_html
from .pdf_repair_async import repair_async
from .pdf_tounicode import repair_radical_tounicode
from .web_security import blocks_file_request, document_host, install_remote_file_guard

_RENDER_GENERATION_META = "markdown-viewer-render-generation"
_RENDER_ERROR_META = "markdown-viewer-render-error"
_LOADING_DELAY_MS = 140
_LOADING_DETAILS_DELAY_MS = 1000
_PAGE_NAVIGATION_LIMIT = 10


def _run_page_javascript(view, script, callback=None):
    """Treat replies from a retired page as empty, never as current document data."""
    page = view.page()
    epoch = getattr(view, "_page_epoch", 0)
    if callback is None:
        page.runJavaScript(script)
        return

    def checked(value):
        if isinstance(view, QObject) and not isValid(view):
            return
        if epoch != getattr(view, "_page_epoch", 0) or page is not view.page():
            callback(None)
        else:
            callback(value)

    page.runJavaScript(script, checked)

_UNHANDLED_ESCAPE_JS = r"""
(function () {
  if (window.__mdUnhandledEscapeInstalled) return;
  window.__mdUnhandledEscapeInstalled = true;
  if (typeof QWebChannel === "undefined" || !window.qt ||
      !qt.webChannelTransport) return;
  new QWebChannel(qt.webChannelTransport, function (channel) {
    var bridge = channel.objects.bridge;
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" && event.key !== "Esc") return;
      var generation = Number(window.__mdSearchEscapeGeneration || 0);
      if (generation <= 0) return;
      window.setTimeout(function () {
        if (!event.defaultPrevented && bridge &&
            bridge.reportUnhandledEscape) {
          bridge.reportUnhandledEscape(generation);
        }
      }, 0);
    });
  });
})();
"""


def _source_line_reveal_js(generation: int, line_number: int) -> str:
    """Reveal the smallest visible block containing a one-based source line."""
    return f"""(function() {{
        var marker = document.querySelector('meta[name="{_RENDER_GENERATION_META}"]');
        if (!marker || Number(marker.content) !== {int(generation)}) return false;
        var line = {int(line_number) - 1};
        var blocks = document.querySelectorAll('[data-src-start][data-src-end]');
        var target = null, smallest = Infinity;
        for (var i = 0; i < blocks.length; i++) {{
            var block = blocks[i];
            var start = Number(block.getAttribute('data-src-start'));
            var end = Number(block.getAttribute('data-src-end'));
            if (!Number.isFinite(start) || !Number.isFinite(end) ||
                start > line || end < line || !block.getClientRects().length) continue;
            var size = end - start;
            if (size < smallest || (size === smallest && target && target.contains(block))) {{
                target = block;
                smallest = size;
            }}
        }}
        if (!target) return false;
        var request = {{}};
        window.__mdSourceRevealRequest = request;
        // Native find navigation settles on the next visual frame. Apply our
        // explicit source target after that frame, not inside its IPC reply.
        requestAnimationFrame(function() {{
            var current = document.querySelector('meta[name="{_RENDER_GENERATION_META}"]');
            if (window.__mdSourceRevealRequest !== request || !current ||
                Number(current.content) !== {int(generation)}) return;
            target.scrollIntoView({{block: 'center', behavior: 'instant'}});
        }});
        return true;
    }})()"""


def _decode_content_size(value):
    """Decode a WebEngine content-size result into positive finite pixels.

    Qt WebEngine does not reliably convert JavaScript arrays to QVariant on
    every Chromium/PySide combination.  The JavaScript caller therefore sends
    JSON text, while accepting a native sequence here keeps the helper
    compatible with engines that do support direct array conversion.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    try:
        width = float(value[0])
        height = float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not math.isfinite(width) or not math.isfinite(height):
        return None
    if width <= 0 or height <= 0:
        return None
    return [width, height]


def _html_with_render_generation(html: str, generation: int) -> str:
    marker = f'<meta name="{_RENDER_GENERATION_META}" content="{generation}">'
    if "</head>" in html:
        return html.replace("</head>", marker + "\n</head>", 1)
    return marker + html


def _html_with_early_scroll(html: str, scroll_y: int, anchor=None) -> str:
    """Scroll before first paint so a reload never flashes the top of the page.

    The loadFinished restore in _on_markdown_load_checked still runs afterwards
    (idempotent); this only removes the visible jump while it is pending.
    """
    # Block line + viewport offset survives changed wrapping and late images.
    # Any real navigation input cancels late restores, so images never drag a
    # reader back after they have started scrolling or editing the page.
    anchor_json = json.dumps(anchor)
    script = f"""<script>(function() {{
        window.__mdReadingRestore = {anchor_json} || {{}};
        window.__mdApplyReadingRestore = function() {{
            var a = window.__mdReadingRestore;
            if (!a) return;
            var block = a.line == null ? null : document.querySelector(
                '[data-src-start="' + Number(a.line) + '"]');
            if (block) window.scrollTo(0, window.scrollY + block.getBoundingClientRect().top - a.offset);
            else window.scrollTo(0, {int(scroll_y)});
        }};
        window.__mdApplyReadingRestore();
        requestAnimationFrame(window.__mdApplyReadingRestore);
        document.addEventListener('load', function(e) {{
            if (e.target && e.target.tagName === 'IMG') window.__mdApplyReadingRestore();
        }}, true);
        ['wheel', 'touchstart', 'pointerdown', 'keydown'].forEach(function(name) {{
            document.addEventListener(name, function() {{
                window.__mdReadingRestore = null; window.__mdReadingInteracted = true;
            }},
                {{capture: true, passive: true, once: true}});
        }});
    }})();</script>"""
    if "</body>" in html:
        return html.replace("</body>", script + "\n</body>", 1)
    return html + script


def _pending_scroll_target(
    pending_scroll: int | None,
    pending_generation: int | None,
    loaded_generation,
) -> tuple[int | None, int | None, int | None]:
    try:
        loaded_generation = int(loaded_generation)
    except (TypeError, ValueError):
        return None, pending_scroll, pending_generation
    if pending_scroll is None or pending_generation != loaded_generation:
        return None, pending_scroll, pending_generation
    return pending_scroll, None, None


# Register the custom "wikilink" scheme so Chromium routes [[note]] clicks
# through acceptNavigationRequest instead of silently dropping them. Must run
# before the QApplication / web engine starts — this import happens at module
# load, ahead of QApplication() in both main.py and the app's entry points.
from .url_schemes import register_document_schemes

register_document_schemes()


def _home_actions_html() -> str:
    """Home-page action links, intercepted by :class:`_DocumentPage`.

    "新增筆記" is the primary action (listed first, tagged with its Ctrl+N
    shortcut) so a blank workspace can go straight into writing; open,
    recent, and quick-open stay available beside it.
    """
    return (
        '<nav class="home-actions" aria-label="開始使用">'
        '<a class="home-action-primary" href="https://markdown-viewer.invalid/home/new">'
        "新增筆記 <kbd>Ctrl+N</kbd></a>"
        '<a href="https://markdown-viewer.invalid/home/open">開啟文件 <kbd>Ctrl+O</kbd></a>'
        '<a href="https://markdown-viewer.invalid/home/recent">最近文件</a>'
        '<a href="https://markdown-viewer.invalid/home/quick">快速開啟 <kbd>Ctrl+P</kbd></a></nav>'
    )


def _same_document(url: QUrl, other) -> bool:
    """True when *url* and *other* differ at most in their fragment.

    ``QUrl.matches(..., UrlFormattingOption.RemoveFragment)`` raises a
    TypeError on PySide6 6.11 (only ComponentFormattingOption is accepted),
    which made every preview link click fail closed; compare
    fragment-stripped copies instead.
    """
    if other is None:
        return False
    left, right = QUrl(url), QUrl(other)
    if left.isEmpty() or right.isEmpty():
        return False
    left.setFragment(None)
    right.setFragment(None)
    return left == right


def _current_document_host(view) -> str:
    """UNC host of the document *view* shows ("" for local documents)."""
    return document_host(getattr(view, "_current_path", None))


# Schemes Chromium loads by itself.  Anything else is "unknown" to it: Qt
# offers a user-gesture navigation there to acceptNavigationRequest and, if it
# is accepted, hands the URL to the OS (QDesktopServices::openUrl in C++).
_ENGINE_SCHEMES = frozenset({
    "http", "https", "file", "data", "about", "blob", "filesystem", "qrc",
    "chrome", "chrome-extension", "chrome-error", "devtools",
})
# A clicked link may leave the preview only through these (file: by S2's rules).
_EXTERNAL_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _after_navigation_callback(view, action, *args) -> None:
    """Run *action* once Chromium's navigation callback has returned.

    acceptNavigationRequest runs inside Chromium's navigation throttle.
    Stopping, replacing or reloading the page from there -- which opening a
    note does via deactivate() -> stop() -- trips a Chromium CHECK (the
    process dies with 0x80000003), and a modal dialog would spin a nested
    event loop inside it.  The callback only decides; everything else is
    queued here.  Arguments must be values (copy any QUrl Chromium passed in).
    """
    def run():
        action(*args)

    if isinstance(view, QObject):
        QTimer.singleShot(0, view, run)  # dropped if the view is gone by then
    else:
        QTimer.singleShot(0, run)


def _warn_network_link(view, text: str) -> None:
    QMessageBox.warning(
        view,
        "已封鎖網路路徑連結",
        "為避免連線到文件指定的網路主機，Markdown Viewer 不會開啟：\n"
        f"{text or '（無效的網路路徑）'}",
    )


def _open_attachment(view, url: QUrl, local: str) -> None:
    policy = attachment_open_policy(local)
    if policy == "block":
        QMessageBox.warning(
            view,
            "已封鎖不安全的附件",
            "為避免執行程式或腳本，Markdown Viewer 不會開啟：\n"
            f"{local}",
        )
    elif policy == "open" or QMessageBox.question(
        view,
        "開啟外部附件",
        "這個檔案類型不在一般文件清單中。\n"
        f"確定要交給系統開啟嗎？\n\n{local}",
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes:
        QDesktopServices.openUrl(url)


class _DocumentPage(QWebEnginePage):
    """Intercept link clicks: wiki-links and cross-note links open in-app,
    external links open in the system browser, in-page anchors scroll."""

    def __init__(self, view, profile=None):
        if profile is None:
            super().__init__(view)
        else:
            super().__init__(profile, view)
        self._view = view
        # Untrusted Markdown must not make Chromium open file://host/... or
        # \\.\UNC\host (SMB, NTLM leak) through an image, CSS or link; only
        # resource loads from the share the current document itself lives on
        # stay reachable.  The guard holds the view weakly: a strong reference
        # from this C++-owned child back to its owner crashes PySide when the
        # wrappers are torn down.
        view_ref = weakref.ref(view)
        self._remote_file_guard = install_remote_file_guard(
            self, lambda: _current_document_host(view_ref())
        )

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        # Decide only: every side effect goes through _after_navigation_callback.
        view = self._view
        clicked = nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        if url.isEmpty() or not url.isValid():
            # e.g. Chromium's file://./UNC/host/... which QUrl cannot hold.
            if clicked:
                _after_navigation_callback(view, _warn_network_link, view, url.toString())
            return False
        scheme = url.scheme()
        if scheme == "https" and url.host() == "markdown-viewer.invalid":
            route = url.path(QUrl.ComponentFormattingOption.FullyEncoded)
            if route.startswith("/wikilink/"):
                target = urllib.parse.unquote(route.removeprefix("/wikilink/"))
                if target and clicked:
                    _after_navigation_callback(view, view.wikilink_clicked.emit, target)
                return False
            if view._current_path is None and clicked:
                _after_navigation_callback(
                    view, view.home_action_requested.emit, url.path().removeprefix("/home/"))
            return False
        # The wikilink scheme is always ours — intercept it regardless of how
        # the navigation was triggered (real click, JS, etc.).
        if scheme == "wikilink":
            raw = url.toString()
            target = urllib.parse.unquote(raw.split(":", 1)[1]) if ":" in raw else ""
            if target:
                _after_navigation_callback(view, view.wikilink_clicked.emit, target)
            return False
        if clicked:
            return self._accept_link_click(url, scheme)
        if scheme not in _ENGINE_SCHEMES:
            # Same rule as for clicks, whatever started the navigation (form,
            # redirect, script): an unknown scheme never gets a True that Qt
            # would turn into an OS launch.
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)

    def _accept_link_click(self, url, scheme) -> bool:
        """Whitelist for clicked links; anything not listed here is refused."""
        view = self._view
        # Same-document fragment (footnote/anchor) -> let Chromium scroll.
        if url.hasFragment() and _same_document(url, self.url()):
            return True
        base_url = getattr(view, "_html_base_url", None)
        if url.hasFragment() and _same_document(url, base_url):
            # A file-backed large preview keeps the original document's
            # base URL; its fragment must not navigate to a directory.
            _after_navigation_callback(view, view.scroll_to, url.fragment())
            return False
        if scheme in _EXTERNAL_LINK_SCHEMES:
            _after_navigation_callback(view, QDesktopServices.openUrl, QUrl(url))
            return False
        if scheme == "file":
            if blocks_file_request(url):
                # file://host/... or a device-namespace UNC path would open
                # an SMB connection to a host picked by the document (NTLM
                # hash leak).  Navigation never gets the document-share
                # exception the resource guard grants.
                _after_navigation_callback(view, _warn_network_link, view, url.toString())
                return False
            local = url.toLocalFile()
            if is_supported_document(local):
                if url.hasFragment():
                    _after_navigation_callback(
                        view, view.local_doc_target_clicked.emit, local, url.fragment())
                else:
                    _after_navigation_callback(view, view.local_doc_clicked.emit, local)
            else:
                _after_navigation_callback(view, _open_attachment, view, QUrl(url), local)
            return False
        # foo:, ms-settings:, search-ms:, other app schemes, about:, data:,
        # javascript: ...: never handed to the OS and never replace the preview.
        return False


class _RenderSignals(QObject):
    ready = Signal(int, object, str, list)
    # Fast text reading: a block-boundary prefix of a very large document,
    # emitted before ``ready`` so there is something to read while the full
    # preview finishes in the render worker process.
    partial_ready = Signal(int, object, str, list, int)


class _LoadingShield(QWidget):
    """Intercept old-page input immediately; display a hint only for slow loads."""

    open_document_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("previewLoadingShield")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._details = QLabel(
            "這份文件仍在準備預覽，解析或排版可能需要較久。\n"
            "可以先切換分頁，或開啟其他文件。", self,
        )
        self._details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._details.setWordWrap(True)
        self._details.setAccessibleName(self._details.text())
        self._open_button = QPushButton("開啟其他文件…", self)
        self._open_button.setAccessibleName("開啟其他文件")
        self._open_button.setMinimumHeight(32)
        self._open_button.clicked.connect(lambda _checked=False: self.open_document_requested.emit())
        layout = QVBoxLayout(self)
        layout.addStretch()
        layout.addWidget(self._label)
        layout.addWidget(self._details)
        layout.addWidget(self._open_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self._details.hide()
        self._open_button.hide()
        self.hide()

    def arm(self, path: Path):
        self.setStyleSheet("#previewLoadingShield { background: transparent; }")
        self._label.setText(f"正在讀取：{path.name}")
        self._label.hide()
        self._details.hide()
        self._open_button.hide()
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def reveal(self, theme: str):
        background = "rgba(28, 30, 34, 210)" if theme == "dark" else "rgba(248, 249, 251, 220)"
        foreground = "#e8e8e8" if theme == "dark" else "#353b45"
        self.setStyleSheet(
            f"#previewLoadingShield {{ background: {background}; }}"
            f"QLabel {{ color: {foreground}; padding: 20px; font-size: 15px; }}"
        )
        self._label.show()

    def reveal_details(self, theme: str):
        self.reveal(theme)
        self._details.show()
        self._open_button.show()

    def keyPressEvent(self, event):
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()


class _MarkdownRenderWorker(QRunnable):
    """Run Markdown conversion off the GUI thread."""

    def __init__(
        self,
        generation: int,
        *,
        path: Path | None = None,
        text: str | None = None,
        theme: str = "light",
        title: str = "preview",
        cancel: threading.Event | None = None,
        defer_code: bool = True,
    ):
        super().__init__()
        self.generation = generation
        self.path = path
        self.text = text
        self.theme = theme
        self.title = title
        self.cancel = cancel
        self.defer_code = defer_code
        self.signals = _RenderSignals()

    def _cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def _emit_fast_preview(self, size: int) -> None:
        """Show a labelled prefix of a very large document straight away.

        Only ever reads the file on disk, never a draft buffer, and the result
        is marked partial in the page itself, so nothing downstream can mistake
        it for the whole document.
        """
        path = self.path
        if md_converter.has_cached_body(path):
            return  # the full body is already cached; it will arrive instantly
        partial = md_converter.render_file_partial(path, size, cancel=self.cancel)
        if partial is None or self._cancelled():
            return  # one giant block: keep the loading page, no fake prefix
        rendered, _prefix_bytes = partial
        html = md_converter.wrap_body(rendered, path.stem, self.theme)
        if self._cancelled():
            return
        percent = max(1, round(_prefix_bytes * 100 / max(size, 1)))
        self.signals.partial_ready.emit(self.generation, path, html,
                                        list(rendered.headings), percent)

    def run(self):
        try:
            if self.cancel is not None and self.cancel.is_set():
                return
            # Only parser work holds its lock; disk IO must remain outside it.
            if self.path is not None:
                try:
                    size = self.path.stat().st_size
                except OSError:
                    size = 0
                if size >= md_converter.SUBPROCESS_MIN_BYTES:
                    # Warm the killable render worker while the prefix renders
                    # here, so its startup overlaps the fast preview.
                    render_service.prewarm()
                if (not md_converter.has_cached_body(self.path)
                        and md_converter.should_fast_preview(self.path, size)):
                    try:
                        self._emit_fast_preview(size)
                    except RenderCancelled:
                        return
                    except Exception:  # a partial view never breaks the real one
                        log.warning("run: optional operation failed", exc_info=True)
                if self._cancelled():
                    return
                html, headings = convert(self.path, self.theme, cancel=self.cancel)
                source = self.path
            else:
                html, headings = convert_text(self.text or "", self.theme, self.title,
                                              cancel=self.cancel)
                source = None
            if self.defer_code:
                from .code_loading import defer_code_highlighting
                html = defer_code_highlighting(html)
        except RenderCancelled:
            return
        except Exception as exc:
            label = self.path.name if self.path is not None else self.title
            html = state_page_html(
                "無法預覽 Markdown",
                f"渲染 {label} 時發生錯誤：{exc}",
                self.theme,
                "錯誤",
            )
            html = html.replace("</head>", f'<meta name="{_RENDER_ERROR_META}" content="true"></head>', 1)
            headings = []
            source = self.path
        if self.cancel is None or not self.cancel.is_set():
            self.signals.ready.emit(self.generation, source, html, headings)


class RendererView(QWebEngineView):
    home_action_requested = Signal(str)
    open_document_requested = Signal()
    active_anchor_changed = Signal(str)
    wikilink_clicked = Signal(str)
    local_doc_clicked = Signal(str)
    local_doc_target_clicked = Signal(str, str)
    source_position_changed = Signal(int, float)
    reading_progress_changed = Signal(float)
    translate_requested = Signal(str)  # selected text to translate
    # (search text, percent of the document loaded) when a search misses only
    # because fast text reading has not loaded the rest of the document yet.
    partial_search_missed = Signal(str, int)
    # Verified document identity plus two animation frames, not loadFinished.
    # Arguments are renderer generation, document path, and partial preview.
    content_ready = Signal(int, str, bool)
    render_failed = Signal(int, str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self.setPage(_DocumentPage(self))
        self._page_epoch = 0
        self._page_navigation_count = 0
        self._page_last_generation = None
        self._page_rotation_count = 0
        self._page_signal_bindings = None
        self._navigation_serial = 0
        self._deferred_navigation = None
        self._pdf_printing = False
        self._on_headings_ready = on_headings_ready
        self._current_anchor = ""
        self._current_path: Path | None = None
        self._theme = "light"
        self._side_notes_visible = False
        self._pdf_callback = None
        self._pdf_async_repair: tuple[bool, Any] = (False, None)
        self._zoom_factor = 1.0
        self._pending_scroll: int | None = None
        self._pending_scroll_generation: int | None = None
        self._pending_find: tuple[int, str] | None = None
        self._source_line_reveal: tuple[int, int] | None = None
        self._loaded_markdown_generation: int | None = None
        # Fast text reading state: True while the page shows a labelled prefix
        # of a large document instead of the whole thing.
        self._partial_preview_active = False
        self._partial_scope_percent = 0
        self._partial_restore: dict | None = None
        self._partial_user_navigated = False
        self._pending_export: tuple | None = None
        # Scroll ratio to re-apply once a live-preview (text) render loads, so
        # the debounced re-render doesn't snap the preview back to the top.
        self._pending_ratio: tuple[int, float] | None = None
        self._render_generation = 0
        self._render_pool = background_jobs.pool()
        self._pending_text_base_url: QUrl | None = None
        self._scroll_y = 0  # last polled vertical scroll (for per-tab restore)
        self._scroll_anchor = None
        self._pending_reading_anchor = None
        self._document_signature = None
        self._reading_positions = OrderedDict()
        self._search_escape_generation = 0
        self._loading_generation: int | None = None
        self._loading_shield = _LoadingShield(self)
        self._loading_shield.open_document_requested.connect(self.open_document_requested.emit)
        self._loading_timer = QTimer(self)
        self._loading_timer.setSingleShot(True)
        self._loading_timer.setInterval(_LOADING_DELAY_MS)
        self._loading_timer.timeout.connect(self._reveal_loading)
        self._loading_details_timer = QTimer(self)
        self._loading_details_timer.setSingleShot(True)
        self._loading_details_timer.setInterval(_LOADING_DETAILS_DELAY_MS)
        self._loading_details_timer.timeout.connect(self._reveal_loading_details)
        self._frame_serial = 0
        self._frame_request = None
        self._frame_polls = 0
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.setInterval(16)
        self._frame_timer.timeout.connect(self._poll_content_frame)
        self._render_failed_generation: int | None = None
        self._html_preparation = HtmlPreparation(self, pool=self._render_pool)
        self._html_preparation.ready.connect(self._on_html_prepared)
        self._html_files = self._html_preparation.files
        self._html_base_url = None
        self.setAcceptDrops(True)
        self._spy_timer = QTimer(self)
        self._spy_timer.setInterval(200)
        self._spy_timer.timeout.connect(self._poll_active_heading)

        self.bridge = AnnotationBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self.bridge)
        self._bind_document_page(self.page())
        self._annot_json = "[]"
        self._qwebchannel_js = self._read_resource(":/qtwebchannel/qwebchannel.js")
        self._annotations_js = (
            Path(__file__).parent.parent / "assets" / "annotations.js"
        ).read_text(encoding="utf-8")
        self._inline_edit_js = (
            Path(__file__).parent.parent / "assets" / "inline_edit.js"
        ).read_text(encoding="utf-8")
        self._table_edit_js = (
            Path(__file__).parent.parent / "assets" / "table_edit.js"
        ).read_text(encoding="utf-8")
        # Inline block editing is a preview-mode affordance; the window turns it
        # off whenever the text editor takes over the buffer.
        self._reading_media_js = (Path(__file__).resolve().parent.parent / "assets" / "reading_media.js").read_text(encoding="utf-8")
        self._inline_edit_enabled = True
        # v2 "click to edit": off by default so a RendererView the window never
        # explicitly opts in (e.g. the split-mode preview pane) never arms the
        # double-click-to-WYSIWYG gesture; see set_preview_double_click_mode.
        self._preview_dblclick_mode = "inline"
        self.show_empty()

    def _bind_document_page(self, page, previous=None):
        """Keep page configuration and QObject-side editing handlers across rotation."""
        if previous is not None:
            for attribute in QWebEngineSettings.WebAttribute:
                page.settings().setAttribute(attribute, previous.settings().testAttribute(attribute))
            for family in QWebEngineSettings.FontFamily:
                page.settings().setFontFamily(family, previous.settings().fontFamily(family))
            for size in QWebEngineSettings.FontSize:
                page.settings().setFontSize(size, previous.settings().fontSize(size))
            page.settings().setDefaultTextEncoding(previous.settings().defaultTextEncoding())
            for script in previous.scripts().toList():
                page.scripts().insert(script)
        else:
            # Both attributes are needed for the built-in plugin PDF viewer.
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
            page.settings().setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, True)
            # No preview feature opens windows; untrusted page script must not.
            page.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        page.setWebChannel(self._channel)

        def loaded(ok):
            if isValid(self) and page is self.page():
                self._on_page_load_finished(ok)

        def printed(path, ok):
            if isValid(self) and page is self.page():
                self._on_pdf_finished(path, ok)

        page.loadFinished.connect(loaded)
        page.pdfPrintingFinished.connect(printed)
        self._page_signal_bindings = (loaded, printed)

    def _rotate_document_page(self):
        """Release Chromium document/process state; history clearing is insufficient."""
        old = self.page()
        # In PySide 6.11 page.profile() parents its default-profile wrapper to
        # that page; deleting the page invalidates that wrapper, not the Qt
        # singleton. Retrieve the same profile through its factory each time.
        replacement = _DocumentPage(self, QWebEngineProfile.defaultProfile())
        loaded, printed = self._page_signal_bindings
        old.loadFinished.disconnect(loaded)
        old.pdfPrintingFinished.disconnect(printed)
        old.setWebChannel(None)
        old.triggerAction(QWebEnginePage.WebAction.Stop)
        self._bind_document_page(replacement, old)
        self._page_epoch += 1
        self.setPage(replacement)
        self.setZoomFactor(self._zoom_factor)
        self._page_navigation_count = 0
        self._page_rotation_count += 1
        # Custom pages are not reliably destroyed by setPage. Keep at most the
        # current page plus this one awaiting GUI-thread deferred deletion.
        if isValid(old):
            old.deleteLater()

    def _navigate_page(self, kind, payload, base_url=None):
        """Rotate only before new content, and never replace a page while printing."""
        self._navigation_serial += 1
        navigation = (self._navigation_serial, self._render_generation, kind, payload, base_url)
        if self._pdf_printing:
            self._deferred_navigation = navigation
            return
        self._submit_page_navigation(navigation)

    def _submit_page_navigation(self, navigation):
        serial, generation, kind, payload, base_url = navigation
        if serial != self._navigation_serial or generation != self._render_generation:
            return
        if (self._page_navigation_count >= _PAGE_NAVIGATION_LIMIT
                and generation != self._page_last_generation):
            self._rotate_document_page()
        self._page_navigation_count += 1
        self._page_last_generation = generation
        if kind == "url":
            self.page().load(payload)
        elif base_url is None:
            self.page().setHtml(payload)
        else:
            self.page().setHtml(payload, base_url)

    def _next_render_generation(self) -> int:
        self._inactive = False
        preparation = getattr(self, "_html_preparation", None)
        if preparation is not None:
            preparation.cancel()
        if hasattr(self, "_loading_shield"):
            self._finish_loading()
            self._frame_timer.stop()
            self._frame_request = None
        self._render_failed_generation = None
        previous = getattr(self, "_render_cancel", None)
        if previous is not None:
            previous.set()
        self._render_cancel = threading.Event()
        self._source_line_reveal = None
        self._loaded_markdown_generation = None
        self._partial_preview_active = False
        self._partial_scope_percent = 0
        self._partial_restore = None
        self._partial_user_navigated = False
        pending_export = getattr(self, "_pending_export", None)
        if pending_export is not None:
            # The document being exported is gone; never silently drop the
            # caller's completion callback.
            self._pending_export = None
            filepath, on_done, _layout = pending_export
            if on_done is not None:
                on_done(filepath, False)
        self._render_generation += 1
        return self._render_generation

    def deactivate(self):
        """Cancel this surface when another document or an editor takes over."""
        self._remember_reading_position()
        self._next_render_generation()
        self._inactive = True
        self._current_path = None
        self._pending_text_base_url = None
        self._pending_find = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_ratio = None
        self._current_anchor = ""
        self._spy_timer.stop()
        self.stop()

    @staticmethod
    def _reading_signature(path):
        try:
            stat = path.stat()
            return (str(path.resolve()), stat.st_mtime_ns, stat.st_size,
                    stat.st_dev, stat.st_ino)
        except OSError:
            return None

    def _remember_reading_position(self):
        if (self._loaded_markdown_generation != self._render_generation
                or not self._current_path or not self._scroll_anchor
                or self._document_signature is None):
            return
        key = str(self._current_path)
        self._reading_positions.pop(key, None)
        self._reading_positions[key] = (
            self._document_signature, self._scroll_y, dict(self._scroll_anchor)
        )
        while len(self._reading_positions) > 32:
            self._reading_positions.popitem(last=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_loading_shield"):
            self._loading_shield.setGeometry(self.rect())

    def _reveal_loading(self):
        if self._loading_generation == self._render_generation:
            self._loading_shield.reveal(self._theme)

    def _reveal_loading_details(self):
        if self._loading_generation == self._render_generation:
            self._loading_shield.reveal_details(self._theme)

    def _finish_loading(self):
        self._loading_timer.stop()
        self._loading_details_timer.stop()
        self._loading_generation = None
        had_focus = (self._loading_shield.hasFocus()
                     or self._loading_shield._open_button.hasFocus())
        self._loading_shield.hide()
        if had_focus:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            generation = self._render_generation
            _run_page_javascript(self, f"""(function() {{
                var meta = document.querySelector('meta[name="{_RENDER_GENERATION_META}"]');
                if (!meta || Number(meta.content) !== {generation} ||
                    window.__mdReadingInteracted || window.__mdSourceRevealRequest) return;
                var focused = document.activeElement;
                if (!focused || !focused.matches('input.task-list-item-checkbox')) return;
                document.body.setAttribute('tabindex', '-1');
                document.body.focus({{preventScroll: true}});
                if (window.__mdApplyReadingRestore) window.__mdApplyReadingRestore();
                else window.scrollTo(0, {self._scroll_y});
            }})();""")

    def _schedule_content_frame(self, generation: int, path: Path):
        self._frame_serial += 1
        serial = self._frame_serial
        self._frame_request = (generation, path, self._partial_preview_active, serial)
        phase = "partial" if self._partial_preview_active else "full"
        self._frame_polls = 0
        _run_page_javascript(self, f"""(function() {{
            requestAnimationFrame(function() {{ requestAnimationFrame(function() {{
                var m = document.querySelector('meta[name="{_RENDER_GENERATION_META}"]');
                var p = document.querySelector('meta[name="markdown-viewer-render-phase"]');
                if (m && Number(m.content) === {generation} && p && p.content === "{phase}")
                    window.__mdReadableFrame = {serial};
            }}); }});
        }})()""")
        self._frame_timer.start()

    def _poll_content_frame(self):
        request = self._frame_request
        if request is None:
            return
        generation, path, _partial, serial = request
        if self._stale_render(generation, path):
            self._frame_request = None
            return
        self._frame_polls += 1
        _run_page_javascript(self,
            "(function(){"
            f"var m=document.querySelector('meta[name=\"{_RENDER_GENERATION_META}\"]');"
            f"return !!(m && Number(m.content)==={generation} && "
            f"window.__mdReadableFrame==={serial} && document.body);"
            "})()",
            lambda ready, request=request: self._on_content_frame_checked(request, ready),
        )

    def _set_document_html(self, html: str, base_url: QUrl | None = None):
        """Avoid Chromium's 2 MiB encoded-data URL limit for large previews.

        A small page retains setHtml's lightweight path. Larger pages use an
        owned, auto-removed temporary file, with at most two files retained so
        replacing an in-flight navigation never removes its source too soon.
        Local asset permissions are unchanged; the base remains the source
        document directory. No user file is written or removed.
        """
        self._html_base_url = base_url
        self._has_deferred_code = 'id="md-code-payload"' in html
        preparation = getattr(self, "_html_preparation", None)
        if preparation is not None:
            preparation.cancel()
        # At most four UTF-8 bytes and three URL bytes per character. Checking
        # character count avoids encoding a large document on the GUI thread.
        if len(html) * 12 < 2 * 1024 * 1024 - 30:
            self._navigate_page("html", html, base_url)
            return
        self._html_preparation.prepare(
            html, base_url.toString() if base_url is not None else "", self._render_generation,
        )

    def _on_html_prepared(self, serial: int, generation: int, path: str, error: str):
        if (generation != self._render_generation
                or serial != self._html_preparation.serial
                or getattr(self, "_inactive", False)):
            return
        if error or not path:
            self._render_failed_generation = self._render_generation
            error = self._state_html("無法預覽 Markdown", "無法建立大型文件預覽暫存檔。", "錯誤")
            error = _html_with_render_generation(error, self._render_generation)
            error = error.replace("</head>", '<meta name="markdown-viewer-render-phase" content="full">'
                                  f'<meta name="{_RENDER_ERROR_META}" content="true"></head>', 1)
            self._navigate_page("html", error)
            return
        self._navigate_page("url", QUrl.fromLocalFile(path))

    def _on_content_frame_checked(self, request, ready):
        if request != self._frame_request or self._stale_render(request[0], request[1]):
            return
        if ready:
            self._frame_request = None
            self.content_ready.emit(request[0], str(request[1]), request[2])
        elif self._frame_polls < 120:
            # Hidden WebEngine views may not produce frames; polling is bounded.
            self._frame_timer.start()
        else:
            self._frame_request = None

    def contextMenuEvent(self, event):
        # annotations.js consumes the event itself when the click lands on an
        # existing highlight, so reaching here means a plain page selection.
        selection = (self.page().selectedText() or "").strip()
        menu = self._build_context_menu()
        if selection:
            menu.addSeparator()
            action = menu.addAction("翻譯選取內容")
            action.triggered.connect(
                lambda _checked=False, text=selection: (
                    self.translate_requested.emit(text)
                )
            )
        if not menu.actions():
            return
        menu.exec(event.globalPos())

    def _build_context_menu(self) -> QMenu:
        """The page's own menu when Qt has a request for it, else a minimal one.

        createStandardContextMenu() dereferences the pending context-menu
        request and segfaults when there is none (verified), which can happen
        if the event reaches us without the render process having filed one.
        """
        if self.lastContextMenuRequest() is not None:
            return self.createStandardContextMenu()
        menu = QMenu(self)
        copy_action = self.pageAction(QWebEnginePage.WebAction.Copy)
        if copy_action is not None:
            menu.addAction(copy_action)
        return menu

    def _on_page_load_finished(self, ok):
        if self._inactive:
            return
        if ok:
            # Workspace navigation owns the reading trail. This removes browser
            # back/forward entries; bounded page rotation releases Chromium state.
            self.history().clear()
            # Re-apply zoom: a freshly loaded page otherwise resets to 1.0.
            self.setZoomFactor(self._zoom_factor)
        js = (
            "(function(){"
            f"var m=document.querySelector('meta[name=\"{_RENDER_GENERATION_META}\"]');"
            "return m ? m.getAttribute('content') : null;"
            "})()"
        )
        if ok and self._current_path and is_markdown(self._current_path):
            js = (
                "(function(){"
                f"var m=document.querySelector('meta[name=\"{_RENDER_GENERATION_META}\"]');"
                "var p=document.querySelector('meta[name=\"markdown-viewer-render-phase\"]');"
                "return JSON.stringify({generation:m ? m.content : null,phase:p ? p.content : null});"
                "})()"
            )
            generation = self._render_generation
            path = self._current_path
            _run_page_javascript(self,
                js,
                lambda loaded_generation, generation=generation, path=path: (
                    self._on_markdown_load_checked(
                        generation, path, loaded_generation
                    )
                ),
            )
        elif ok and self._current_path is None and self._pending_ratio is not None:
            # Live-preview text render: restore the synced scroll ratio.
            self._spy_timer.stop()
            generation = self._render_generation
            _run_page_javascript(self,
                js,
                lambda loaded_generation, generation=generation: (
                    self._on_text_load_checked(generation, loaded_generation)
                ),
            )
        else:
            self._spy_timer.stop()
            if not ok:
                # stop()/replacement can also emit loadFinished(False). Only
                # release the shield once the active navigation is no longer
                # running; stale false completions must not expose the old page.
                generation, path = self._render_generation, self._current_path
                QTimer.singleShot(0, lambda: self._on_failed_navigation(generation, path))
            elif self._current_path is None:
                self._inject_annotations(True)

    def _on_failed_navigation(self, generation, path):
        if generation != self._render_generation or path != self._current_path:
            return
        if self.page().isLoading() or self._loading_generation != generation:
            return
        # The parser may still be working while the previous navigation stops.
        if getattr(self, "_submitted_generation", None) != generation:
            return
        self._finish_loading()
        self.render_failed.emit(generation, str(path or ""))

    def _on_markdown_load_checked(self, generation: int, path: Path, loaded_generation):
        if (
            generation != self._render_generation
            or path != self._current_path
            or not is_markdown(path)
        ):
            return

        if isinstance(loaded_generation, str) and loaded_generation.startswith("{"):
            try:
                loaded = json.loads(loaded_generation)
            except (ValueError, TypeError):
                return
            expected_phase = "partial" if self._partial_preview_active else "full"
            if loaded.get("phase") != expected_phase:
                return
            loaded_generation = loaded.get("generation")

        try:
            loaded_generation = int(loaded_generation)
        except (TypeError, ValueError):
            self._spy_timer.stop()
            return
        if loaded_generation != generation:
            self._spy_timer.stop()
            return

        self._loaded_markdown_generation = generation
        pending_export = getattr(self, "_pending_export", None)
        if pending_export is not None and not self._partial_preview_active:
            filepath, on_done, layout = pending_export
            self._pending_export = None
            async_repair, on_repair_failed = getattr(
                self, "_pdf_async_repair", (False, None)
            )
            if self._render_failed_generation == generation:
                if on_done is not None:
                    on_done(filepath, False)
            else:
                self.export_pdf(
                    filepath, on_done, layout, async_repair, on_repair_failed
                )
        target, self._pending_scroll, self._pending_scroll_generation = (
            _pending_scroll_target(
                self._pending_scroll,
                self._pending_scroll_generation,
                loaded_generation,
            )
        )
        if target is not None:
            _run_page_javascript(self,
                "if(window.__mdApplyReadingRestore) window.__mdApplyReadingRestore();"
                f"else window.scrollTo(0, {target});"
            )
        if self._pending_find and self._pending_find[0] == loaded_generation:
            _generation, text = self._pending_find
            self._pending_find = None
            self.find_text(text)
        if self._source_line_reveal and self._source_line_reveal[0] == generation:
            self._apply_source_line_reveal(self._source_line_reveal)
        self._spy_timer.start()
        self._finish_loading()
        if self._render_failed_generation == generation:
            self.render_failed.emit(generation, str(path))
        else:
            self._inject_annotations(True)
            self._schedule_content_frame(generation, path)

    def _on_text_load_checked(self, generation: int, loaded_generation):
        try:
            loaded_generation = int(loaded_generation)
        except (TypeError, ValueError):
            return
        if (
            loaded_generation != generation
            or generation != self._render_generation
            or self._current_path is not None
        ):
            return
        self._loaded_markdown_generation = generation
        self._spy_timer.start()
        if self._pending_ratio and self._pending_ratio[0] == loaded_generation:
            _generation, ratio = self._pending_ratio
            self._pending_ratio = None
            self.scroll_to_ratio(ratio)
        self._inject_annotations(True)

    def set_zoom(self, factor: float):
        self._zoom_factor = max(0.5, min(3.0, factor))
        self.setZoomFactor(self._zoom_factor)
        return self._zoom_factor

    def zoom(self) -> float:
        return self._zoom_factor

    def _state_html(self, title: str, message: str, label: str = "") -> str:
        return state_page_html(title, message, self._theme, label)

    def show_empty(self):
        self._next_render_generation()
        self._pending_text_base_url = None
        self._current_path = None
        self._current_anchor = ""
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_ratio = None
        self._spy_timer.stop()
        html = self._state_html(
            "接續你的閱讀與思考",
            "拖入 Markdown、文字或 PDF 文件，開始閱讀；需要寫作時，再切換編輯模式。",
            "你的文件工作台",
        )
        actions = _home_actions_html()
        self._set_document_html(html.replace("</main>", actions + "</main>"))
        if self._on_headings_ready:
            self._on_headings_ready([])

    def show_pending_recovery(self, path: str | Path):
        """Show a read-only holding page without opening or replacing a draft."""
        self._next_render_generation()
        self._pending_text_base_url = None
        self._current_path = None
        self._current_anchor = ""
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_ratio = None
        self._pending_find = None
        self._spy_timer.stop()
        self._set_document_html(self._state_html(
            "這份文件有待復原草稿",
            "請從「檔案 → 待復原草稿」選擇繼續編輯、另存副本或稍後處理。",
            Path(path).name,
        ))
        if self._on_headings_ready:
            self._on_headings_ready([])

    def show_loading(self, path: Path):
        self._current_anchor = ""
        self._spy_timer.stop()
        self._loading_generation = self._render_generation
        self._loading_shield.arm(path)
        _run_page_javascript(self,
            "window.__inlineEdit && window.__inlineEdit.setEnabled(false);"
        )
        self._loading_timer.start()
        self._loading_details_timer.start()

    def load_file(
        self,
        filepath: str | Path,
        scroll_y: int | None = None,
        show_loading: bool = True,
    ):
        path = Path(filepath)
        self._remember_reading_position()
        generation = self._next_render_generation()
        self._pending_find = None
        self._pending_ratio = None
        self._pending_text_base_url = None
        self._current_path = path
        self._document_signature = self._reading_signature(path)
        remembered = self._reading_positions.get(str(path))
        self._pending_reading_anchor = (
            remembered[2] if remembered and remembered[0] == self._document_signature
            and scroll_y is not None and abs(remembered[1] - scroll_y) <= 2 else None
        )
        self._scroll_anchor = None
        self._scroll_y = int(scroll_y) if scroll_y else 0
        # Restore a remembered scroll position once the page finishes loading.
        self._pending_scroll = int(scroll_y) if scroll_y else None
        self._pending_scroll_generation = generation if self._pending_scroll else None
        # Even an in-place reload blocks old-page edits while its replacement
        # is pending. The optional hint never creates a second WebEngine page.
        self.show_loading(path)
        if not show_loading:
            self._loading_timer.stop()
            self._loading_details_timer.stop()
        self._finish_load_file(path, generation)

    def _finish_load_file(self, path: Path, generation: int):
        if generation != self._render_generation or path != self._current_path:
            return

        if is_pdf(path):
            if self._on_headings_ready:
                self._on_headings_ready([])
            self._navigate_page("url", QUrl.fromLocalFile(str(path)))
            return

        worker = _MarkdownRenderWorker(generation, path=path, theme=self._theme,
                                       cancel=self._render_cancel)
        worker.signals.partial_ready.connect(self._on_file_partial_ready)
        worker.signals.ready.connect(self._on_file_render_ready)
        self._render_pool.start(worker, background_jobs.VISIBLE)

    def _stale_render(self, generation: int, source) -> bool:
        path = Path(source) if source is not None else None
        return (
            generation != self._render_generation
            or path is None
            or path != self._current_path
            or not is_markdown(path)
        )

    def is_partial_preview(self) -> bool:
        """True while only a labelled prefix of the document is on screen.

        Callers that need the whole document (export, print, anything that
        persists content) must not treat the current page as complete.
        """
        return self._partial_preview_active

    def _on_file_partial_ready(self, generation: int, source, html: str,
                               headings: list, percent: int = 0):
        """Show the fast-text-reading prefix while the full render continues."""
        if self._stale_render(generation, source):
            return
        path = Path(source)
        # Remember what the full page still has to satisfy: the partial page's
        # loadFinished would otherwise consume a pending scroll or search that
        # can only be answered correctly by the complete document.
        self._partial_restore = {
            "generation": generation,
            "pending_scroll": self._pending_scroll,
            "pending_scroll_generation": self._pending_scroll_generation,
            "pending_find": self._pending_find,
        }
        self._partial_preview_active = True
        self._partial_scope_percent = int(percent)
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        html = _html_with_render_generation(html, generation)
        html = html.replace("</head>", '<meta name="markdown-viewer-render-phase" content="partial"></head>', 1)
        html = _html_with_early_scroll(
            html, self._pending_scroll or 0, getattr(self, "_pending_reading_anchor", None)
        )
        self._frame_request = None
        self._submitted_generation = generation
        self._set_document_html(html, base_url)
        if self._on_headings_ready:
            self._on_headings_ready(headings)

    def _on_file_render_ready(self, generation: int, source, html: str, headings: list):
        if self._stale_render(generation, source):
            return
        path = Path(source)
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        html = _html_with_render_generation(html, generation)
        html = html.replace("</head>", '<meta name="markdown-viewer-render-phase" content="full"></head>', 1)
        self._frame_request = None
        self._render_failed_generation = (
            generation if f'name="{_RENDER_ERROR_META}"' in html else None
        )
        restore = self._partial_restore
        if restore is not None and restore["generation"] == generation:
            # Re-arm what the partial page consumed, and keep the reader where
            # they already scrolled to inside the prefix.
            self._pending_find = restore["pending_find"]
            if getattr(self, "_partial_user_navigated", False):
                self._pending_scroll = self._scroll_y
                self._pending_scroll_generation = generation
                self._pending_reading_anchor = getattr(self, "_scroll_anchor", None)
            elif restore["pending_scroll"] is not None:
                self._pending_scroll = restore["pending_scroll"]
                self._pending_scroll_generation = restore["pending_scroll_generation"]
            elif self._scroll_y > 0:
                self._pending_scroll = self._scroll_y
                self._pending_scroll_generation = generation
                self._pending_reading_anchor = getattr(self, "_scroll_anchor", None)
        self._partial_restore = None
        self._partial_preview_active = False
        self._partial_scope_percent = 0
        if (
            self._pending_scroll is not None
            and self._pending_scroll_generation == generation
        ):
            html = _html_with_early_scroll(
                html, self._pending_scroll, getattr(self, "_pending_reading_anchor", None)
            )
        self._submitted_generation = generation
        self._set_document_html(html, base_url)
        if self._on_headings_ready:
            self._on_headings_ready(headings)

    def reload_current(self):
        if not self._current_path:
            return
        # Preserve the scroll position across a reload (reload button, external
        # change, save) instead of jumping back to the top.
        if is_markdown(self._current_path):
            generation, path = self._render_generation, self._current_path
            _run_page_javascript(self,
                "window.scrollY",
                lambda scroll, generation=generation, path=path: (
                    self._reload_at_scroll(scroll, generation, path)
                ),
            )
        else:
            self.load_file(self._current_path)

    def _reload_at_scroll(self, scroll_y, generation=None, path=None):
        if generation is not None and (
            generation != self._render_generation or path != self._current_path
        ):
            return
        if self._current_path:
            self.load_file(
                self._current_path,
                scroll_y=int(scroll_y or 0),
                show_loading=False,
            )

    def render_html(self, html: str, base_url: QUrl | None = None):
        """Render a self-contained HTML string (used by the live edit preview).

        Does not set ``_current_path``, so annotation injection and heading
        scroll-spy stay off — this surface is a throwaway preview, not the
        annotatable document view.
        """
        self._next_render_generation()
        self._pending_text_base_url = None
        self._current_path = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_ratio = None
        self._set_document_html(html, base_url)

    def render_markdown_text(
        self,
        text: str,
        theme: str = "light",
        title: str = "preview",
        base_url: QUrl | None = None,
        scroll_ratio: float | None = None,
        defer_code: bool = True,
    ):
        """Render unsaved Markdown buffer text in the background.

        When *scroll_ratio* is given, the freshly loaded page is scrolled to
        that 0..1 ratio (used by the live preview so a debounced re-render
        keeps following the editor instead of jumping back to the top).
        """
        generation = self._next_render_generation()
        self._current_path = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_ratio = (
            (generation, float(scroll_ratio)) if scroll_ratio is not None else None
        )
        self._pending_text_base_url = base_url
        worker = _MarkdownRenderWorker(
            generation, text=text, theme=theme, title=title, cancel=self._render_cancel,
            defer_code=defer_code,
        )
        worker.signals.ready.connect(self._on_text_render_ready)
        self._render_pool.start(worker, background_jobs.VISIBLE)

    def _on_text_render_ready(self, generation: int, _source, html: str, _headings: list):
        if generation != self._render_generation or self._current_path is not None:
            return
        html = _html_with_render_generation(html, generation)
        base_url = self._pending_text_base_url
        self._pending_text_base_url = None
        self._set_document_html(html, base_url)

    def scroll_y(self) -> int:
        """Last polled vertical scroll position in px (0 when unknown)."""
        return self._scroll_y

    def scroll_to_source_position(self, line: int, fraction: float = 0.0):
        line = max(0, int(line) - 1)
        fraction = max(0.0, min(1.0, float(fraction)))
        generation = self._render_generation
        js = """(function(line, fraction, generation) {
            var meta = document.querySelector('meta[name="markdown-viewer-render-generation"]');
            if (!meta || Number(meta.content) !== generation) return false;
            var blocks = document.querySelectorAll('[data-src-start]');
            var chosen = null, span = Infinity;
            for (var block of blocks) {
                var start = Number(block.dataset.srcStart);
                var end = Number(block.dataset.srcEnd || start) + 1;
                if (start <= line && line < end && end - start < span) {
                    chosen = block; span = Math.max(1, end - start);
                }
            }
            if (!chosen) return false;
            var rect = chosen.getBoundingClientRect();
            var relative = Math.max(0, Math.min(1,
                (line - Number(chosen.dataset.srcStart) + fraction) / span));
            window.__mdReadingRestore = null;
            window.__mdSyncScrollUntil = performance.now() + 400;
            window.scrollTo(0, window.scrollY + rect.top + relative * rect.height);
            return true;
        })(%d, %s, %d)""" % (line, fraction, generation)
        _run_page_javascript(self, js)

    def scroll_to_ratio(self, ratio: float):
        ratio = max(0.0, min(1.0, ratio))
        js = (
            "(function(){"
            "window.__mdReadingRestore=null;"
            "var d=document.documentElement,b=document.body;"
            "var h=Math.max(d.scrollHeight,b.scrollHeight)-window.innerHeight;"
            f"window.scrollTo(0, h*{ratio});"
            "})()"
        )
        _run_page_javascript(self, js)

    @staticmethod
    def _read_resource(path: str) -> str:
        f = QFile(path)
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            data = bytes(f.readAll()).decode("utf-8")
            f.close()
            return data
        return ""

    def _inject_annotations(self, ok):
        if not ok or self._inactive:
            return
        if not self._current_path or not is_markdown(self._current_path):
            # Live/split previews deliberately skip annotation and inline-edit
            # scripts, but their WebEngine focus widget still swallows Escape.
            # Install only the tiny unhandled-key bridge on those pages.
            _run_page_javascript(self,
                "window.__mdSearchEscapeGeneration = %d;\n"
                % self._search_escape_generation
                + self._qwebchannel_js
                + "\n"
                + _UNHANDLED_ESCAPE_JS
                + "\n" + self._reading_media_js
            )
            return
        boot = ("window.mdCode && window.mdCode.ensureAll();" if self._annot_json != "[]" else "")
        boot += "window.__annotBoot(%s, %s, %s);" % (
            json.dumps(self._annot_json),
            json.dumps(self._side_notes_visible),
            json.dumps(self._inline_edit_enabled),
        )
        dblclick_boot = "window.__inlineEdit && window.__inlineEdit.setDoubleClickMode(%s);" % (
            json.dumps(self._preview_dblclick_mode)
        )
        _run_page_javascript(self,
            "(function(){"
            f"var m=document.querySelector('meta[name=\"{_RENDER_GENERATION_META}\"]');"
            f"if(!m || Number(m.content)!=={self._render_generation}) return;\n"
            + self._qwebchannel_js
            + "\n"
            + self._annotations_js
            + "\n" + self._reading_media_js
            + "\n"
            # Injected before inline_edit.js on purpose: opening a table
            # editor checks for window.__tableEdit as soon as it boots.
            + self._table_edit_js
            + "\n"
            + self._inline_edit_js
            + "\n"
            + dblclick_boot
            + "\n"
            + "window.__mdSearchEscapeGeneration = %d;\n"
            % self._search_escape_generation
            + boot
            + "\n})();"
        )

    def set_search_escape_generation(self, generation: int):
        """Arm unhandled-Escape reports for the current search generation."""
        self._search_escape_generation = max(0, int(generation))
        _run_page_javascript(self,
            "window.__mdSearchEscapeGeneration = %d;"
            % self._search_escape_generation
        )

    def set_inline_edit_enabled(self, enabled: bool):
        """Allow or forbid triple-click-to-edit in the rendered preview.

        Stored as well as pushed, so a page loaded later boots with the right
        state rather than defaulting back to enabled.
        """
        self._inline_edit_enabled = bool(enabled)
        if self._loaded_markdown_generation != self._render_generation:
            return
        _run_page_javascript(self,
            "window.__inlineEdit && window.__inlineEdit.setEnabled(%s)"
            % json.dumps(self._inline_edit_enabled)
        )

    def set_preview_double_click_mode(self, mode: str):
        """Arm/disarm the v2 double-click-to-WYSIWYG gesture.

        *mode* is one of ``edit_backend.PREVIEW_DOUBLE_CLICK_*`` -- kept as a
        plain string (not imported) so this module stays free of a hard
        dependency on ``edit_backend``; window.py owns normalizing it.
        Stored as well as pushed, mirroring ``set_inline_edit_enabled``, so a
        page loaded later boots with the right state.
        """
        self._preview_dblclick_mode = "wysiwyg" if mode == "wysiwyg" else "inline"
        if self._loaded_markdown_generation != self._render_generation:
            return
        _run_page_javascript(self,
            "window.__inlineEdit && window.__inlineEdit.setDoubleClickMode(%s)"
            % json.dumps(self._preview_dblclick_mode)
        )

    def set_annotations(self, annotations: list[dict]):
        self._annot_json = json.dumps(annotations, ensure_ascii=False)
        if self._loaded_markdown_generation != self._render_generation:
            return
        _run_page_javascript(self,
            ("window.mdCode && window.mdCode.ensureAll();" if annotations else "")
            + "window.__annot && window.__annot.render(%s)" % json.dumps(self._annot_json)
        )

    def remove_annotation(self, ann_id: str):
        _run_page_javascript(self,
            "window.__annot && window.__annot.remove(%s)" % json.dumps(ann_id)
        )

    def update_annotation_color(self, ann_id: str, color: str):
        _run_page_javascript(self,
            "window.__annot && window.__annot.updateColor(%s,%s)"
            % (json.dumps(ann_id), json.dumps(color))
        )

    def annotation_orphans(self, callback):
        """Read the current DOM, ignoring reports queued by a retired document."""
        generation = self._render_generation

        def checked(value):
            if generation != self._render_generation or not isinstance(value, str):
                return
            try:
                ids = json.loads(value)
            except (TypeError, ValueError):
                return
            if isinstance(ids, list) and all(isinstance(item, str) for item in ids):
                callback(ids)

        _run_page_javascript(self,
            "(() => {const m=document.querySelector('meta[name=\"%s\"]');"
            "if(!m || Number(m.content)!==%d || !window.__annot) return null;"
            "return JSON.stringify(window.__annot.orphanIds());})()"
            % (_RENDER_GENERATION_META, generation), checked)

    def scroll_to_annotation(self, ann_id: str, on_done=None):
        generation = self._render_generation

        def checked(value):
            if generation == self._render_generation and on_done is not None:
                on_done(value if isinstance(value, bool) else None)

        _run_page_javascript(self,
            "(() => {const m=document.querySelector('meta[name=\"%s\"]');"
            "if(!m || Number(m.content)!==%d || !window.__annot) return null;"
            "return window.__annot.scrollTo(%s);})()"
            % (_RENDER_GENERATION_META, generation, json.dumps(ann_id)),
            checked if on_done is not None else None,
        )

    def select_annotation(self, ann_id: str):
        _run_page_javascript(self,
            "window.__annot && window.__annot.select(%s)" % json.dumps(ann_id)
        )

    def set_annotation_side_notes_visible(self, visible: bool):
        self._side_notes_visible = bool(visible)
        _run_page_javascript(self,
            "window.__annot && window.__annot.setSideNotesVisible(%s)"
            % json.dumps(self._side_notes_visible)
        )

    def export_pdf(self, filepath: str | Path, on_done=None, layout=None,
                   async_repair=False, on_repair_failed=None):
        """Render the current page to a PDF. on_done(path, ok) fires when finished.

        layout is a QPageLayout; when omitted an A4 portrait layout is used.
        With async_repair the text-layer repair runs on a worker thread and
        on_done fires only after it; if it fails the unrepaired PDF is kept,
        on_repair_failed(path) is called, then on_done(path, True).  Without it
        (the headless CLI) the repair runs synchronously before on_done.
        """
        if getattr(self, "_pdf_printing", False):
            if on_done:
                on_done(str(filepath), False)
            return
        self._pdf_callback = on_done
        self._pdf_async_repair = (bool(async_repair), on_repair_failed)
        if self._partial_preview_active:
            # Fast text reading only ever shows a prefix; exporting it would
            # silently produce a truncated PDF. Hold the request until the full
            # preview has replaced it (_on_markdown_load_checked runs it).
            self._pending_export = (str(filepath), on_done, layout)
            return
        if layout is None:
            layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(12, 12, 12, 12),
                QPageLayout.Unit.Millimeter,
            )
        def prepared(ok):
            if not ok:
                if self._pdf_callback is on_done:
                    self._pdf_callback = None
                if on_done:
                    on_done(str(filepath), False)
                return
            self._pdf_printing = True
            try:
                self.page().printToPdf(str(filepath), layout)
            except (RuntimeError, TypeError):
                self._on_pdf_finished(str(filepath), False)

        self._prepare_diagrams(prepared)

    def _prepare_diagrams(self, callback):
        """Finish lazy graphs before printing or measuring a continuous page."""
        generation = self._render_generation
        deadline = time.monotonic() + 60
        timer = QTimer(self)
        timer.setInterval(40)
        state = {"done": False, "checking": False}

        def abandon(*_args):
            state["done"] = True

        def finish(ok):
            if state["done"]:
                return
            state["done"] = True
            timer.stop()
            self.destroyed.disconnect(abandon)
            timer.deleteLater()
            callback(ok)

        def checked(ready):
            state["checking"] = False
            if state["done"]:
                return
            if generation != self._render_generation:
                finish(False)
            elif ready is True:
                finish(True)

        def poll():
            if state["done"]:
                return
            if generation != self._render_generation or time.monotonic() >= deadline:
                finish(False)
                return
            if state["checking"]:
                return
            state["checking"] = True
            _run_page_javascript(self, """(function() {
                if (window.mdCode) window.mdCode.ensureAll();
                if (!window.mdDiagrams) return true;
                window.mdDiagrams.ensureAll();
                return window.mdDiagrams.status().complete &&
                    (!document.fonts || document.fonts.status !== 'loading');
            })()""", checked)

        self.destroyed.connect(abandon)
        timer.timeout.connect(poll)
        timer.start()
        poll()

    def content_size(self, callback):
        """Measure rendered content as [width_px, height_px] and pass it to callback."""
        js = (
            "(function() {"
            "  var d = document.documentElement, b = document.body;"
            "  return JSON.stringify(["
            "    Math.max(d.scrollWidth, b.scrollWidth, d.clientWidth),"
            "    Math.max(d.scrollHeight, b.scrollHeight)"
            "  ]);"
            "})()"
        )
        self._prepare_diagrams(lambda ok: _run_page_javascript(self,
            js, lambda value: callback(_decode_content_size(value))
        ) if ok else callback(None))

    def _on_pdf_finished(self, path: str, ok: bool):
        async_repair, on_repair_failed = getattr(
            self, "_pdf_async_repair", (False, None)
        )
        if ok and async_repair:
            # Stay in the "printing" state (no re-export, navigation deferred)
            # until the worker is done; the callback is delivered afterwards.
            def repaired(succeeded: bool):
                if not isValid(self):
                    return  # view destroyed meanwhile; the PDF is intact on disk
                try:
                    if not succeeded and on_repair_failed:
                        on_repair_failed(path)
                finally:
                    self._finish_pdf_export(path, True)

            try:
                repair_async(path, repaired)
            except Exception:
                log.warning("Could not start PDF text repair", exc_info=True)
                repaired(False)
            return
        self._finish_pdf_export(path, ok, repair=ok)

    def _finish_pdf_export(self, path: str, ok: bool, repair: bool = False):
        self._pdf_printing = False
        deferred, self._deferred_navigation = getattr(self, "_deferred_navigation", None), None
        callback = self._pdf_callback
        self._pdf_callback = None
        try:
            try:
                if repair:
                    # Chromium writes Kangxi radicals into ToUnicode for glyphs
                    # CJK fonts share with ideographs; fix copy/search text.
                    repair_radical_tounicode(path)
            finally:
                if callback:
                    callback(path, ok)
        finally:
            if deferred is not None and isValid(self):
                self._submit_page_navigation(deferred)

    def set_theme(self, theme: str):
        self._theme = "dark" if theme == "dark" else "light"
        if not self._current_path:
            self.show_empty()
            return
        if not is_markdown(self._current_path):
            return

        theme_class = f"theme-{self._theme}"
        mermaid_theme = "dark" if self._theme == "dark" else "default"
        js = f"""(function() {{
            var targets = [document.documentElement, document.body];
            for (var i = 0; i < targets.length; i++) {{
                var el = targets[i];
                if (!el) {{ continue; }}
                el.classList.remove('theme-light', 'theme-dark');
                el.classList.add({json.dumps(theme_class)});
            }}
            if (window.mdDiagrams) {{
                window.mdDiagrams.setTheme({json.dumps(mermaid_theme)});
            }} else if (window.mermaid) {{
                var blocks = document.querySelectorAll('.mermaid');
                for (var j = 0; j < blocks.length; j++) {{
                    var src = blocks[j].getAttribute('data-diagram');
                    if (src === null) {{ continue; }}
                    blocks[j].removeAttribute('data-processed');
                    blocks[j].textContent = src;
                }}
                window.mermaid.initialize({{ startOnLoad: false, theme: {json.dumps(mermaid_theme)} }});
                try {{ window.mermaid.run({{ querySelector: '.mermaid' }}); }} catch (e) {{}}
            }}
        }})()"""
        _run_page_javascript(self, js)

    def scroll_to(self, anchor: str):
        """Scroll the rendered page to the given anchor id."""
        self._cancel_source_line_reveal()
        anchor_json = json.dumps(anchor)
        js = f"""(function() {{
            window.__mdReadingRestore = null;
            var el = document.getElementById({anchor_json});
            if (!el) {{ return; }}
            var reduce = window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            el.scrollIntoView({{ behavior: reduce ? 'auto' : 'smooth', block: 'start' }});
        }})()"""
        _run_page_javascript(self, js)

    def find_text(self, text: str, result_callback=None):
        self._cancel_source_line_reveal()
        generation = self._render_generation
        epoch = getattr(self, "_page_epoch", 0)

        def finished(result):
            if (generation != self._render_generation
                    or epoch != getattr(self, "_page_epoch", 0)):
                return
            if not result and getattr(self, "_partial_preview_active", False):
                # Never fail silently on a partially loaded document: say what
                # is loaded and search again once the full preview arrives.
                self._notify_partial_search_miss(text)
            if result_callback is not None:
                result_callback(result)
            # A library result may request its source block while an earlier
            # findText is still running. Restore that block after Chromium's
            # find callback so it cannot leave us at the first identical match.
            request = self._source_line_reveal
            if request is not None and request[0] == generation:
                self._apply_source_line_reveal(request)

        # Passing resultCallback=None into PySide6 findText crashes the process.
        self._find_with_stable_code(text, QWebEnginePage.FindFlag(0), finished)

    def _find_with_stable_code(self, text, flags, callback=None):
        """Keep native match ranges stable while deferred syntax is present."""
        generation, epoch = self._render_generation, getattr(self, "_page_epoch", 0)

        def find(_ready=None):
            if (generation != self._render_generation
                    or epoch != getattr(self, "_page_epoch", 0)):
                return
            if callback is None:
                self.page().findText(text, flags)
            else:
                self.page().findText(text, flags, callback)

        if getattr(self, "_has_deferred_code", False):
            method = "freeze" if text else "resume"
            _run_page_javascript(self, f"window.mdCode && window.mdCode.{method}();", find)
        else:
            find()

    def _notify_partial_search_miss(self, text: str) -> bool:
        """Explain a "not found" that is really "not loaded yet".

        Returns True when the miss was caused by fast text reading, in which
        case the search is re-armed for the full document.
        """
        if not getattr(self, "_partial_preview_active", False) or not text:
            return False
        percent = getattr(self, "_partial_scope_percent", 0) or 0
        message = (
            f"找不到「{text}」。目前只載入這份文件的前 {percent}%"
            "（快速文字閱讀）；完整預覽載入完成後會自動再搜尋一次。"
        )
        _run_page_javascript(self,
            "(function(){var n=document.querySelector('.partial-preview-notice');"
            "if(!n){return;}var m=n.querySelector('.ppn-search');"
            "if(!m){m=document.createElement('span');m.className='ppn-search';"
            "n.appendChild(m);}m.textContent=" + json.dumps(message, ensure_ascii=False) + ";})()"
        )
        # Re-run the same search against the whole document when it loads.
        self._pending_find = (self._render_generation, text)
        self.partial_search_missed.emit(text, int(percent))
        return True

    def reveal_source_line_after_load(self, line_number: int):
        """Reveal a source block after this render loads, or immediately if ready.

        ``line_number`` is one-based. Source mapping identifies a block, not
        a selected character or an exact visual line within a paragraph.
        """
        try:
            line_number = int(line_number)
        except (TypeError, ValueError):
            return
        if line_number < 1:
            return
        self._pending_find = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._source_line_reveal = (self._render_generation, line_number)
        if self._loaded_markdown_generation == self._render_generation:
            request = self._source_line_reveal
            # Stop Chromium's native match navigation before scrolling to a
            # source block; a pending find may otherwise scroll after its reply.
            self.page().findText(
                "", QWebEnginePage.FindFlag(0),
                lambda _result: self._apply_source_line_reveal(request),
            )

    def _apply_source_line_reveal(self, request: tuple[int, int]):
        if request != self._source_line_reveal or request[0] != self._render_generation:
            return
        if self._loaded_markdown_generation != request[0]:
            return
        _run_page_javascript(self, _source_line_reveal_js(*request))

    def _cancel_source_line_reveal(self):
        if self._source_line_reveal is not None:
            _run_page_javascript(self, "window.__mdSourceRevealRequest = null;")
        self._source_line_reveal = None

    def find_text_after_load(self, text: str):
        """Repeat a search when the current Markdown generation is fully loaded."""
        self._cancel_source_line_reveal()
        self._pending_find = (self._render_generation, text) if text else None

    def cancel_pending_find(self):
        self._pending_find = None
        self._cancel_source_line_reveal()

    def find_next(self, text: str):
        self._cancel_source_line_reveal()
        self._find_with_stable_code(text, QWebEnginePage.FindFlag(0))

    def find_prev(self, text: str):
        self._cancel_source_line_reveal()
        from PySide6.QtWebEngineCore import QWebEnginePage

        self._find_with_stable_code(
            text, QWebEnginePage.FindFlag.FindBackward
        )

    def _poll_active_heading(self):
        generation, path = self._render_generation, self._current_path
        if self._loaded_markdown_generation != generation:
            return
        js = """(function() {
            var hs = document.querySelectorAll('h1[id],h2[id],h3[id],h4[id],h5[id],h6[id]');
            var active = '';
            for (var i = 0; i < hs.length; i++) {
                if (hs[i].getBoundingClientRect().top <= 80) { active = hs[i].id; }
                else { break; }
            }
            var reading = null, source = null;
            // Hit-test a few viewport points instead of scanning every source
            // block on each poll of a large document.
            var x = window.innerWidth / 2;
            for (var y of [40, 80, 140]) {
                var hit = document.elementFromPoint(x, y);
                var block = hit && hit.closest('[data-src-start]');
                if (block) {
                    var r = block.getBoundingClientRect();
                    reading = {line:Number(block.getAttribute('data-src-start')),offset:r.top};
                    var count = Math.max(1, Number(block.dataset.srcEnd) - reading.line + 1);
                    var units = Math.max(0, Math.min(count - 0.0001, -r.top / Math.max(1, r.height) * count));
                    source = [reading.line + Math.floor(units) + 1, units % 1];
                    break;
                }
            }
            var extent = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
            return JSON.stringify([active, window.scrollY, reading, !!window.__mdReadingInteracted,
                performance.now() >= (window.__mdSyncScrollUntil || 0) ? source : null,
                extent ? Math.max(0, Math.min(1, window.scrollY / extent)) : 1]);
        })()"""
        _run_page_javascript(self,
            js, lambda result, generation=generation, path=path: (
                self._on_spy_result(result, generation, path)
            ),
        )

    def _on_spy_result(self, result, generation=None, path=None):
        if generation is not None and (
            generation != self._render_generation or path != self._current_path
        ):
            return
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                return
        if isinstance(result, (list, tuple)) and 2 <= len(result) <= 6:
            anchor, scroll = result[:2]
            changed = self._scroll_y != int(scroll or 0)
            self._scroll_y = int(scroll or 0)
            if len(result) >= 3:
                self._scroll_anchor = result[2]
            if len(result) >= 4 and self._partial_preview_active:
                self._partial_user_navigated = bool(result[3])
            if changed and self._current_path is None and len(result) >= 5 and result[4]:
                self.source_position_changed.emit(int(result[4][0]), float(result[4][1]))
            if len(result) >= 6:
                try:
                    progress = float(result[5])
                except (TypeError, ValueError):
                    progress = float("nan")
                if math.isfinite(progress):
                    self.reading_progress_changed.emit(max(0.0, min(1.0, progress)))
        else:
            anchor = result
        anchor = anchor or ""
        if anchor != self._current_anchor:
            self._current_anchor = anchor
            self.active_anchor_changed.emit(anchor)
