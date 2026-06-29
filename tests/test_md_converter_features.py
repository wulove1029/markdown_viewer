"""Tests for the upgraded Markdown converter (math, autolink, copy, preview)."""

from app.md_converter import convert, convert_text, set_user_css


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


def test_callout_blockquote_becomes_callout_div():
    html, _ = convert_text("> [!warning] Care\n> body\n")
    assert 'class="callout callout-warning"' in html
    assert '<span class="callout-title">Care</span>' in html
    assert "<blockquote>" not in html


def test_plain_blockquote_stays_blockquote():
    html, _ = convert_text("> just a quote\n")
    assert "<blockquote>" in html
    assert 'class="callout' not in html  # the CSS mentions callout; the body must not


def test_safe_html_allowlist_passes_through():
    html, _ = convert_text("Press <kbd>Ctrl</kbd>, H<sub>2</sub>O, <mark>hi</mark>")
    assert "<kbd>Ctrl</kbd>" in html
    assert "<sub>2</sub>" in html
    assert "<mark>hi</mark>" in html


def test_unsafe_html_still_escaped():
    html, _ = convert_text('text <script>alert(1)</script> <div onclick="x">y</div>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "onclick" not in html or "&lt;div" in html


def test_convert_caches_by_mtime(tmp_path):
    p = tmp_path / "c.md"
    p.write_text("# Cached\n", encoding="utf-8")
    set_user_css("")  # ensure a clean cache
    first = convert(p)
    second = convert(p)
    assert first is second  # cache hit returns the same object


def test_user_css_injected_and_clears_cache(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# Styled\n", encoding="utf-8")
    set_user_css("")
    before = convert(p)
    set_user_css("body { color: rebeccapurple }")  # also clears the cache
    html, _ = convert(p)
    assert "rebeccapurple" in html
    assert convert(p) is not before
    set_user_css("")  # reset for other tests


def test_front_matter_renders_metadata_and_keeps_line_numbers():
    from app.md_converter import parse_front_matter, front_matter_tags
    src = "---\ntitle: T\ntags: [a, b]\n---\n# H\n\n- [ ] todo\n"
    data, _ = parse_front_matter(src)
    assert data["title"] == "T"
    assert front_matter_tags(data) == ["a", "b"]
    html, _ = convert_text(src)
    assert 'class="frontmatter"' in html
    assert html.count('class="fm-tag"') == 2
    # "- [ ] todo" is line index 6 in the source; data-line must match.
    assert 'data-line="6"' in html
