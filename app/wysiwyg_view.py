"""WYSIWYG Markdown editor backend: a QWebEngineView hosting Vditor 3.11.3.

Only used by the EDIT/SPLIT view modes when the user has opted into the
WYSIWYG edit backend (see ``app/edit_backend.py``); the PREVIEW-mode
rendering pipeline (``app/renderer.py``) is completely untouched.

Data-safety contract (see the WYSIWYG spec's "shadow document push model"):
this widget never owns the save-worthy truth.  It only ever (a) receives a
full Markdown load from Python via :meth:`load_markdown`, or (b) reports a
full Markdown snapshot back via :attr:`content_changed`, debounced ~250ms by
``assets/vditor_glue.js``.  ``window.py`` is responsible for writing that
snapshot into the active tab's ``QTextDocument`` and marking it modified.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

ASSETS_DIR = Path(__file__).parent.parent / "assets"

_LANG_MAP = {"zh_TW": "zh_TW", "en_US": "en_US"}


def _read_resource(path: str) -> str:
    """Read a Qt resource (``:/...``) as UTF-8 text, or "" if unavailable."""
    f = QFile(path)
    if f.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(f.readAll()).decode("utf-8")
        f.close()
        return data
    return ""


class _WysiwygBridge(QObject):
    """QWebChannel object registered as ``wysiwygBridge`` (never "bridge" --

    RendererView already occupies that name; see renderer.py:274).

    The Signal/Slot names below are deliberately distinct from the page's
    JS-facing method names (``contentChanged``/``saveRequested``/``ready``,
    called by assets/vditor_glue.js): a Signal and a @Slot cannot share one
    name on the same QObject in PySide6.
    """

    contentPushed = Signal(str)
    saveRequestedSig = Signal()
    viewReadySig = Signal()
    escRequestedSig = Signal()
    toolbarActionSig = Signal(str)
    contextMenuRequestedSig = Signal(int, int)

    @Slot(str)
    def contentChanged(self, markdown: str) -> None:
        self.contentPushed.emit(markdown)

    @Slot()
    def saveRequested(self) -> None:
        self.saveRequestedSig.emit()

    @Slot()
    def ready(self) -> None:
        self.viewReadySig.emit()

    @Slot()
    def escRequested(self) -> None:
        self.escRequestedSig.emit()

    @Slot(str)
    def toolbarAction(self, name: str) -> None:
        self.toolbarActionSig.emit(name)

    @Slot(int, int)
    def contextMenuRequested(self, x: int, y: int) -> None:
        self.contextMenuRequestedSig.emit(x, y)


class WysiwygView(QWebEngineView):
    """Embeds Vditor in WYSIWYG mode and bridges it to Python via QWebChannel."""

    content_changed = Signal(str)
    save_requested = Signal()
    view_ready = Signal()
    # Esc inside Vditor, once assets/vditor_glue.js has confirmed no hint
    # panel ate it first (see its isHintPanelOpen()). window.py leaves
    # WYSIWYG back to PREVIEW on this -- no save/discard confirmation, the
    # dirty buffer just stays parked in tab_state (see the v2 spec).
    esc_requested = Signal()
    # v4: custom toolbar button click (name in {"save", "export_pdf",
    # "export_docx", "export_html", "insert_image", "toggle_theme"}).
    toolbar_action = Signal(str)
    # v4: right-click inside the Vditor surface; (x, y) are viewport-relative
    # pixels from the JS "contextmenu" event, which map 1:1 onto this
    # widget's local coordinates (no scroll offset -- Vditor's own toolbar
    # is pinned, not the page).
    context_menu_requested = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        page = QWebEnginePage(self)
        settings = page.settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self.setPage(page)

        self._bridge = _WysiwygBridge(self)
        self._bridge.contentPushed.connect(self.content_changed)
        self._bridge.saveRequestedSig.connect(self.save_requested)
        self._bridge.viewReadySig.connect(self._on_view_ready)
        self._bridge.escRequestedSig.connect(self.esc_requested)
        self._bridge.toolbarActionSig.connect(self.toolbar_action)
        self._bridge.contextMenuRequestedSig.connect(self.context_menu_requested)

        self._channel = QWebChannel(self)
        self._channel.registerObject("wysiwygBridge", self._bridge)
        page.setWebChannel(self._channel)

        self._base_url = QUrl.fromLocalFile(str(ASSETS_DIR) + "/")
        self._ready = False
        self._pending_markdown: str | None = None
        # Set while Python is pushing content into Vditor, so the resulting
        # echo (see vditor_glue.js's setValue guard) is not mistaken for a
        # push worth re-checking here; kept for parity/debugging only, the
        # real guard lives in JS.
        self._loading = False

        self._load_html(self._host_html(lang="zh_TW"))

    # ------------------------------------------------------------------
    def _host_html(self, *, lang: str) -> str:
        template = (ASSETS_DIR / "vditor_host.html").read_text(encoding="utf-8")
        qwebchannel_js = _read_resource(":/qtwebchannel/qwebchannel.js")
        html = template.replace("/*__WYSIWYG_QWEBCHANNEL_JS__*/", qwebchannel_js)
        html = html.replace(
            "__WYSIWYG_LANG__", _LANG_MAP.get(lang, "zh_TW")
        )
        return html

    def _load_html(self, html: str) -> None:
        # Deliberately not named ``load``: QWebEngineView.load(QUrl) is a
        # real Qt slot with its own overloads, and shadowing it with a
        # str-taking method here would hide that API from this subclass.
        self.page().setHtml(html, self._base_url)

    def _on_view_ready(self) -> None:
        self._ready = True
        self.view_ready.emit()
        if self._pending_markdown is not None:
            self._push_markdown(self._pending_markdown)
            self._pending_markdown = None

    # ---- public API ----------------------------------------------------
    def load_markdown(self, text: str) -> None:
        """Push *text* into Vditor, overwriting whatever it currently holds.

        Safe to call before the page has finished booting: the value is
        queued and flushed once ``ready()`` arrives.
        """
        if not self._ready:
            self._pending_markdown = text
            return
        self._push_markdown(text)

    def _push_markdown(self, text: str) -> None:
        self._loading = True
        js = (
            "window.__wysiwygGlue && window.__wysiwygGlue.setValue(%s);"
            % json.dumps(text)
        )
        self.page().runJavaScript(js, lambda _result: setattr(self, "_loading", False))

    def insert_value(self, text: str) -> None:
        """Insert *text* at the caret (v4: image/attachment link insertion)."""
        js = (
            "window.__wysiwygGlue && window.__wysiwygGlue.insertValue(%s);"
            % json.dumps(text)
        )
        self.page().runJavaScript(js)

    def get_html(self, callback) -> None:
        """Fetch Vditor's rendered HTML (``getHTML()``) asynchronously.

        *callback* receives a ``str`` on success or ``None`` if Vditor is not
        booted yet; used by the v4 HTML export entry point.
        """
        js = (
            "(function(){var v=window.__wysiwygGlue && "
            "window.__wysiwygGlue._state.vditor;"
            "return v && v.getHTML ? v.getHTML() : null;})();"
        )
        self.page().runJavaScript(js, callback)

    def flush_pending_edits(self) -> None:
        """Force any debounced-but-unsent edit to push immediately (pre-save)."""
        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.flushPending();"
        )

    def focus_near_text(self, snippet: str) -> None:
        """Best-effort: place the caret near *snippet* (v2 double-click-to-edit).

        Uses Chromium's non-standard ``window.find`` to locate and select the
        text, which for a focused contenteditable also parks the caret there.
        Purely a convenience -- the v2 spec accepts silent failure (falling
        back to wherever Vditor's own initial caret lands) over raising.
        """
        if not snippet:
            return
        js = (
            "(function(){try{if (window.find) { window.find(%s); }}"
            "catch(e){}})();" % json.dumps(snippet)
        )
        self.page().runJavaScript(js)

    def apply_theme(self, theme) -> None:
        """Best-effort Vditor theme sync; failures are non-fatal (cosmetic only)."""
        name = getattr(theme, "name", theme)
        vditor_theme = "dark" if name == "dark" else "classic"
        content_theme = "dark" if name == "dark" else "light"
        js = (
            "(function(){var v=window.__wysiwygGlue && window.__wysiwygGlue._state.vditor;"
            "if(v && v.setTheme){try{v.setTheme(%s,%s,%s);}catch(e){}}})();"
            % (json.dumps(vditor_theme), json.dumps(content_theme), json.dumps(content_theme))
        )
        self.page().runJavaScript(js)
