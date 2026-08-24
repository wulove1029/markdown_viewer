"""Guard rails for the ``render_body`` / ``_wrap`` split in ``md_converter``.

Splitting "render the body" out of "wrap it in a document" had to be
byte-exact: every preview ``convert_text`` produces must stay identical, or the
whole rendering stack shifts under the inline-edit and export code. These tests
pin that down for a document per Markdown feature, in both themes, and assert
the properties the incremental-save path depends on -- the body fragment
carries no document chrome, is theme-independent, is a pure function of the
text, and is cached once per file rather than once per (file, theme).
"""

import pytest

from app import md_converter
from app.md_converter import (
    RenderedBody,
    _wrap,
    convert,
    convert_body,
    convert_text,
    render_body,
    set_user_css,
    state_page_html,
)

# --- fixture documents, one per Markdown feature ---

FRONT_MATTER = """---
title: 專案筆記
author: "Jerry"
tags: [alpha, beta]
status:
  - draft
  - review
---

# 標題一

正文段落，含 *斜體* 與 **粗體**。
"""

TABLE = """## 表格

| 欄位 | 型別 | 說明 |
| --- | :---: | ---: |
| id | int | 主鍵 |
| name | str | 名稱 |
| `code` | **str** | 內含格式 |
"""

MERMAID = """# 流程

```mermaid
graph TD
    A[開始] --> B{判斷}
    B -->|是| C[執行]
    B -->|否| D[結束]
```

後續文字。
"""

MATH = """## 數學

行內 $a^2 + b^2 = c^2$ 混排。

$$
\\int_{0}^{\\infty} e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}
$$

結束。
"""

CODE_BLOCK = """### 程式碼

```python
def greet(name: str) -> str:
    return f"hello {name}"
```

```
沒有語言標記的區塊
```

```notalanguage
fallback lexer
```
"""

TASK_LIST = """# 待辦

- [ ] 未完成項目
- [x] 已完成項目
    - [ ] 巢狀子項
- 一般項目
"""

CALLOUT = """> [!note] 提示標題
> 這是 callout 內文。
> 第二行。

> [!warning]
> 沒有標題的警告。
"""

FOOTNOTE = """本文有註腳[^1]，也有第二個[^note]。

[^1]: 第一個註腳內容。
[^note]: 第二個註腳，含 `code`。
"""

DETAILS = """<details>
<summary>展開看看</summary>

內部是 **Markdown**。

</details>
"""

WIKILINK = """看看 [[目標筆記]] 與 [[path/to/note|別名]]。

裸連結 https://example.com/x 也會被 linkify。
"""

DEF_LIST = """術語
: 定義內容一

另一術語
: 定義內容二
"""

KITCHEN_SINK = (
    FRONT_MATTER
    + "\n"
    + TABLE
    + "\n"
    + MERMAID
    + "\n"
    + MATH
    + "\n"
    + CODE_BLOCK
    + "\n"
    + TASK_LIST
    + "\n"
    + CALLOUT
    + "\n"
    + FOOTNOTE
    + "\n"
    + DETAILS
    + "\n"
    + WIKILINK
    + "\n"
    + DEF_LIST
    + "\n## 標題一\n\n重複標題以測試 anchor 去重。\n"
)

EMPTY = ""

PLAIN = "只有一行純文字。\n"

SAMPLES = {
    "front_matter": FRONT_MATTER,
    "table": TABLE,
    "mermaid": MERMAID,
    "math": MATH,
    "code_block": CODE_BLOCK,
    "task_list": TASK_LIST,
    "callout": CALLOUT,
    "footnote": FOOTNOTE,
    "details": DETAILS,
    "wikilink": WIKILINK,
    "def_list": DEF_LIST,
    "kitchen_sink": KITCHEN_SINK,
    "empty": EMPTY,
    "plain": PLAIN,
}

THEMES = ("light", "dark")
NAMES = sorted(SAMPLES)

# Markers that belong to the document wrapper, never to the body fragment.
CHROME_MARKERS = (
    "<!DOCTYPE",
    "<html",
    "</head>",
    "<style",
    "<script",
    "a.wikilink {",       # a slice of _FULL_CSS
    ".callout-title",     # another slice of _FULL_CSS
    "mermaid.initialize",  # trailing mermaid loader
    "katex",               # trailing KaTeX loader
)


@pytest.fixture(autouse=True)
def _clean_converter_state():
    """Every test starts with an empty cache and no user stylesheet."""
    set_user_css("")  # set_user_css also clears _CONVERT_CACHE
    yield
    set_user_css("")


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", NAMES)
def test_wrapped_render_body_is_byte_identical_to_convert_text(name, theme):
    """The refactor's contract: _wrap(render_body(t)) == convert_text(t), exactly."""
    text = SAMPLES[name]
    rendered = render_body(text)
    rebuilt = _wrap(
        rendered.body,
        "preview",
        theme,
        mermaid=rendered.mermaid,
        code_copy=rendered.code_copy,
        math=rendered.math,
    )
    html, headings = convert_text(text, theme)
    assert rebuilt == html
    assert rendered.headings == headings


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("name", NAMES)
def test_convert_matches_convert_text_for_the_same_file(name, theme, tmp_path):
    """convert() still returns the identical full document convert_text() does."""
    path = tmp_path / f"{name}.md"
    path.write_text(SAMPLES[name], encoding="utf-8")
    from_file = convert(path, theme)
    from_text = convert_text(SAMPLES[name], theme, title=path.stem)
    assert from_file[0] == from_text[0]
    assert from_file[1] == from_text[1]


@pytest.mark.parametrize("name", NAMES)
def test_body_fragment_has_no_document_chrome(name):
    body = render_body(SAMPLES[name]).body
    lowered = body.lower()
    for marker in CHROME_MARKERS:
        assert marker.lower() not in lowered, f"{name}: body leaked {marker!r}"
    assert md_converter._FULL_CSS not in body


@pytest.mark.parametrize("name", NAMES)
def test_render_body_is_pure(name):
    """Same text in, equal RenderedBody out -- no hidden state between calls."""
    first = render_body(SAMPLES[name])
    second = render_body(SAMPLES[name])
    assert first == second
    assert isinstance(first, RenderedBody)


@pytest.mark.parametrize("theme", THEMES)
def test_body_fragment_is_theme_independent(theme):
    """theme reaches _wrap only: the same fragment sits inside both documents."""
    text = SAMPLES["kitchen_sink"]
    rendered = render_body(text)
    html, _ = convert_text(text, theme)
    assert rendered.body in html


def test_light_and_dark_share_one_body_but_differ_in_chrome():
    text = SAMPLES["kitchen_sink"]
    body = render_body(text).body
    light, _ = convert_text(text, "light")
    dark, _ = convert_text(text, "dark")
    assert body in light and body in dark
    assert light != dark
    assert 'class="theme-light"' in light and 'class="theme-dark"' in dark


def test_feature_flags_match_the_loaders_wrap_injects():
    plain = render_body(SAMPLES["plain"])
    assert not (plain.mermaid or plain.code_copy or plain.math)

    assert render_body(SAMPLES["mermaid"]).mermaid
    assert render_body(SAMPLES["code_block"]).code_copy
    assert render_body(SAMPLES["math"]).math

    sink = render_body(SAMPLES["kitchen_sink"])
    assert sink.mermaid and sink.code_copy and sink.math


@pytest.mark.parametrize("name", NAMES)
def test_convert_body_matches_render_body_of_the_file_text(name, tmp_path):
    path = tmp_path / f"{name}.md"
    path.write_text(SAMPLES[name], encoding="utf-8")
    assert convert_body(path) == render_body(path.read_text(encoding="utf-8"))


def test_convert_body_returns_none_when_the_file_is_unusable(tmp_path):
    assert convert_body(tmp_path / "nope.md") is None
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    assert convert_body(big) is None
    undecodable = tmp_path / "bad.md"
    undecodable.write_bytes(b"\xff\xfe\x00\x00 \xff\xff")
    assert convert_body(undecodable) is None


def test_body_is_parsed_once_for_both_themes(tmp_path, monkeypatch):
    """The cache key dropped `theme`, so a second theme must not re-parse."""
    path = tmp_path / "cached.md"
    path.write_text(SAMPLES["kitchen_sink"], encoding="utf-8")

    calls = []
    real_render = md_converter._PARSER.render

    def counting_render(text, env=None):
        calls.append(text)
        return real_render(text) if env is None else real_render(text, env)

    monkeypatch.setattr(md_converter._PARSER, "render", counting_render)

    light, _ = convert(path, "light")
    assert len(calls) == 1
    dark, _ = convert(path, "dark")
    assert len(calls) == 1  # different theme, same cached body
    assert convert(path, "light")[0] == light
    assert len(calls) == 1
    assert convert_body(path) is not None
    assert len(calls) == 1  # convert_body shares the same cache entry
    assert light != dark


def test_cache_still_reparses_after_the_file_changes(tmp_path):
    path = tmp_path / "edited.md"
    path.write_text("# One\n", encoding="utf-8")
    first = convert_body(path)
    import os

    stat = path.stat()
    path.write_text("# Two\n", encoding="utf-8")
    if path.stat().st_mtime_ns == stat.st_mtime_ns:  # coarse clock: force a bump
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000))
    second = convert_body(path)
    assert first is not None and second is not None
    assert "One" in first.body
    assert "Two" in second.body


def test_user_css_reaches_a_document_built_from_a_cached_body(tmp_path):
    path = tmp_path / "styled.md"
    path.write_text("# Styled\n", encoding="utf-8")
    before, _ = convert(path)
    assert "rebeccapurple" not in before
    set_user_css("body { color: rebeccapurple }")
    after, _ = convert(path)
    assert "rebeccapurple" in after


def test_state_pages_do_not_go_through_render_body():
    """Error/state pages keep their own body shape and never touch render_body."""
    page = state_page_html("標題", "訊息", "light", "錯誤")
    assert '<main class="state-page">' in page
    assert page.startswith("<!DOCTYPE html>")
    for name in NAMES:
        assert "state-page" not in render_body(SAMPLES[name]).body


def test_convert_text_still_returns_a_full_document():
    html, headings = convert_text(SAMPLES["kitchen_sink"])
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert headings and all(len(h) == 3 for h in headings)
