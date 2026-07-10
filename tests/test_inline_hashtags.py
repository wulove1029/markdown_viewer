"""Inline Markdown hashtag parsing tests."""

from app.md_converter import body_hashtags


def test_body_hashtags_require_line_start_or_whitespace_and_stop_at_punctuation():
    text = "#alpha word #beta, x#ignored (#also_ignored)\n中文 #標籤。 #"

    assert body_hashtags(text) == ["alpha", "beta", "標籤"]


def test_body_hashtags_ignore_fenced_and_inline_code_and_atx_heading_marker():
    text = """# Heading
outside #kept and `inline #hidden`
```
#fenced
text #also_hidden
```
## Another heading
after ``code #hidden_too`` #final
"""

    assert body_hashtags(text) == ["kept", "final"]


def test_body_hashtags_are_unique_case_insensitively_in_first_seen_order():
    assert body_hashtags("#Tag #tag #另一個 #Tag") == ["Tag", "另一個"]
