"""Tests for the upgraded Markdown converter (math, autolink, copy, preview)."""

from app.md_converter import convert_text


def test_inline_math_emits_katex():
    html, _ = convert_text("Energy $E=mc^2$ done.")
    assert 'class="math inline"' in html
    assert "katex" in html.lower()  # KaTeX loader injected


def test_block_math_emits_katex():
    html, _ = convert_text("$$\n\\int_0^1 x\\,dx\n$$")
    assert 'class="math block"' in html
    assert "katex" in html.lower()


def test_no_math_no_katex():
    html, _ = convert_text("# Just a heading\n\nplain text")
    assert "katex" not in html.lower()


def test_autolink_bare_url():
    html, _ = convert_text("see https://example.com now")
    assert 'href="https://example.com"' in html


def test_footnote_renders():
    html, _ = convert_text("text[^1]\n\n[^1]: note")
    assert "footnote" in html.lower()


def test_code_copy_button_present_only_with_code():
    with_code, _ = convert_text("```python\nx=1\n```")
    assert "copy-btn" in with_code
    without, _ = convert_text("no code here")
    assert "copy-btn" not in without


def test_convert_text_returns_headings():
    _html, headings = convert_text("# Title\n## Sub")
    assert headings == [(1, "Title", "title"), (2, "Sub", "sub")]


def test_task_checkboxes_interactive_with_source_line():
    html, _ = convert_text("intro\n\n- [ ] first\n- [x] second\n")
    # Interactive (not disabled) and tagged with the 0-based source line.
    assert "disabled" not in html
    assert 'data-line="2"' in html  # "- [ ] first" is line index 2
    assert 'data-line="3"' in html  # "- [x] second" is line index 3
    assert 'checked="checked"' in html  # the done item


def test_wikilink_renders_anchor():
    html, _ = convert_text("See [[My Note]] and [[a/b|alias]].")
    assert 'class="wikilink"' in html
    assert "wikilink:My%20Note" in html
    assert ">alias<" in html
