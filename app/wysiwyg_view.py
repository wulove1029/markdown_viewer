"""WYSIWYG backend hosting the Office Viewer 4.2 Vditor fork.

Only used by the EDIT/SPLIT view modes when the user has opted into the
WYSIWYG edit backend (see ``app/edit_backend.py``); the PREVIEW-mode
rendering pipeline (``app/renderer.py``) is completely untouched.

Data-safety contract: normal typing is mirrored into the active tab's hidden
``QTextDocument`` as a small UTF-16 delta after a short idle window.  Save,
tab, export, backend-switch and close transitions instead take an explicit
asynchronous snapshot and acknowledge it before moving on.  The visible web
editor owns undo/redo while it is active; the Qt document remains the durable
save/recovery buffer.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from PySide6.QtCore import (
    QFile,
    QIODevice,
    QObject,
    QSettings,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .text_positions import py_to_qt_position, qt_to_py_position
from .theme import get_theme

ASSETS_DIR = Path(__file__).parent.parent / "assets"

_LANG_MAP = {"zh_TW": "zh_TW", "en_US": "en_US"}

_SETTINGS_ORG = "markdown-viewer"
_SETTINGS_APP = "MarkdownViewer"
_EDITOR_THEME_KEY = "wysiwyg_editor_theme"
_CODE_THEME_KEY = "wysiwyg_code_theme"
_MERMAID_THEME_KEY = "wysiwyg_mermaid_theme"

_EDITOR_THEMES = frozenset(
    {
        "Auto",
        "Light",
        "Solarized",
        "Warm Light",
        "Dim Light",
        "One Dark",
        "Github Dark",
        "Nord",
        "Monokai",
        "Dracula",
    }
)
_DARK_EDITOR_THEMES = frozenset(
    {"One Dark", "Github Dark", "Nord", "Monokai", "Dracula"}
)
_CODE_THEMES = frozenset(
    {
        "Auto",
        "Github",
        "Solarized Light",
        "Material Light",
        "Quiet Light",
        "One Light",
        "Dracula",
        "Monokai",
        "One Dark",
        "Solarized Dark",
        "Material Dark",
    }
)
_MERMAID_THEMES = frozenset(
    {
        "Auto",
        "Light",
        "Forest",
        "Ocean",
        "Sunset",
        "Dark",
        "Dracula",
        "Monokai",
        "Nord",
    }
)


def _document_session_id(path: str | Path | None) -> str:
    """Return a stable, non-identifying cache key for one document path."""
    if path:
        identity = str(Path(path).resolve()).casefold()
    else:
        identity = "untitled"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"markdown-viewer:{digest}"


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
    JS-facing method names (``contentChanged``/``saveWithContent``/``ready``,
    called by assets/vditor_glue.js): a Signal and a @Slot cannot share one
    name on the same QObject in PySide6.
    """

    contentPushed = Signal(str, int, int, int, str, int, int)
    contentDeltaPushed = Signal(int, int, int, str, int, int)
    saveRequestedSig = Signal()
    saveWithContentSig = Signal(str, int, int, int, str, int, int)
    viewReadySig = Signal()
    escRequestedSig = Signal()
    toolbarActionSig = Signal(str)
    zoomRequestedSig = Signal(int)
    contextMenuRequestedSig = Signal(int, int)

    def __init__(self, view: "WysiwygView") -> None:
        super().__init__(view)
        self._view = view

    @Slot(result=str)
    def initialConfig(self) -> str:
        """Return the one-shot constructor payload requested by the host page."""
        return self._view._claim_initial_config()

    @Slot(str, int, int, int, str, int, int)
    def contentChanged(
        self,
        markdown: str,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        self.contentPushed.emit(
            markdown,
            generation,
            start,
            delete_count,
            inserted,
            base_revision,
            final_length,
        )

    @Slot(int, int, int, str, int, int)
    def contentDelta(
        self,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        """Receive the typing hot path without a full Markdown IPC copy."""
        self.contentDeltaPushed.emit(
            generation,
            start,
            delete_count,
            inserted,
            base_revision,
            final_length,
        )

    @Slot()
    def saveRequested(self) -> None:
        self.saveRequestedSig.emit()

    @Slot(str, int, int, int, str, int, int)
    def saveWithContent(
        self,
        markdown: str,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        self.saveWithContentSig.emit(
            markdown,
            generation,
            start,
            delete_count,
            inserted,
            base_revision,
            final_length,
        )

    @Slot()
    def ready(self) -> None:
        self.viewReadySig.emit()

    @Slot()
    def escRequested(self) -> None:
        self.escRequestedSig.emit()

    @Slot(str)
    def toolbarAction(self, name: str) -> None:
        self.toolbarActionSig.emit(name)

    @Slot(int)
    def zoomRequested(self, steps: int) -> None:
        self.zoomRequestedSig.emit(steps)

    @Slot(str)
    def changeEditorTheme(self, value: str) -> None:
        self._view._on_editor_theme_changed(value)

    @Slot(str)
    def changeCodeTheme(self, value: str) -> None:
        self._view._on_code_theme_changed(value)

    @Slot(str)
    def changeMermaidTheme(self, value: str) -> None:
        self._view._on_mermaid_theme_changed(value)

    @Slot(int, int)
    def contextMenuRequested(self, x: int, y: int) -> None:
        self.contextMenuRequestedSig.emit(x, y)


class WysiwygView(QWebEngineView):
    """Embeds Vditor in WYSIWYG mode and bridges it to Python via QWebChannel."""

    content_changed = Signal(str)
    content_changed_detailed = Signal(str, int, int, str, int, int)
    save_requested = Signal()
    save_with_content_requested = Signal(str)
    save_with_content_detailed = Signal(str, int, int, str, int, int)
    view_ready = Signal()
    # Compatibility bridge for pages from before Office parity. The bundled
    # 4.2 glue never emits a clean Esc: exact popovers consume it and an
    # otherwise-unhandled Esc stays inside the editor.
    esc_requested = Signal()
    # Host-specific toolbar button click (export, attachments, source switch,
    # graph and other Qt-owned workflows).
    toolbar_action = Signal(str)
    # Coalesced Ctrl/meta+wheel gesture from the Office host page. Positive
    # values zoom in; negative values zoom out.
    zoom_requested = Signal(int)
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
        self._bridge.contentPushed.connect(self._on_content_pushed)
        self._bridge.contentDeltaPushed.connect(self._on_content_delta_pushed)
        self._bridge.saveRequestedSig.connect(self.save_requested)
        self._bridge.saveWithContentSig.connect(self._on_save_with_content)
        self._bridge.viewReadySig.connect(self._on_view_ready)
        self._bridge.escRequestedSig.connect(self.esc_requested)
        self._bridge.toolbarActionSig.connect(self.toolbar_action)
        self._bridge.zoomRequestedSig.connect(self.zoom_requested)
        self._bridge.contextMenuRequestedSig.connect(self.context_menu_requested)

        self._channel = QWebChannel(self)
        self._channel.registerObject("wysiwygBridge", self._bridge)
        page.setWebChannel(self._channel)

        self._base_url = QUrl.fromLocalFile(str(ASSETS_DIR) + "/")
        self._ready = False
        self._generation = 0
        self._pending_markdown: tuple[str, int, str, str] | None = None
        self._document_base_url = ""
        self._document_session_id = _document_session_id(None)
        self._live_markdown = ""
        self._live_revision = 0
        self._resync_in_flight = False
        self._pending_theme_name = "light"
        theme_settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._editor_theme_preference = self._read_theme_preference(
            theme_settings, _EDITOR_THEME_KEY, _EDITOR_THEMES
        )
        self._code_theme_preference = self._read_theme_preference(
            theme_settings, _CODE_THEME_KEY, _CODE_THEMES
        )
        self._mermaid_theme_preference = self._read_theme_preference(
            theme_settings, _MERMAID_THEME_KEY, _MERMAID_THEMES
        )
        self._boot_generation: int | None = None
        self._boot_markdown: str | None = None
        self._boot_base_url = ""
        self._boot_session_id = self._document_session_id
        self._boot_theme_name: str | None = None
        self._boot_config_json: str | None = None
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

    @staticmethod
    def _read_theme_preference(
        settings: QSettings, key: str, allowed: frozenset[str]
    ) -> str | None:
        raw = settings.value(key, None)
        value = str(raw).strip() if raw is not None else ""
        if value in allowed:
            return value
        if value:
            settings.remove(key)
            settings.sync()
        return None

    def _persist_theme_preference(
        self, key: str, value: str, allowed: frozenset[str]
    ) -> str | None:
        value = str(value or "").strip()
        if value not in allowed:
            return None
        settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        settings.setValue(key, value)
        settings.sync()
        return value

    def _theme_options(self, name: str) -> dict[str, str]:
        """Return validated IDs from Office Viewer's exact 4.2 catalog."""
        app_dark = name == "dark"
        editor_theme = self._editor_theme_preference or "Auto"
        if editor_theme == "Auto":
            effective_dark = app_dark
        else:
            effective_dark = editor_theme in _DARK_EDITOR_THEMES

        return {
            "editorTheme": editor_theme,
            "theme": "dark" if effective_dark else "classic",
            "codeMirrorTheme": self._code_theme_preference or "Auto",
            "mermaidTheme": self._mermaid_theme_preference or "Auto",
        }

    @staticmethod
    def _host_theme_payload(name: str) -> dict[str, object]:
        """Build the complete VS Code token surface consumed by Auto.css."""
        theme = get_theme("dark" if name == "dark" else "light")
        tokens = {
            "--vscode-badge-background": theme.accent,
            "--vscode-charts-blue": theme.accent,
            "--vscode-charts-foreground": theme.text,
            "--vscode-charts-green": theme.success,
            "--vscode-charts-orange": theme.warning,
            "--vscode-charts-purple": theme.accent_hover,
            "--vscode-charts-red": theme.danger,
            "--vscode-charts-yellow": theme.warning,
            "--vscode-descriptionForeground": theme.text_muted,
            "--vscode-dropdown-background": theme.surface,
            "--vscode-editor-background": theme.surface,
            "--vscode-editorCursor-foreground": theme.accent,
            "--vscode-editor-foldBackground": theme.accent_soft,
            "--vscode-editor-foreground": theme.text,
            "--vscode-editorGroupHeader-tabsBackground": theme.surface_alt,
            "--vscode-editorGutter-foldingControlForeground": theme.text_muted,
            "--vscode-editor-inactiveSelectionBackground": theme.surface_hover,
            "--vscode-editor-lineHighlightBackground": theme.surface_hover,
            "--vscode-editorLineNumber-activeForeground": theme.text,
            "--vscode-editorLineNumber-foreground": theme.text_subtle,
            "--vscode-editor-selectionBackground": theme.accent_soft,
            "--vscode-editor-selectionHighlightBackground": theme.surface_hover,
            "--vscode-editorWidget-background": theme.surface,
            "--vscode-errorForeground": theme.danger,
            "--vscode-focusBorder": theme.accent,
            "--vscode-foreground": theme.text,
            "--vscode-icon-foreground": theme.text_muted,
            "--vscode-input-background": theme.surface,
            "--vscode-input-placeholderForeground": theme.text_subtle,
            "--vscode-keybindingTable-headerBackground": theme.surface_alt,
            "--vscode-list-focusHighlightForeground": theme.accent,
            "--vscode-list-hoverBackground": theme.surface_hover,
            "--vscode-menu-background": theme.surface,
            "--vscode-menu-selectionBackground": theme.surface_hover,
            "--vscode-panel-border": theme.border,
            "--vscode-scrollbarSlider-background": theme.border,
            "--vscode-scrollbarSlider-hoverBackground": theme.text_subtle,
            "--vscode-sideBar-background": theme.surface_alt,
            "--vscode-sideBarSectionHeader-background": theme.surface_alt,
            "--vscode-symbolIcon-enumeratorForeground": theme.warning,
            "--vscode-symbolIcon-keywordForeground": theme.accent,
            "--vscode-symbolIcon-numberForeground": theme.warning,
            "--vscode-symbolIcon-operatorForeground": theme.text_muted,
            "--vscode-symbolIcon-stringForeground": theme.success,
            "--vscode-tab-inactiveForeground": theme.text_muted,
            "--vscode-textBlockQuote-background": theme.surface_alt,
            "--vscode-textBlockQuote-border": theme.border,
            "--vscode-textCodeBlock-background": theme.code_bg,
            "--vscode-textLink-foreground": theme.accent,
            "--vscode-textPreformat-foreground": theme.code_text,
            "--vscode-textSeparator-foreground": theme.border,
            "--vscode-widget-border": theme.border,
            "--vscode-widget-shadow": theme.shadow,
        }
        return {"kind": theme.name, "tokens": tokens}

    def _on_editor_theme_changed(self, value: str) -> None:
        saved = self._persist_theme_preference(
            _EDITOR_THEME_KEY, value, _EDITOR_THEMES
        )
        if saved is None:
            return
        self._editor_theme_preference = saved
        # The exact core applies the skin immediately. Sync its content theme
        # as well, without changing the newly selected preference.
        if self._ready:
            self._apply_theme_name(self._pending_theme_name)

    def _on_code_theme_changed(self, value: str) -> None:
        saved = self._persist_theme_preference(
            _CODE_THEME_KEY, value, _CODE_THEMES
        )
        if saved is not None:
            self._code_theme_preference = saved

    def _on_mermaid_theme_changed(self, value: str) -> None:
        saved = self._persist_theme_preference(
            _MERMAID_THEME_KEY, value, _MERMAID_THEMES
        )
        if saved is not None:
            self._mermaid_theme_preference = saved

    def _claim_initial_config(self) -> str:
        """Snapshot queued state for Vditor's first constructor invocation.

        MainWindow creates this view, applies its theme and queues the active
        document before Chromium can finish QWebChannel setup.  Supplying that
        state here lets the host construct Vditor once with the final value and
        themes instead of first rendering an empty Auto-themed editor.
        """
        if self._boot_config_json is not None:
            return self._boot_config_json

        pending = self._pending_markdown
        if pending is None:
            markdown = ""
            generation = self._generation
            base_url = self._document_base_url
            session_id = self._document_session_id
        else:
            markdown, generation, base_url, session_id = pending

        self._boot_generation = int(generation)
        self._boot_markdown = markdown
        self._boot_base_url = base_url
        self._boot_session_id = session_id
        self._boot_theme_name = self._pending_theme_name
        config = {
            "value": markdown,
            "generation": int(generation),
            "documentBaseUrl": base_url,
            "hostTheme": self._host_theme_payload(self._boot_theme_name),
            # Keep all constructor-owned settings in one extensible object.
            # Per-document fields such as documentCacheId can be added here
            # without another bespoke host-page handshake.
            "vditorOptions": {
                **self._theme_options(self._boot_theme_name),
                "cache": {
                    "enable": False,
                    "id": session_id,
                    # The reused WebEngine owns per-document caret/scroll
                    # sessions in vditor_glue.js.  The fork's VS Code adapter
                    # keeps a private WeakMap that cannot be invalidated when
                    # cache.id changes and can leak one tab's caret to another.
                    "focusHost": "browser",
                },
                "preview": {"markdown": {"linkBase": base_url}},
            },
        }
        self._boot_config_json = json.dumps(config, ensure_ascii=False)
        return self._boot_config_json

    def _on_view_ready(self) -> None:
        self._ready = True
        pending = self._pending_markdown
        if pending is not None:
            text, generation, base_url, session_id = pending
            if (
                int(generation) == self._boot_generation
                and text == self._boot_markdown
                and base_url == self._boot_base_url
                and session_id == self._boot_session_id
            ):
                # The constructor already owns this exact document.
                self._pending_markdown = None
            else:
                # A newer document arrived after the host claimed its initial
                # config. Preserve the generation/data-safety contract.
                self._push_markdown(text, generation, base_url, session_id)
                self._pending_markdown = None
        if self._pending_theme_name != self._boot_theme_name:
            # Same race handling for a theme changed during page startup.
            self._apply_theme_name(self._pending_theme_name)

        # Release the transient full-document constructor copies as soon as
        # the page has acknowledged that it is ready.
        self._boot_markdown = None
        self._boot_config_json = None
        self.view_ready.emit()

    def _on_content_pushed(
        self,
        markdown: str,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        """Compatibility full-value path for older glue/bridge clients."""
        if int(generation) == self._generation:
            self._live_markdown = markdown
            self._live_revision = int(base_revision) + 1
            self.content_changed_detailed.emit(
                markdown,
                int(start),
                int(delete_count),
                inserted,
                int(base_revision),
                int(final_length),
            )
            self.content_changed.emit(markdown)

    @staticmethod
    def _apply_utf16_delta(
        markdown: str,
        start: int,
        delete_count: int,
        inserted: str,
        final_length: int,
    ) -> str | None:
        """Apply JavaScript/Qt UTF-16 offsets to a Python Unicode string."""
        start = int(start)
        delete_count = int(delete_count)
        if start < 0 or delete_count < 0:
            return None
        old_length = py_to_qt_position(markdown, len(markdown))
        end = start + delete_count
        if end > old_length:
            return None
        start_py = qt_to_py_position(markdown, start)
        end_py = qt_to_py_position(markdown, end)
        # Refuse offsets that split a surrogate pair; recovery will request a
        # rare full value instead of creating invalid Unicode.
        if (
            py_to_qt_position(markdown, start_py) != start
            or py_to_qt_position(markdown, end_py) != end
        ):
            return None
        updated = markdown[:start_py] + inserted + markdown[end_py:]
        if py_to_qt_position(updated, len(updated)) != int(final_length):
            return None
        return updated

    def _on_content_delta_pushed(
        self,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        """Rebuild the full value locally after delta-only QWebChannel IPC."""
        if int(generation) != self._generation:
            return
        base_revision = int(base_revision)
        if base_revision < self._live_revision:
            return
        markdown = None
        if base_revision == self._live_revision:
            markdown = self._apply_utf16_delta(
                self._live_markdown,
                start,
                delete_count,
                inserted,
                final_length,
            )
        if markdown is None:
            self._request_live_resync(int(generation))
            return
        self._live_markdown = markdown
        self._live_revision = base_revision + 1
        self.content_changed_detailed.emit(
            markdown,
            int(start),
            int(delete_count),
            inserted,
            base_revision,
            int(final_length),
        )
        self.content_changed.emit(markdown)

    def _request_live_resync(self, generation: int) -> None:
        """Recover a rare revision/offset drift with one explicit full read."""
        if self._resync_in_flight or not self._ready:
            return
        self._resync_in_flight = True
        script = (
            "(function(){var g=window.__wysiwygGlue;"
            "if(!g||!g._state.vditor){return null;}return JSON.stringify({"
            "markdown:g.getValue(),generation:g._state.generation,"
            "revision:g._state.revision});})();"
        )

        def _recovered(result) -> None:
            self._resync_in_flight = False
            try:
                value = json.loads(result) if isinstance(result, str) else None
            except (TypeError, ValueError):
                value = None
            if (
                not isinstance(value, dict)
                or int(value.get("generation", -1)) != self._generation
                or int(value.get("generation", -1)) != generation
                or not isinstance(value.get("markdown"), str)
            ):
                return
            revision = max(0, int(value.get("revision", 0)))
            if revision < self._live_revision:
                return
            markdown = value["markdown"]
            self._live_markdown = markdown
            self._live_revision = revision
            # A negative start deliberately selects MainWindow's full-value
            # convergence path. This path runs only after detected drift.
            self.content_changed_detailed.emit(
                markdown,
                -1,
                0,
                "",
                max(0, revision - 1),
                py_to_qt_position(markdown, len(markdown)),
            )
            self.content_changed.emit(markdown)

        self.page().runJavaScript(script, _recovered)

    def _on_save_with_content(
        self,
        markdown: str,
        generation: int,
        start: int,
        delete_count: int,
        inserted: str,
        base_revision: int,
        final_length: int,
    ) -> None:
        if int(generation) == self._generation:
            self._live_markdown = markdown
            self._live_revision = int(base_revision) + 1
            self.save_with_content_detailed.emit(
                markdown,
                int(start),
                int(delete_count),
                inserted,
                int(base_revision),
                int(final_length),
            )
            self.save_with_content_requested.emit(markdown)

    # ---- public API ----------------------------------------------------
    def load_markdown(self, text: str) -> None:
        """Push *text* into Vditor, overwriting whatever it currently holds.

        Safe to call before the page has finished booting: the value is
        queued and flushed once ``ready()`` arrives.
        """
        self._generation += 1
        generation = self._generation
        self._live_markdown = text
        self._live_revision = 0
        self._resync_in_flight = False
        if not self._ready:
            self._pending_markdown = (
                text,
                generation,
                self._document_base_url,
                self._document_session_id,
            )
            return
        self._push_markdown(
            text,
            generation,
            self._document_base_url,
            self._document_session_id,
        )

    def _push_markdown(
        self, text: str, generation: int, base_url: str, session_id: str
    ) -> None:
        self._loading = True
        js = (
            "window.__wysiwygGlue && window.__wysiwygGlue.setValue(%s,%d,%s,%s);"
            % (
                json.dumps(text),
                int(generation),
                json.dumps(base_url),
                json.dumps(session_id),
            )
        )
        self.page().runJavaScript(js, lambda _result: setattr(self, "_loading", False))

    def set_document_path(self, path: str | Path | None) -> None:
        """Set the base used to resolve relative images now and on next load."""
        self._document_session_id = _document_session_id(path)
        if not path:
            self._document_base_url = ""
        else:
            directory = Path(path).resolve().parent
            self._document_base_url = QUrl.fromLocalFile(
                str(directory) + "/"
            ).toString()

        if self._pending_markdown is not None:
            text, generation, _old_base, _old_session_id = self._pending_markdown
            self._pending_markdown = (
                text,
                generation,
                self._document_base_url,
                self._document_session_id,
            )
        if self._ready:
            self.page().runJavaScript(
                "window.__wysiwygGlue && window.__wysiwygGlue.setDocumentBase(%s,%s);"
                % (
                    json.dumps(self._document_base_url),
                    json.dumps(self._document_session_id),
                )
            )

    def insert_value(self, text: str) -> None:
        """Insert an image/attachment Markdown snippet at the live caret."""
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

    def prepare_pdf_export(self, callback) -> None:
        """Build a chrome-free print sibling and report its full CSS size.

        *callback* receives ``{"ok": True, "width": float, "height": float}``
        or ``None``. The sibling remains active until ``finish_pdf_export()``
        so ``QWebEnginePage.printToPdf`` can render the same prepared DOM.
        """
        if not self._ready:
            callback(None)
            return

        def prepared(result) -> None:
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (TypeError, ValueError):
                    result = None
            if not isinstance(result, dict) or result.get("ok") is not True:
                callback(None)
                return
            try:
                width = float(result["width"])
                height = float(result["height"])
            except (KeyError, TypeError, ValueError):
                callback(None)
                return
            if (
                not math.isfinite(width)
                or not math.isfinite(height)
                or width <= 0
                or height <= 0
            ):
                callback(None)
                return
            callback({"ok": True, "width": width, "height": height})

        self.page().runJavaScript(
            "window.__wysiwygGlue ? "
            "JSON.stringify(window.__wysiwygGlue.preparePrint()) : null",
            prepared,
        )

    def finish_pdf_export(self) -> None:
        """Remove the prepared print sibling; safe to call repeatedly."""
        if not self._ready:
            return
        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.finishPrint();"
        )

    def flush_pending_edits(self) -> None:
        """Force any debounced-but-unsent edit to push immediately (pre-save)."""
        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.flushPending();"
        )

    def request_markdown_snapshot(self, callback) -> None:
        """Asynchronously return the live editor value without emitting it."""
        if not self._ready:
            pending = self._pending_markdown
            callback(pending[0] if pending is not None else None)
            return
        self.page().runJavaScript(
            "window.__wysiwygGlue ? window.__wysiwygGlue.takeSnapshot() : null",
            lambda result: callback(result if isinstance(result, str) else None),
        )

    def request_markdown_snapshot_envelope(self, callback) -> None:
        """Return the live Markdown plus a UTF-16 delta from the last push."""
        if not self._ready:
            pending = self._pending_markdown
            callback(
                {
                    "markdown": pending[0],
                    "generation": pending[1],
                    "start": 0,
                    "deleteCount": 0,
                    "inserted": "",
                }
                if pending is not None
                else None
            )
            return

        def _decode(result) -> None:
            try:
                value = json.loads(result) if isinstance(result, str) else None
            except (TypeError, ValueError):
                value = None
            if not isinstance(value, dict):
                callback(None)
                return
            if (
                not isinstance(value.get("markdown"), str)
                or int(value.get("generation", -1)) != self._generation
                or int(value.get("token", 0)) <= 0
            ):
                callback(None)
                return
            callback(value)

        self.page().runJavaScript(
            "window.__wysiwygGlue ? "
            "window.__wysiwygGlue.takeSnapshotEnvelope() : null",
            _decode,
        )

    def acknowledge_markdown(
        self, markdown: str, token: int, revision: int, callback=None
    ) -> None:
        """Align the next JS delta after Python accepted a direct snapshot."""
        def _acknowledged(result) -> None:
            if result is True:
                self._live_markdown = markdown
                self._live_revision = int(revision)
            if callback is not None:
                callback(result)

        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.acknowledgeMarkdown(%s,%d,%d,%d);"
            % (
                json.dumps(markdown),
                self._generation,
                int(token),
                int(revision),
            ),
            _acknowledged,
        )

    def cancel_snapshot(self, token: int = 0) -> None:
        """Release a failed transition without consuming its pending push."""
        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.cancelSnapshot(%d);"
            % int(token)
        )

    def mark_saved(self, markdown: str | None = None) -> None:
        value = "null" if markdown is None else json.dumps(markdown)
        self.page().runJavaScript(
            "window.__wysiwygGlue && window.__wysiwygGlue.markSaved(%s);" % value
        )

    def open_find(self) -> None:
        self.page().runJavaScript(
            "(function(){var b=document.querySelector('button[data-type=\"find\"]');"
            "if(b){b.click();}})();"
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
        self._pending_theme_name = "dark" if name == "dark" else "light"
        if not self._ready:
            return
        self._apply_theme_name(self._pending_theme_name)

    def _apply_theme_name(self, name: str) -> None:
        options = self._theme_options(name)
        host_theme = self._host_theme_payload(name)
        js = (
            "(function(){var g=window.__wysiwygGlue;"
            "if(!g){return;}try{"
            "if(g.applyHostTheme){g.applyHostTheme(%s);}"
            "var v=g._state&&g._state.vditor;if(!v){return;}"
            "if(v.setTheme){v.setTheme(%s,%s);}"
            "if(v.setEditorTheme){v.setEditorTheme(%s);}"
            "if(v.setMermaidTheme){v.setMermaidTheme(%s);}"
            "}catch(e){}})();"
            % (
                json.dumps(host_theme, ensure_ascii=False),
                json.dumps(options["theme"]),
                json.dumps(options["codeMirrorTheme"]),
                json.dumps(options["editorTheme"]),
                json.dumps(options["mermaidTheme"]),
            )
        )
        self.page().runJavaScript(js)
