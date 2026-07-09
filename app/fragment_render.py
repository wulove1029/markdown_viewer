"""Render single Mermaid / LaTeX-math fragments to PNG for the PPT export.

This is the only Qt-dependent piece of the PPTX pipeline and is imported lazily
by the export action — never by the pure ``pptx_export`` module or its tests.

Pipeline per fragment (one reused, never-shown ``QWebEnginePage``):

1. Build a minimal standalone HTML page holding just that fragment plus the
   bundled offline renderer (the same ``mermaid.min.js`` / KaTeX loaders the
   viewer uses), on a white background with zero body margin.
2. ``setHtml`` and wait for ``loadFinished`` (nested ``QEventLoop`` — the GUI
   loop is already running, so blocking here is fine and keeps the caller's
   ``image_provider(kind, source) -> png`` call ordinary and synchronous).
3. Poll until the rendered node (``.mermaid svg`` / ``.katex``) exists and web
   fonts are loaded, then measure its bounding box.
4. ``printToPdf`` (bytes callback) into a one-page PDF sized to that box — the
   page *is* the fragment, so there is no "tiny formula in a sea of white".
5. Rasterize the page with PyMuPDF at high DPI, tag the PNG's DPI so PowerPoint
   sizes it physically (a small formula stays small), and save it.

Every step has a watchdog; any failure returns ``None`` so the caller falls
back to the labelled source box. QtWebEngine cannot run headless (it segfaults
under offscreen), so this is verified by manual GUI runs only.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QMarginsF, QSizeF, QTimer, QUrl, QEventLoop
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

try:
    import pymupdf
except Exception:  # pragma: no cover - optional at runtime
    pymupdf = None

from .md_converter import _FULL_CSS, _katex_html, _mermaid_script

ASSETS_DIR = Path(__file__).parent.parent / "assets"

# The first fragment pays QtWebEngine's cold-start cost, so the load budget is
# generous; subsequent fragments reuse the warm engine.
_LOAD_MS = 20000
_RENDER_MS = {"mermaid": 12000, "math": 6000}
_MEASURE_MS = 2500
_PRINT_MS = 7000
_DPI = {"mermaid": 200, "math": 300}
_PAD_PX = 8
_MAX_PX = 4000


def _js_str(s: str) -> str:
    return json.dumps(s)


class FragmentRenderer:
    """Renders Mermaid/math fragments to PNG. ``provide`` is the image_provider."""

    def __init__(self, parent=None):
        self._page = None
        self._tmp = None
        self._cache: dict = {}
        self._fails: dict = {}
        self._busy = False
        self._warm = False
        self._n = 0
        try:
            self._page = QWebEnginePage(parent)
            settings = self._page.settings()
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
            )
            settings.setAttribute(
                QWebEngineSettings.WebAttribute.ShowScrollBars, False
            )
            self._base = QUrl.fromLocalFile(str(ASSETS_DIR) + "/")
            self._tmp = Path(tempfile.mkdtemp(prefix="mdv_pptx_"))
        except Exception:
            self.cleanup()  # tear down whatever was built before re-raising
            raise

    # ------------------------------------------------------------------
    def provide(self, kind: str, source: str):
        """image_provider(kind, source) -> png path or None."""
        if pymupdf is None or kind not in ("mermaid", "math"):
            return None
        key = (kind, source)
        if key in self._cache:
            return self._cache[key]
        if self._busy:
            return None
        self._busy = True
        try:
            path = self._render_one(kind, source)
        except Exception:
            path = None
        finally:
            self._busy = False
        if path is not None:
            self._cache[key] = path
        else:
            # Don't poison duplicates on a transient (e.g. cold-start) failure —
            # let a later identical fragment retry on the warm engine. Give up
            # (cache None) only after a couple of tries so a genuinely broken
            # fragment can't re-pay the full watchdog budget on every repeat.
            self._fails[key] = self._fails.get(key, 0) + 1
            if self._fails[key] >= 2:
                self._cache[key] = None
        return path

    def cleanup(self):
        if self._tmp is not None:
            try:
                shutil.rmtree(self._tmp, ignore_errors=True)
            except Exception:
                pass
        if self._page is not None:
            try:
                self._page.deleteLater()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _ensure_warm(self):
        # Pay QtWebEngine's cold-start cost on a trivial page once, so the first
        # *real* fragment renders on a warm engine instead of racing a watchdog.
        if self._warm:
            return
        self._warm = True
        self._run(
            lambda f, td: self._arm_load(
                "<!doctype html><html><body>&middot;</body></html>", f, td
            ),
            _LOAD_MS,
        )

    def _render_one(self, kind: str, source: str):
        self._ensure_warm()
        doc, sel = self._build_html(kind, source)
        if not self._run(lambda f, td: self._arm_load(doc, f, td), _LOAD_MS):
            return None
        if not self._run(lambda f, td: self._arm_ready(sel, f, td), _RENDER_MS[kind]):
            return None
        rect = self._run(lambda f, td: self._arm_measure(sel, f, td), _MEASURE_MS)
        if not rect or len(rect) < 2 or rect[0] <= 0 or rect[1] <= 0:
            return None
        layout = self._layout_for(rect)
        pdf = self._run(lambda f, td: self._arm_print(layout, f, td), _PRINT_MS)
        if not pdf:
            return None
        return self._rasterize(pdf, kind)

    def _build_html(self, kind: str, source: str):
        from html import escape

        esc = escape(source, quote=True)
        if kind == "mermaid":
            frag = f'<pre class="mermaid" data-diagram="{esc}">{esc}</pre>'
            loader = _mermaid_script("light")
            sel = ".mermaid svg"
        else:
            frag = f'<span class="math block">{esc}</span>'
            loader = _katex_html()
            sel = ".katex"
        # Mermaid 11 emits <svg width="100%" viewBox=...>, which measures to ~0
        # inside a shrink-to-fit wrapper. Pin the svg to its intrinsic viewBox
        # size before measuring so the page is sized correctly.
        normalize = "true" if kind == "mermaid" else "false"
        shim = (
            "<script>window.__renderDone=false;(function(){var NORM=%s;"
            "function norm(n){if(!NORM)return;try{var vb=n.viewBox&&n.viewBox.baseVal;"
            "if(vb&&vb.width){n.setAttribute('width',vb.width);"
            "n.setAttribute('height',vb.height);n.style.maxWidth='none';"
            "n.style.width=vb.width+'px';n.style.height=vb.height+'px';}}catch(e){}}"
            "function c(){var n=document.querySelector(%s);"
            "if(n){norm(n);(document.fonts?document.fonts.ready:Promise.resolve())"
            ".then(function(){window.__renderDone=true;});}"
            "else{setTimeout(c,30);}}c();})();</script>" % (normalize, _js_str(sel))
        )
        doc = (
            "<!doctype html><html><head><meta charset='utf-8'><style>"
            f"{_FULL_CSS}\n"
            "html,body{margin:0;padding:0;background:#fff;}"
            ".frag-wrap{display:inline-block;}"
            ".frag-wrap .mermaid,.frag-wrap .katex{margin:0;}"
            "</style></head><body>"
            f"<div class='frag-wrap'>{frag}</div>{loader}{shim}"
            "</body></html>"
        )
        return doc, sel

    # ---- nested-event-loop driver ----
    def _run(self, arm, timeout_ms):
        loop = QEventLoop()
        state = {"done": False, "value": None}
        teardowns = []

        def finish(value):
            if state["done"]:
                return
            state["done"] = True
            state["value"] = value
            for t in teardowns:
                try:
                    t()
                except Exception:
                    pass
            loop.quit()

        QTimer.singleShot(timeout_ms, lambda: finish(None))
        arm(finish, teardowns.append)
        loop.exec()
        return state["value"]

    def _arm_load(self, doc, finish, add_teardown):
        def on_loaded(ok):
            finish(bool(ok))

        self._page.loadFinished.connect(on_loaded)
        add_teardown(lambda: self._safe_disconnect(on_loaded))
        self._page.setHtml(doc, self._base)

    def _arm_ready(self, sel, finish, add_teardown):
        probe = (
            "(function(){var n=document.querySelector(%s);"
            "if(!n)return false;return window.__renderDone===true;})()"
            % _js_str(sel)
        )
        timer = QTimer()
        timer.setInterval(60)

        def tick():
            self._page.runJavaScript(probe, lambda v: finish(True) if v else None)

        timer.timeout.connect(tick)
        add_teardown(timer.stop)
        timer.start()

    def _arm_measure(self, sel, finish, add_teardown):
        js = (
            "(function(){var n=document.querySelector(%s);if(!n)return null;"
            "var r=n.getBoundingClientRect();"
            "return [r.width,r.height,r.left,r.top];})()" % _js_str(sel)
        )
        self._page.runJavaScript(js, lambda v: finish(v))

    def _arm_print(self, layout, finish, add_teardown):
        self._page.printToPdf(
            lambda ba: finish(bytes(ba) if ba and len(ba) else None), layout
        )

    def _safe_disconnect(self, slot):
        try:
            self._page.loadFinished.disconnect(slot)
        except Exception:
            pass

    # ---- sizing + rasterize ----
    def _layout_for(self, rect):
        right = rect[0] + max(0.0, rect[2] if len(rect) > 2 else 0.0)
        bottom = rect[1] + max(0.0, rect[3] if len(rect) > 3 else 0.0)
        w_pt = max(24.0, (math.ceil(right) + _PAD_PX) * 0.75)
        h_pt = max(16.0, (math.ceil(bottom) + _PAD_PX) * 0.75)
        size = QPageSize(QSizeF(w_pt, h_pt), QPageSize.Unit.Point)
        return QPageLayout(
            size,
            QPageLayout.Orientation.Portrait,
            QMarginsF(0, 0, 0, 0),
            QPageLayout.Unit.Point,
        )

    def _rasterize(self, pdf_bytes, kind):
        dpi = _DPI[kind]
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            page = doc[0]
            zoom = dpi / 72.0
            max_px = max(page.rect.width, page.rect.height) * zoom
            if max_px > _MAX_PX:
                zoom *= _MAX_PX / max_px
                dpi = max(1, int(round(72 * zoom)))
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            try:
                pix.set_dpi(dpi, dpi)  # so PowerPoint sizes the image physically
            except Exception:
                pass
            self._n += 1
            out = self._tmp / f"frag_{self._n}.png"
            pix.save(str(out))
            return str(out)
        finally:
            doc.close()
