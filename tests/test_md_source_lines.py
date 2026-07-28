"""data-src-start / data-src-end injection (app/md_converter source_line plugin).

These attributes are what lets a double-click in the preview find the exact
source lines behind a rendered block, so every assertion here checks the
attribute against the real line text it claims to describe.
"""

import re

from app.md_converter import convert_text

_TAG_RE = re.compile(r"<([a-zA-Z][\w-]*)((?:\s[^>]*)?)>")
_START_RE = re.compile(r'data-src-start="(\d+)"')
_END_RE = re.compile(r'data-src-end="(\d+)"')


def src_blocks(html):
    """Every element carrying a source range, as (tag, start, end)."""
    found = []
    for match in _TAG_RE.finditer(html):
        attrs = match.group(2)
        start, end = _START_RE.search(attrs), _END_RE.search(attrs)
        if start and end:
            found.append((match.group(1), int(start.group(1)), int(end.group(1))))
    return found


def ranges_for(html, tag):
    return [(s, e) for t, s, e in src_blocks(html) if t == tag]


def test_paragraph_range_covers_every_line_of_the_paragraph():
    src = "intro\n\nline one\nline two\n"
    html, _ = convert_text(src)
    assert ranges_for(html, "p") == [(0, 0), (2, 3)]


def test_heading_keeps_its_anchor_id_and_gains_a_range():
    html, _ = convert_text("# Title\n\nbody\n")
    assert 'id="title"' in html
    assert ranges_for(html, "h1") == [(0, 0)]


def test_only_the_outer_list_is_tagged():
    src = "- alpha\n  - nested\n  - nested two\n- beta\n"
    html, _ = convert_text(src)
    # The nested <ul> must stay untagged so a double-click resolves to the
    # whole top-level list, never to a fragment of it.
    assert ranges_for(html, "ul") == [(0, 3)]


def test_list_range_excludes_the_trailing_blank_line():
    src = "- alpha\n- beta\n\nafter\n"
    html, _ = convert_text(src)
    # markdown-it's list map swallows the blank separator; the range must not.
    assert ranges_for(html, "ul") == [(0, 1)]
    assert ranges_for(html, "p") == [(3, 3)]


def test_table_range_spans_header_separator_and_body():
    src = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    html, _ = convert_text(src)
    assert ranges_for(html, "table") == [(0, 2)]


def test_mermaid_fence_range_covers_the_whole_fence():
    src = "text\n\n```mermaid\ngraph TD\nA-->B\n```\n"
    html, _ = convert_text(src)
    assert 'class="mermaid"' in html  # the mermaid loader still finds it
    assert ranges_for(html, "pre") == [(2, 5)]


def test_highlighted_fence_range_lands_on_the_outer_pre():
    src = "```python\nx = 1\n```\n"
    html, _ = convert_text(src)
    assert 'class="highlight"' in html
    # Exactly one range, on the outer <pre>: the inner Pygments <pre> and the
    # <code> element must not carry a duplicate.
    assert ranges_for(html, "pre") == [(0, 2)]
    assert ranges_for(html, "code") == []


def test_callout_div_carries_the_blockquote_range():
    src = "> [!note] Care\n> body\n"
    html, _ = convert_text(src)
    assert 'class="callout callout-note"' in html
    assert ranges_for(html, "div") == [(0, 1)]


def test_front_matter_is_never_editable_and_does_not_shift_line_numbers():
    src = "---\ntitle: T\n---\n\n# Heading\n\npara\n"
    html, _ = convert_text(src)
    blocks = src_blocks(html)
    # No block may claim a front-matter line (0-2)...
    assert all(start >= 4 for _tag, start, _end in blocks)
    # ...and the body lines are numbered against the original source.
    assert ranges_for(html, "h1") == [(4, 4)]
    assert ranges_for(html, "p") == [(6, 6)]


def test_task_list_keeps_data_line_and_gains_a_range():
    src = "intro\n\n- [ ] first\n- [x] second\n"
    html, _ = convert_text(src)
    assert 'data-line="2"' in html  # checkbox write-back is untouched
    assert 'data-line="3"' in html
    assert ranges_for(html, "ul") == [(2, 3)]


def test_every_range_points_at_non_blank_source_lines():
    src = (
        "---\ntitle: T\n---\n\n"
        "# Heading\n\n"
        "para one\npara one cont\n\n"
        "- a\n  - a1\n- b\n\n"
        "> quote\n\n"
        "```mermaid\ngraph TD\nA-->B\n```\n\n"
        "| x | y |\n|---|---|\n| 1 | 2 |\n\n"
        "final\n"
    )
    lines = src.split("\n")
    html, _ = convert_text(src)
    blocks = src_blocks(html)
    assert blocks, "expected at least one tagged block"
    for tag, start, end in blocks:
        assert 0 <= start <= end < len(lines), (tag, start, end)
        assert lines[start].strip(), (tag, start, "blank first line")
        assert lines[end].strip(), (tag, end, "blank last line")


def test_ranges_survive_a_nested_blockquote_without_tagging_the_inner_block():
    src = "> outer\n>\n> > inner\n"
    html, _ = convert_text(src)
    assert ranges_for(html, "blockquote") == [(0, 2)]


def test_unbalanced_html_block_gets_no_range_but_a_closed_one_does():
    src = "<details>\n<summary>S</summary>\n\ninner\n\n</details>\n"
    html, _ = convert_text(src)
    # <details> renders as a wrapper around blocks it does not own, so tagging
    # it would offer one line to edit while hiding everything inside.
    assert "<details>" in html
    assert ranges_for(html, "details") == []
    assert ranges_for(html, "summary") == [(1, 1)]
    assert ranges_for(html, "p") == [(3, 3)]
