"""Right-side Markdown renderer (QWebEngineView wrapper)."""

from pathlib import Path
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl, QTimer, pyqtSignal
from .md_converter import convert

_PLACEHOLDER = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       display: flex; align-items: center; justify-content: center;
       height: 100vh; margin: 0; background: #f6f8fa; color: #656d76; }
p { font-size: 15px; }
</style></head>
<body><p>請從左側選擇一個 Markdown 檔案</p></body></html>"""


class RendererView(QWebEngineView):
    active_anchor_changed = pyqtSignal(str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self._on_headings_ready = on_headings_ready
        self._current_anchor = ""
        self.setAcceptDrops(True)

        self._spy_timer = QTimer(self)
        self._spy_timer.setInterval(200)
        self._spy_timer.timeout.connect(self._poll_active_heading)
        self.page().loadFinished.connect(lambda _: self._spy_timer.start())

        self._show_placeholder()

    def _show_placeholder(self):
        self._spy_timer.stop()
        self.setHtml(_PLACEHOLDER)
        if self._on_headings_ready:
            self._on_headings_ready([])

    def load_file(self, filepath: str | Path):
        path = Path(filepath)
        html, headings = convert(path)
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        self.page().setHtml(html, base_url)
        if self._on_headings_ready:
            self._on_headings_ready(headings)

    def scroll_to(self, anchor: str):
        """Scroll the rendered page to the given anchor id."""
        js = f'document.getElementById("{anchor}")?.scrollIntoView({{behavior:"smooth",block:"start"}})'
        self.page().runJavaScript(js)

    def find_text(self, text: str):
        from PyQt6.QtWebEngineCore import QWebEnginePage
        self.page().findText(text)

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
            return active;
        })()"""
        self.page().runJavaScript(js, self._on_spy_result)

    def _on_spy_result(self, anchor):
        anchor = anchor or ""
        if anchor != self._current_anchor:
            self._current_anchor = anchor
            self.active_anchor_changed.emit(anchor)
