"""Right-side Markdown renderer (QWebEngineView wrapper)."""

import json
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
from PyQt6.QtWebEngineWidgets import QWebEngineView

from .md_converter import convert, state_page_html


class RendererView(QWebEngineView):
    active_anchor_changed = pyqtSignal(str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self._on_headings_ready = on_headings_ready
        self._current_anchor = ""
        self._current_path: Path | None = None
        self._theme = "light"
        self.setAcceptDrops(True)

        self._spy_timer = QTimer(self)
        self._spy_timer.setInterval(200)
        self._spy_timer.timeout.connect(self._poll_active_heading)
        self.page().loadFinished.connect(lambda _: self._spy_timer.start())

        self.show_empty()

    def _state_html(self, title: str, message: str, label: str = "") -> str:
        return state_page_html(title, message, self._theme, label)

    def show_empty(self):
        self._current_path = None
        self._current_anchor = ""
        self._spy_timer.stop()
        self.setHtml(
            self._state_html(
                "開啟 Markdown 檔案",
                "拖放 Markdown 檔案到視窗，或使用開啟按鈕選擇檔案。",
                "尚未載入",
            )
        )
        if self._on_headings_ready:
            self._on_headings_ready([])

    def show_loading(self, path: Path):
        self._current_anchor = ""
        self._spy_timer.stop()
        self.setHtml(
            self._state_html(
                "正在載入 Markdown",
                f"正在讀取並轉換：{path.name}",
                "載入中",
            )
        )

    def load_file(self, filepath: str | Path):
        path = Path(filepath)
        self._current_path = path
        self.show_loading(path)
        QTimer.singleShot(0, lambda path=path: self._finish_load_file(path))

    def _finish_load_file(self, path: Path):
        if path != self._current_path:
            return

        html, headings = convert(path, self._theme)
        base_url = QUrl.fromLocalFile(str(path.parent) + "/")
        self.page().setHtml(html, base_url)
        if self._on_headings_ready:
            self._on_headings_ready(headings)

    def reload_current(self):
        if self._current_path:
            self.load_file(self._current_path)

    def set_theme(self, theme: str):
        self._theme = "dark" if theme == "dark" else "light"
        if self._current_path:
            self.reload_current()
        else:
            self.show_empty()

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

    def find_text(self, text: str):
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
