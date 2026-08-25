"""Pure string-level tests for the Markdown format actions."""

from app.format_actions import (
    BOLD,
    CODE,
    ITALIC,
    MARK_CLOSE,
    MARK_OPEN,
    MATH,
    MERMAID_TEMPLATE,
    STRIKE,
    code_block,
    compute_edit,
    insert_horizontal_rule,
    insert_link,
    insert_table,
    math_block,
    mermaid_block,
    toggle_bullet_list,
    toggle_heading,
    toggle_inline,
    toggle_ordered_list,
    toggle_quote,
    toggle_task_list,
    toggle_wrap,
)


def apply(text, edit):
    """Apply a TextEdit to a plain string, returning (new_text, sel)."""
    new_text = text[: edit.start] + edit.replacement + text[edit.end:]
    return new_text, (edit.sel_start, edit.sel_end)


# ── bold ───────────────────────────────────────────────────────────────


def test_bold_wraps_selection():
    text = "hello world"
    new, sel = apply(text, toggle_inline(text, 0, 5, BOLD))
    assert new == "**hello** world"
    assert sel == (2, 7)  # content stays selected


def test_bold_unwraps_selection_including_markers():
    text = "**hello** world"
    new, sel = apply(text, toggle_inline(text, 0, 9, BOLD))
    assert new == "hello world"
    assert sel == (0, 5)


def test_bold_unwraps_selection_inside_markers():
    text = "**hello** world"
    new, sel = apply(text, toggle_inline(text, 2, 7, BOLD))
    assert new == "hello world"
    assert sel == (0, 5)


def test_bold_without_selection_inserts_pair_and_removes_it():
    text = "ab"
    new, sel = apply(text, toggle_inline(text, 1, 1, BOLD))
    assert new == "a****b"
    assert sel == (3, 3)  # caret between the markers
    text2 = new
    new2, sel2 = apply(text2, toggle_inline(text2, 3, 3, BOLD))
    assert new2 == "ab"
    assert sel2 == (1, 1)


def test_bold_trims_whitespace_before_wrapping():
    text = "a hello b"
    new, _sel = apply(text, toggle_inline(text, 1, 8, BOLD))
    assert new == "a **hello** b"


# ── italic vs bold disambiguation ──────────────────────────────────────


def test_italic_wraps_and_unwraps():
    text = "hi there"
    new, sel = apply(text, toggle_inline(text, 0, 2, ITALIC))
    assert new == "*hi* there"
    assert sel == (1, 3)
    new2, _ = apply(new, toggle_inline(new, 0, 4, ITALIC))
    assert new2 == "hi there"


def test_italic_on_bold_text_adds_a_third_star():
    text = "**hello**"
    new, _sel = apply(text, toggle_inline(text, 0, 9, ITALIC))
    assert new == "***hello***"


def test_italic_unwrap_on_bold_italic_keeps_bold():
    text = "***hello***"
    new, _sel = apply(text, toggle_inline(text, 0, 11, ITALIC))
    assert new == "**hello**"


def test_bold_unwrap_on_bold_italic_keeps_italic():
    text = "***hello***"
    new, _sel = apply(text, toggle_inline(text, 0, 11, BOLD))
    assert new == "*hello*"


def test_italic_inside_markers_of_bold_selection_does_not_unwrap_bold():
    # "hello" selected inside "**hello**": italic must wrap, not strip bold.
    text = "**hello**"
    new, _sel = apply(text, toggle_inline(text, 2, 7, ITALIC))
    assert new == "***hello***"


# ── strikethrough / inline code ────────────────────────────────────────


def test_strikethrough_toggle():
    text = "gone"
    new, _ = apply(text, toggle_inline(text, 0, 4, STRIKE))
    assert new == "~~gone~~"
    new2, _ = apply(new, toggle_inline(new, 0, 8, STRIKE))
    assert new2 == "gone"


def test_inline_code_toggle_inside_and_outside():
    text = "`x`"
    new, _ = apply(text, toggle_inline(text, 0, 3, CODE))
    assert new == "x"
    text2 = "`x`"
    new2, _ = apply(text2, toggle_inline(text2, 1, 2, CODE))
    assert new2 == "x"


def test_inline_code_without_selection():
    text = ""
    new, sel = apply(text, toggle_inline(text, 0, 0, CODE))
    assert new == "``"
    assert sel == (1, 1)
    new2, _ = apply(new, toggle_inline(new, 1, 1, CODE))
    assert new2 == ""


# ── headings ───────────────────────────────────────────────────────────


def test_heading_set_toggle_and_switch_level():
    text = "Title"
    new, _ = apply(text, toggle_heading(text, 2, 2, 1))
    assert new == "# Title"
    new2, _ = apply(new, toggle_heading(new, 2, 2, 1))
    assert new2 == "Title"
    new3, _ = apply(new, toggle_heading(new, 2, 2, 2))
    assert new3 == "## Title"


def test_heading_multi_line_selection():
    text = "one\ntwo\nafter"
    new, sel = apply(text, toggle_heading(text, 0, 7, 3))
    assert new == "### one\n### two\nafter"
    assert sel == (0, len("### one\n### two"))


def test_heading_selection_ending_at_line_start_skips_that_line():
    text = "one\ntwo"
    new, _ = apply(text, toggle_heading(text, 0, 4, 1))
    assert new == "# one\ntwo"


# ── lists / quote ──────────────────────────────────────────────────────


def test_bullet_list_toggle_multi_line():
    text = "a\nb"
    new, _ = apply(text, toggle_bullet_list(text, 0, 3))
    assert new == "- a\n- b"
    new2, _ = apply(new, toggle_bullet_list(new, 0, len(new)))
    assert new2 == "a\nb"


def test_bullet_list_mixed_lines_adds_to_all():
    text = "- a\nb"
    new, _ = apply(text, toggle_bullet_list(text, 0, len(text)))
    assert new == "- - a\n- b"  # no normalizing, but nothing is lost


def test_ordered_list_toggle_and_renumber():
    text = "a\nb\nc"
    new, _ = apply(text, toggle_ordered_list(text, 0, len(text)))
    assert new == "1. a\n2. b\n3. c"
    new2, _ = apply(new, toggle_ordered_list(new, 0, len(new)))
    assert new2 == "a\nb\nc"


def test_ordered_list_renumbers_existing_numbers():
    text = "a\n7. b\nc"
    new, _ = apply(text, toggle_ordered_list(text, 0, len(text)))
    assert new == "1. a\n2. b\n3. c"


def test_task_list_toggle_including_checked():
    text = "a"
    new, _ = apply(text, toggle_task_list(text, 0, 1))
    assert new == "- [ ] a"
    checked = "- [x] a"
    new2, _ = apply(checked, toggle_task_list(checked, 0, len(checked)))
    assert new2 == "a"


def test_quote_toggle_single_line_caret_only():
    text = "line one\nline two"
    new, _ = apply(text, toggle_quote(text, 10, 10))
    assert new == "line one\n> line two"
    new2, _ = apply(new, toggle_quote(new, 12, 12))
    assert new2 == "line one\nline two"


# ── link ───────────────────────────────────────────────────────────────


def test_link_with_selection_selects_url():
    text = "see hello now"
    edit = insert_link(text, 4, 9)
    new, sel = apply(text, edit)
    assert new == "see [hello](url) now"
    assert new[sel[0]:sel[1]] == "url"


def test_link_without_selection_selects_placeholder_text():
    text = ""
    edit = insert_link(text, 0, 0)
    new, sel = apply(text, edit)
    assert new == "[文字](網址)"
    assert new[sel[0]:sel[1]] == "文字"


# ── code block ─────────────────────────────────────────────────────────


def test_code_block_wraps_selected_lines():
    text = "before\na\nb\nafter"
    new, _ = apply(text, code_block(text, 7, 10))
    assert new == "before\n```\na\nb\n```\nafter"


def test_code_block_without_selection_inserts_empty_block():
    text = ""
    edit = code_block(text, 0, 0)
    new, sel = apply(text, edit)
    assert new == "```\n\n```\n"
    assert sel == (4, 4)  # caret on the empty line inside the fences


# ── table / horizontal rule ────────────────────────────────────────────


def test_table_inserted_on_new_line_with_header_selected():
    text = "para"
    edit = insert_table(text, 2, 2)
    new, sel = apply(text, edit)
    assert new == "para\n| 標題1 | 標題2 |\n| --- | --- |\n|  |  |\n"
    assert new[sel[0]:sel[1]] == "標題1"


def test_table_on_empty_line_inserts_in_place():
    text = ""
    new, _ = apply(text, insert_table(text, 0, 0))
    assert new == "| 標題1 | 標題2 |\n| --- | --- |\n|  |  |\n"


def test_horizontal_rule_gets_blank_lines_around():
    text = "abc\ndef"
    new, _ = apply(text, insert_horizontal_rule(text, 1, 1))
    assert new == "abc\n\n---\n\ndef"


def test_horizontal_rule_on_blank_line_keeps_blank_above():
    text = "para\n\npara2"
    new, _ = apply(text, insert_horizontal_rule(text, 5, 5))
    assert new == "para\n\n---\n\npara2"


# ── mermaid block ──────────────────────────────────────────────────────


def test_mermaid_block_inserted_after_line_with_valid_starter_selected():
    text = "Alpha"
    new, sel = apply(text, mermaid_block(text, 5, 5))
    assert new == f"Alpha\n```mermaid\n{MERMAID_TEMPLATE}\n```\n"
    assert new[sel[0]:sel[1]] == "步驟一"
    assert new[:sel[0]].endswith("A[")


def test_mermaid_block_on_empty_line_inserts_in_place():
    text = ""
    new, sel = apply(text, mermaid_block(text, 0, 0))
    assert new == f"```mermaid\n{MERMAID_TEMPLATE}\n```\n"
    assert new[sel[0]:sel[1]] == "步驟一"


# ── math ───────────────────────────────────────────────────────────────


def test_math_inline_wraps_selection():
    text = "hello world"
    new, sel = apply(text, toggle_inline(text, 0, 5, MATH))
    assert new == "$hello$ world"
    assert sel == (1, 6)


def test_math_inline_unwraps_selection_including_markers():
    text = "$hello$ world"
    new, sel = apply(text, toggle_inline(text, 0, 7, MATH))
    assert new == "hello world"
    assert sel == (0, 5)


def test_math_inline_caret_inserts_pair_and_removes_it():
    text = "ab"
    new, sel = apply(text, toggle_inline(text, 1, 1, MATH))
    assert new == "a$$b"
    assert sel == (2, 2)  # caret between the dollars
    new2, sel2 = apply(new, toggle_inline(new, 2, 2, MATH))
    assert new2 == "ab"
    assert sel2 == (1, 1)


def test_math_block_inserted_with_blank_line_padding():
    # dollarmath needs a blank line between text and the $$ fences,
    # otherwise the block parses as part of the paragraph.
    text = "Alpha"
    new, sel = apply(text, math_block(text, 5, 5))
    assert new == "Alpha\n\n$$\n\n$$\n"
    assert sel == (10, 10)  # caret on the empty line between the fences


def test_math_block_next_to_text_renders_as_display_math():
    from app.md_converter import render_body

    text = "Alpha"
    new, _sel = apply(text, math_block(text, 5, 5))
    filled = new.replace("$$\n\n$$", "$$\nE=mc^2\n$$")
    rendered = render_body(filled)
    assert 'class="math block"' in rendered.body


# ── wikilink ───────────────────────────────────────────────────────────


def test_wikilink_caret_inserts_brackets_with_cursor_inside():
    text = "ab"
    new, sel = apply(text, toggle_wrap(text, 1, 1, "[[", "]]", caret_after=True))
    assert new == "a[[]]b"
    assert sel == (3, 3)  # between the brackets


def test_wikilink_wraps_selection_with_cursor_after():
    text = "note x"
    new, sel = apply(text, toggle_wrap(text, 0, 4, "[[", "]]", caret_after=True))
    assert new == "[[note]] x"
    assert sel == (8, 8)  # caret after the closing brackets


def test_wikilink_unwraps_exactly_wrapped_selection():
    text = "[[note]] x"
    new, sel = apply(text, toggle_wrap(text, 0, 8, "[[", "]]", caret_after=True))
    assert new == "note x"
    assert sel == (0, 4)


def test_wikilink_unwraps_when_markers_sit_outside_selection():
    text = "[[note]]"
    new, sel = apply(text, toggle_wrap(text, 2, 6, "[[", "]]", caret_after=True))
    assert new == "note"
    assert sel == (0, 4)


def test_wikilink_caret_between_empty_brackets_removes_them():
    text = "a[[]]b"
    new, sel = apply(text, toggle_wrap(text, 3, 3, "[[", "]]", caret_after=True))
    assert new == "ab"
    assert sel == (1, 1)


# ── highlight (<mark>) ─────────────────────────────────────────────────


def test_highlight_wraps_selection():
    text = "hi x"
    new, sel = apply(text, toggle_wrap(text, 0, 2, MARK_OPEN, MARK_CLOSE))
    assert new == "<mark>hi</mark> x"
    assert sel == (6, 8)  # content stays selected


def test_highlight_unwraps_selection_including_tags():
    text = "<mark>hi</mark> x"
    new, sel = apply(text, toggle_wrap(text, 0, 15, MARK_OPEN, MARK_CLOSE))
    assert new == "hi x"
    assert sel == (0, 2)


def test_highlight_unwraps_when_tags_sit_outside_selection():
    text = "<mark>hi</mark>"
    new, sel = apply(text, toggle_wrap(text, 6, 8, MARK_OPEN, MARK_CLOSE))
    assert new == "hi"
    assert sel == (0, 2)


def test_highlight_caret_inserts_pair_and_removes_it():
    text = "ab"
    new, sel = apply(text, toggle_wrap(text, 1, 1, MARK_OPEN, MARK_CLOSE))
    assert new == "a<mark></mark>b"
    assert sel == (7, 7)  # caret between the tags
    new2, sel2 = apply(new, toggle_wrap(new, 7, 7, MARK_OPEN, MARK_CLOSE))
    assert new2 == "ab"
    assert sel2 == (1, 1)


def test_highlight_trims_whitespace_before_wrapping():
    text = "a hi b"
    new, _sel = apply(text, toggle_wrap(text, 1, 4, MARK_OPEN, MARK_CLOSE))
    assert new == "a <mark>hi</mark> b"


# ── dispatch ───────────────────────────────────────────────────────────


def test_compute_edit_dispatches_every_action():
    text = "hello"
    for action in (
        "bold", "italic", "strikethrough", "inline_code",
        "h1", "h2", "h3", "bullet_list", "ordered_list", "task_list",
        "quote", "link", "table", "hr", "code_block",
        "mermaid", "math_inline", "math_block", "wikilink", "highlight",
    ):
        assert compute_edit(action, text, 0, 5) is not None
    assert compute_edit("nope", text, 0, 5) is None
