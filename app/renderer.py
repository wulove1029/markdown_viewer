"""Right-side Markdown renderer (QWebEngineView wrapper)."""

import json
import urllib.parse
from pathlib import Path

from PyQt6.QtCore import (
    QFile,
    QIODevice,
    QMarginsF,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QDesktopServices, QPageLayout, QPageSize
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineSettings,
    QWebEngineUrlScheme,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .annotation_bridge import AnnotationBridge
from .file_types import document_kind, is_markdown, is_pdf, is_supported_document
from .md_converter import convert, convert_text, state_page_html

_RENDER_GENERATION_META = "markdown-viewer-render-generation"


def _html_with_render_generation(html: str, generation: int) -> str:
    marker = f'<meta name="{_RENDER_GENERATION_META}" content="{generation}">'
    if "</head>" in html:
        return html.replace("</head>", marker + "\n</head>", 1)
    return marker + html


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
if not QWebEngineUrlScheme.schemeByName(b"wikilink").name():
    _wikilink_scheme = QWebEngineUrlScheme(b"wikilink")
    _wikilink_scheme.setFlags(
        QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
    )
    QWebEngineUrlScheme.registerScheme(_wikilink_scheme)


class _DocumentPage(QWebEnginePage):
    """Intercept link clicks: wiki-links and cross-note links open in-app,
    external links open in the system browser, in-page anchors scroll."""

    def __init__(self, view):
        super().__init__(view)
        self._view = view

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        scheme = url.scheme()
        # The wikilink scheme is always ours — intercept it regardless of how
        # the navigation was triggered (real click, JS, etc.).
        if scheme == "wikilink":
            raw = url.toString()
            target = urllib.parse.unquote(raw.split(":", 1)[1]) if ":" in raw else ""
            if target:
                self._view.wikilink_clicked.emit(target)
            return False
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            # Same-document fragment (footnote/anchor) -> let it scroll.
            if url.matches(self.url(), QUrl.UrlFormattingOption.RemoveFragment):
                return True
            if scheme in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
                return False
            if scheme == "file":
                local = url.toLocalFile()
                if is_supported_document(local):
                    self._view.local_doc_clicked.emit(local)
                else:
                    QDesktopServices.openUrl(url)
                return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class _RenderSignals(QObject):
    ready = pyqtSignal(int, object, str, list)


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
    ):
        super().__init__()
        self.generation = generation
        self.path = path
        self.text = text
        self.theme = theme
        self.title = title
        self.signals = _RenderSignals()

    def run(self):
        try:
            if self.path is not None:
                html, headings = convert(self.path, self.theme)
                source = self.path
            else:
                html, headings = convert_text(self.text or "", self.theme, self.title)
                source = None
        except Exception as exc:
            label = self.path.name if self.path is not None else self.title
            html = state_page_html(
                "無法預覽 Markdown",
                f"渲染 {label} 時發生錯誤：{exc}",
                self.theme,
                "錯誤",
            )
            headings = []
            source = self.path
        self.signals.ready.emit(self.generation, source, html, headings)


class RendererView(QWebEngineView):
    active_anchor_changed = pyqtSignal(str)
    wikilink_clicked = pyqtSignal(str)
    local_doc_clicked = pyqtSignal(str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self.setPage(_DocumentPage(self))
        self._on_headings_ready = on_headings_ready
        self._current_anchor = ""
        self._current_path: Path | None = None
        self._theme = "light"
        self._side_notes_visible = False
        self._pdf_callback = None
        self._zoom_factor = 1.0
        self._pending_scroll: int | None = None
        self._pending_scroll_generation: int | None = None
        self._render_generation = 0
        self._render_pool = QThreadPool.globalInstance()
        self._pending_text_base_url: QUrl | None = None
        self._scroll_y = 0  # last polled vertical scroll (for per-tab restore)
        self.setAcceptDrops(True)
        # The built-in PDF viewer is plugin-based, so both attributes are
        # required; PdfViewerEnabled alone leaves the PDF blank / downloaded.
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PluginsEnabled, True
        )
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PdfViewerEnabled, True
        )
        self.page().pdfPrintingFinished.connect(self._on_pdf_finished)

        self._spy_timer = QTimer(self)
        self._spy_timer.setInterval(200)
        self._spy_timer.timeout.connect(self._poll_active_heading)
        self.page().loadFinished.connect(self._on_page_load_finished)

        self.bridge = AnnotationBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self.bridge)
        self.page().setWebChannel(self._channel)
        self._annot_json = "[]"
        self._qwebchannel_js = self._read_resource(":/qtwebchannel/qwebchannel.js")
        self._annotations_js = (
            Path(__file__).parent.parent / "assets" / "annotations.js"
        ).read_text(encoding="utf-8")
        self.page().loadFinished.connect(self._inject_annotations)

        self.show_empty()

    def _next_render_generation(self) -> int:
        self._render_generation += 1
        return self._render_generation

    def _on_page_load_finished(self, ok):
        if ok:
            # Re-apply zoom: a freshly loaded page otherwise resets to 1.0.
            self.setZoomFactor(self._zoom_factor)
        if ok and self._current_path and is_markdown(self._current_path):
            generation = self._render_generation
            path = self._current_path
            js = (
                "(function(){"
                f"var m=document.querySelector('meta[name=\"{_RENDER_GENERATION_META}\"]');"
                "return m ? m.getAttribute('content') : null;"
                "})()"
            )
            self.page().runJavaScript(
                js,
                lambda loaded_generation, generation=generation, path=path: (
                    self._on_markdown_load_checked(
                        generation, path, loaded_generation
                    )
                ),
            )
        else:
            self._spy_timer.stop()

    def _on_markdown_load_checked(self, generation: int, path: Path, loaded_generation):
        if (
            generation != self._render_generation
            or path != self._current_path
            or not is_markdown(path)
        ):
            return

        try:
            loaded_generation = int(loaded_generation)
        except (TypeError, ValueError):
            self._spy_timer.stop()
            return
        if loaded_generation != generation:
            self._spy_timer.stop()
            return

        target, self._pending_scroll, self._pending_scroll_generation = (
            _pending_scroll_target(
                self._pending_scroll,
                self._pending_scroll_generation,
                loaded_generation,
            )
        )
        if target is not None:
            self.page().runJavaScript(f"window.scrollTo(0, {target})")
        self._spy_timer.start()

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
        self._spy_timer.stop()
        self.setHtml(
            self._state_html(
                "開啟文件",
                "拖放 Markdown 或 PDF 檔案到視窗，或使用開啟按鈕選擇檔案。",
                "尚未載入",
            )
        )
        if self._on_headings_ready:
            self._on_headings_ready([])

    def show_loading(self, path: Path):
        self._current_anchor = ""
        self._spy_timer.stop()
        kind = document_kind(path)
        label = "PDF" if kind == "pdf" else "Markdown"
        self.setHtml(
            self._state_html(
                f"正在載入 {label}",
                f"正在讀取：{path.name}",
                "載入中",
            )
        )

    def load_file(self, filepath: str | Path, scroll_y: int | None = None):
        path = Path(filepath)
        generation = self._next_render_generation()
        self._pending_text_base_url = None
        self._current_path = path
        self._scroll_y = 0
        # Restore a remembered scroll position once the page finishes loading.
        self._pending_scroll = int(scroll_y) if scroll_y else None
        self._pending_scroll_generation = generation if self._pending_scroll else None
        self.show_loading(path)
        self._finish_load_file(path, generation)

    def _finish_load_file(self, path: Path, generation: int):
        if generation != self._render_generation or path != self._current_path:
            return

        if is_pdf(path):
            if self._on_headings_ready:
                self._on_headings_ready([])
            self.load(QUrl.fromLocalFile(str(path)))
            return

        worker = _MarkdownRenderWorker(generation, path=path, theme=self._theme)
        worker.signals.ready.connect(self._on_file_render_ready)
        self._render_pool.start(worker)

    def _on_file_render_ready(self, generation: int, source, html: str, headings: list):
        path = Path(source) if source is not None else None
        if (
            generation != self._render_generation
            or path is None
            or path != self._current_path
            or not is_markdown(path)
        ):
            return
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        html = _html_with_render_generation(html, generation)
        self.page().setHtml(html, base_url)
        if self._on_headings_ready:
            self._on_headings_ready(headings)

    def reload_current(self):
        if not self._current_path:
            return
        # Preserve the scroll position across a reload (reload button, external
        # change, save) instead of jumping back to the top.
        if is_markdown(self._current_path):
            self.page().runJavaScript("window.scrollY", self._reload_at_scroll)
        else:
            self.load_file(self._current_path)

    def _reload_at_scroll(self, scroll_y):
        if self._current_path:
            self.load_file(self._current_path, scroll_y=int(scroll_y or 0))

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
        if base_url is not None:
            self.page().setHtml(html, base_url)
        else:
            self.page().setHtml(html)

    def render_markdown_text(
        self,
        text: str,
        theme: str = "light",
        title: str = "preview",
        base_url: QUrl | None = None,
    ):
        """Render unsaved Markdown buffer text in the background."""
        generation = self._next_render_generation()
        self._current_path = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self._pending_text_base_url = base_url
        worker = _MarkdownRenderWorker(
            generation, text=text, theme=theme, title=title
        )
        worker.signals.ready.connect(self._on_text_render_ready)
        self._render_pool.start(worker)

    def _on_text_render_ready(self, generation: int, _source, html: str, _headings: list):
        if generation != self._render_generation or self._current_path is not None:
            return
        base_url = self._pending_text_base_url
        self._pending_text_base_url = None
        if base_url is not None:
            self.page().setHtml(html, base_url)
        else:
            self.page().setHtml(html)

    def scroll_y(self) -> int:
        """Last polled vertical scroll position in px (0 when unknown)."""
        return self._scroll_y

    def scroll_to_ratio(self, ratio: float):
        ratio = max(0.0, min(1.0, ratio))
        js = (
            "(function(){"
            "var d=document.documentElement,b=document.body;"
            "var h=Math.max(d.scrollHeight,b.scrollHeight)-window.innerHeight;"
            f"window.scrollTo(0, h*{ratio});"
            "})()"
        )
        self.page().runJavaScript(js)

    @staticmethod
    def _read_resource(path: str) -> str:
        f = QFile(path)
        if f.open(QIODevice.OpenModeFlag.ReadOnly):
            data = bytes(f.readAll()).decode("utf-8")
            f.close()
            return data
        return ""

    def _inject_annotations(self, ok):
        if not ok or not self._current_path or not is_markdown(self._current_path):
            return
        boot = "window.__annotBoot(%s, %s);" % (
            json.dumps(self._annot_json),
            json.dumps(self._side_notes_visible),
        )
        self.page().runJavaScript(
            self._qwebchannel_js + "\n" + self._annotations_js + "\n" + boot
        )

    def set_annotations(self, annotations: list[dict]):
        self._annot_json = json.dumps(annotations, ensure_ascii=False)
        self.page().runJavaScript(
            "window.__annot && window.__annot.render(%s)" % json.dumps(self._annot_json)
        )

    def remove_annotation(self, ann_id: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.remove(%s)" % json.dumps(ann_id)
        )

    def update_annotation_color(self, ann_id: str, color: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.updateColor(%s,%s)"
            % (json.dumps(ann_id), json.dumps(color))
        )

    def scroll_to_annotation(self, ann_id: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.scrollTo(%s)" % json.dumps(ann_id)
        )

    def select_annotation(self, ann_id: str):
        self.page().runJavaScript(
            "window.__annot && window.__annot.select(%s)" % json.dumps(ann_id)
        )

    def set_annotation_side_notes_visible(self, visible: bool):
        self._side_notes_visible = bool(visible)
        self.page().runJavaScript(
            "window.__annot && window.__annot.setSideNotesVisible(%s)"
            % json.dumps(self._side_notes_visible)
        )

    def export_pdf(self, filepath: str | Path, on_done=None, layout=None):
        """Render the current page to a PDF. on_done(path, ok) fires when finished.

        layout is a QPageLayout; when omitted an A4 portrait layout is used.
        """
        self._pdf_callback = on_done
        if layout is None:
            layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Portrait,
                QMarginsF(12, 12, 12, 12),
                QPageLayout.Unit.Millimeter,
            )
        self.page().printToPdf(str(filepath), layout)

    def content_size(self, callback):
        """Measure rendered content as [width_px, height_px] and pass it to callback."""
        js = (
            "(function() {"
            "  var d = document.documentElement, b = document.body;"
            "  return ["
            "    Math.max(d.scrollWidth, b.scrollWidth, d.clientWidth),"
            "    Math.max(d.scrollHeight, b.scrollHeight)"
            "  ];"
            "})()"
        )
        self.page().runJavaScript(js, callback)

    def _on_pdf_finished(self, path: str, ok: bool):
        callback = self._pdf_callback
        self._pdf_callback = None
        if callback:
            callback(path, ok)

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
            if (window.mermaid) {{
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
        self.page().runJavaScript(js)

    def scroll_to(self, anchor: str):
        """Scroll the rendered page to the given anchor id."""
        anchor_json = json.dumps(anchor)
        js = f"""(function() {{
            var el = document.getElementById({anchor_json});
            if (!el) {{ return; }}
            var reduce = window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            el.scrollIntoView({{ behavior: reduce ? 'auto' : 'smooth', block: 'start' }});
        }})()"""
        self.page().runJavaScript(js)

    def find_text(self, text: str, result_callback=None):
        # Passing resultCallback=None into PyQt6 findText crashes the process.
        if result_callback is None:
            self.page().findText(text)
        else:
            self.page().findText(text, resultCallback=result_callback)

    def find_next(self, text: str):
        self.page().findText(text)

    def find_prev(self, text: str):
        from PyQt6.QtWebEngineCore import QWebEnginePage

        self.page().findText(
            text, QWebEnginePage.FindFlag.FindBackward
        )

    def _poll_active_heading(self):
        js = """(function() {
            var hs = document.querySelectorAll('h1[id],h2[id],h3[id],h4[id],h5[id],h6[id]');
            var active = '';
            for (var i = 0; i < hs.length; i++) {
                if (hs[i].getBoundingClientRect().top <= 80) { active = hs[i].id; }
                else { break; }
            }
            return [active, window.scrollY];
        })()"""
        self.page().runJavaScript(js, self._on_spy_result)

    def _on_spy_result(self, result):
        if isinstance(result, (list, tuple)) and len(result) == 2:
            anchor, scroll = result
            self._scroll_y = int(scroll or 0)
        else:
            anchor = result
        anchor = anchor or ""
        if anchor != self._current_anchor:
            self._current_anchor = anchor
            self.active_anchor_changed.emit(anchor)
