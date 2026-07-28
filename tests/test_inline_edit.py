"""Pure line-range surgery behind preview inline editing (app/inline_edit.py)."""

from app.inline_edit import (
    extract_source_lines,
    normalize_newlines,
    replace_source_lines,
)

DOC = "# Title\n\nfirst para\nsecond line\n\n- a\n- b\n"


def test_extract_single_line():
    assert extract_source_lines(DOC, 0, 0) == "# Title"


def test_extract_multi_line_range_is_inclusive():
    assert extract_source_lines(DOC, 2, 3) == "first para\nsecond line"


def test_extract_rejects_out_of_range():
    assert extract_source_lines(DOC, 2, 99) is None
    assert extract_source_lines(DOC, -1, 0) is None
    assert extract_source_lines(DOC, 3, 2) is None  # end before start


def test_extract_normalizes_crlf_before_splitting():
    assert extract_source_lines("a\r\nb\r\nc", 1, 1) == "b"


def test_replace_same_line_count():
    out = replace_source_lines(DOC, 0, 0, "# Title", "# New Title")
    assert out == "# New Title\n\nfirst para\nsecond line\n\n- a\n- b\n"


def test_replace_grows_line_count():
    out = replace_source_lines(DOC, 2, 3, "first para\nsecond line", "one\ntwo\nthree")
    assert out.split("\n")[2:5] == ["one", "two", "three"]
    assert out.endswith("- a\n- b\n")


def test_replace_shrinks_line_count():
    out = replace_source_lines(DOC, 2, 3, "first para\nsecond line", "merged")
    assert out == "# Title\n\nmerged\n\n- a\n- b\n"


def test_replace_with_empty_text_deletes_the_block():
    out = replace_source_lines(DOC, 5, 6, "- a\n- b", "")
    assert out == "# Title\n\nfirst para\nsecond line\n\n"


def test_replace_refuses_when_original_does_not_match():
    # The classic drift case: the file changed under a stale rendering.
    assert replace_source_lines(DOC, 0, 0, "# Stale Title", "# New") is None


def test_replace_refuses_out_of_range():
    assert replace_source_lines(DOC, 20, 21, "anything", "x") is None


def test_replace_preserves_crlf_line_endings():
    crlf_source = normalize_newlines("alpha\r\nbeta\r\ngamma")
    out = replace_source_lines(crlf_source, 1, 1, "beta", "BETA", newline="\r\n")
    assert out == "alpha\r\nBETA\r\ngamma"
    assert "\n" not in out.replace("\r\n", "")


def test_replace_accepts_crlf_inside_the_submitted_text():
    # A browser may hand back CRLF; it must not defeat the equality check
    # nor leak stray CRs into an LF document.
    out = replace_source_lines(DOC, 2, 3, "first para\r\nsecond line", "one\r\ntwo")
    assert out == "# Title\n\none\ntwo\n\n- a\n- b\n"


def test_replace_keeps_the_rest_of_the_document_byte_for_byte():
    out = replace_source_lines(DOC, 5, 6, "- a\n- b", "- a\n- b\n- c")
    assert out.startswith("# Title\n\nfirst para\nsecond line\n\n")
    assert out == "# Title\n\nfirst para\nsecond line\n\n- a\n- b\n- c\n"
