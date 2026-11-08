"""D1b: common Markdown dialect (==mark==, [TOC]), link-scheme safety and
the front-matter quote fix.  Qt-free except the in-process navigation test."""

from __future__ import annotations

import re
import urllib.parse
import zipfile

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage

from app import md_converter, renderer

_CLICK = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
_TYPED = QWebEnginePage.NavigationType.NavigationTypeTyped


def _body(text: str) -> str:
    return md_converter.render_body(text).body


# ---------------------------------------------------------------- ==mark==


def test_mark_renders_inline_with_nested_formatting():
    html = _body("==hi== and ==a *b* c==\n")
    assert "<mark>hi</mark>" in html
    assert "<mark>a <em>b</em> c</mark>" in html
    assert "==" not in html


@pytest.mark.parametrize(
    "source",
    [
        "if a == b then c == d\n",  # comparison operators, spaces around
        "`==x==`\n",             # code span
        "```\n==x==\n```\n",     # fence
        "== x ==\n",             # spaces inside the markers
        "==unclosed\n",
    ],
)
def test_mark_leaves_non_marks_alone(source):
    assert "<mark>" not in _body(source)


def test_mark_css_has_light_and_dark_rules():
    css = md_converter._FULL_CSS
    assert "mark:not(.annot)" in css
    assert ".theme-dark mark:not(.annot)" in css


def _docx_xml(md: str, tmp_path) -> str:
    from app.docx_export import export_markdown_to_docx

    out = tmp_path / "out.docx"
    export_markdown_to_docx(md, out)
    with zipfile.ZipFile(out) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def test_docx_export_highlights_marks_and_leaves_no_equals_signs(tmp_path):
    xml = _docx_xml("# T\n\nplain ==lit== tail\n\n[TOC]\n", tmp_path)
    assert '<w:highlight w:val="yellow"/>' in xml
    assert "==" not in xml
    assert "[TOC]" not in xml
    assert "lit" in xml


def test_pptx_export_highlights_marks_and_drops_toc(tmp_path):
    from pptx import Presentation

    from app.pptx_export import export_markdown_to_pptx

    out = tmp_path / "deck.pptx"
    export_markdown_to_pptx("# T\n\n[toc]\n\nplain ==lit== tail\n", out)
    deck = Presentation(str(out))
    texts, xml = [], ""
    for slide in deck.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
                xml += shape.text_frame._txBody.xml
    joined = "\n".join(texts)
    assert "==" not in joined and "[toc]" not in joined.lower()
    assert "lit" in joined
    assert re.search(r"<a:highlight>\s*<a:srgbClr val=\"FFF3A3\"", xml)


# ------------------------------------------------------------------- [TOC]


def test_toc_outline_links_match_heading_ids_including_duplicates():
    html = _body("# A\n\n[Toc]\n\n## B\n\n### C\n\n## B\n\n# D & E\n")
    ids = re.findall(r'<h[1-6] id="([^"]+)"', html)
    assert ids == ["a", "b", "c", "b-2", "d-amp-e"]
    nav = re.search(r'<nav class="md-toc">.*?</nav>', html, re.S).group(0)
    assert re.findall(r'href="#([^"]+)"', nav) == ids
    assert nav.count("<ul>") == nav.count("</ul>")
    assert nav.count("<li>") == nav.count("</li>")
    # nesting: C sits inside B's sub-list, the second B is a sibling of A's child.
    assert re.search(r'<a href="#b">B</a><ul><li><a href="#c">C</a></li></ul></li>', nav)
    assert "[Toc]" not in html


def test_toc_only_replaces_a_whole_top_level_paragraph():
    html = _body("# A\n\nsee [TOC] here\n\n- [TOC]\n\n```\n[TOC]\n```\n")
    assert "md-toc" not in html
    assert html.count("[TOC]") >= 2  # inline text and code stay literal


def test_toc_without_headings_is_an_empty_nav():
    html = _body("[TOC]\n\ntext\n")
    assert '<nav class="md-toc"></nav>' in html
    assert "[TOC]" not in html


def test_toc_survives_body_rendering_in_both_themes():
    for theme in ("light", "dark"):
        doc = md_converter.wrap_body(md_converter.render_body("# A\n\n[TOC]\n\n## B\n"), "t", theme)
        assert 'href="#b"' in doc
        assert "[TOC]" not in doc


# ------------------------------------------------------------ link schemes


def test_dangerous_link_schemes_do_not_render_as_links():
    for bad in ("javascript:alert(1)", "vbscript:x", "data:text/html,x",
                "//host/share/x", "file://host/share/x"):
        assert "<a href" not in _body(f"[x]({bad})\n"), bad


def test_custom_scheme_links_are_not_rewritten():
    html = _body("[go](foo://item/abc-123?x=1#h)\n")
    assert 'href="foo://item/abc-123?x=1#h"' in html
    assert "markdown-viewer.invalid" not in html


class _FakeView(QObject):
    wikilink_clicked = Signal(str)
    home_action_requested = Signal(str)
    local_doc_clicked = Signal(str)
    local_doc_target_clicked = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._current_path = "C:/notes/a.md"
        self._html_base_url = None

    def scroll_to(self, anchor):
        pass


def _route(target: str) -> QUrl:
    return QUrl("https://markdown-viewer.invalid/x/" + urllib.parse.quote(target, safe=""))


def test_unknown_scheme_clicks_never_reach_the_os(qapp, monkeypatch):
    page = renderer._DocumentPage(_FakeView())
    opened = []
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()) or True)
    monkeypatch.setattr(renderer.QMessageBox, "warning", lambda *a: pytest.fail("warned"))
    monkeypatch.setattr(renderer.QMessageBox, "question", lambda *a: pytest.fail("asked"))

    # Custom, look-alike and script schemes: neither a click nor a scripted
    # navigation is delegated.
    for target in ("foo://item/1", "foo.evil://item/1", "ms-msdt://x", "ssh://h/x",
                   "steam://run/1", "javascript:alert(1)"):
        assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False, target
        assert page.acceptNavigationRequest(QUrl(target), _TYPED, True) is False, target
    QTest.qWait(20)
    assert opened == []

    # The reserved preview host never forwards a URL it carries.
    for target in ("foo://item/1", "https://example.com/",
                   "file:///C:/Windows/System32/calc.exe"):
        assert page.acceptNavigationRequest(_route(target), _CLICK, True) is False
    QTest.qWait(20)
    assert opened == []


# ------------------------------------------------------------ front matter


def _fm(raw: str) -> dict:
    return md_converter._parse_yaml_subset(raw)


def test_front_matter_double_quotes_are_unescaped():
    raw = 'a: "x\\"y\\\\z"\nb: "line1\\nline2\\tTab"\nc: "\\u4e2d\\u6587"\nd: "\\q stays"\n'
    data = _fm(raw)
    assert data["a"] == 'x"y\\z'
    assert data["b"] == "line1\nline2\tTab"
    assert data["c"] == "\u4e2d\u6587"
    assert data["d"] == "\\q stays"


def test_front_matter_single_quotes_only_fold_doubled_quotes():
    data = _fm("a: 'it''s'\nb: 'no \\n escape'\nc: plain \\n text\n")
    assert data["a"] == "it's"
    assert data["b"] == "no \\n escape"
    assert data["c"] == "plain \\n text"


def test_front_matter_lists_use_the_same_unescaping():
    data = _fm('t: ["a\\u0041", \'b\'\'c\']\nu:\n  - "x\\ty"\n  - \'z\'\'\'\n')
    assert data["t"] == ["aA", "b'c"]
    assert data["u"] == ["x\ty", "z'"]


def test_front_matter_unquoted_and_mismatched_quotes_unchanged():
    data = _fm("a: \"open\nb: 'x\"\nc: \"\"\nd: ''\n")
    assert data["a"] == '"open'
    assert data["b"] == "'x\""
    assert data["c"] == ""
    assert data["d"] == ""
