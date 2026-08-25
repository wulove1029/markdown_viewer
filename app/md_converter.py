"""Markdown to self-contained HTML converter."""

import codecs
from dataclasses import dataclass
from html import escape
import json
import re
import threading
import unicodedata
import urllib.parse
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name

_CSS_PATH = Path(__file__).parent.parent / "assets" / "obsidian-light.css"
_MERMAID_JS = _CSS_PATH.parent / "mermaid.min.js"
_KATEX_DIR = _CSS_PATH.parent / "katex"
_KATEX_JS = _KATEX_DIR / "katex.min.js"
_KATEX_CSS = _KATEX_DIR / "katex.min.css"
_PYGMENTS_CSS = HtmlFormatter(style="one-dark").get_style_defs(".highlight")

try:
    _THEME_CSS = _CSS_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    _THEME_CSS = "body { font-family: sans-serif; padding: 2em; }"

_WIKILINK_CSS = (
    "a.wikilink { text-decoration: none; border-bottom: 1px dashed currentColor; }"
    "a.wikilink:hover { border-bottom-style: solid; }"
)
_CALLOUT_CSS = """
.callout { border: 1px solid rgba(128,128,128,.25); border-left: 4px solid var(--cl, #888);
    border-radius: 6px; padding: 8px 14px; margin: 14px 0; background: var(--cl-bg, rgba(128,128,128,.06)); }
.callout .callout-title { display: block; font-weight: 700; margin-bottom: 2px; color: var(--cl, #666); }
.callout > :first-child:not(.callout-title) { margin-top: 0; }
.callout > p:last-child { margin-bottom: 0; }
.callout-note,.callout-info,.callout-important { --cl: #3b82f6; --cl-bg: rgba(59,130,246,.08); }
.callout-tip,.callout-hint,.callout-success,.callout-done,.callout-check { --cl: #10b981; --cl-bg: rgba(16,185,129,.08); }
.callout-warning,.callout-caution,.callout-attention,.callout-todo { --cl: #f59e0b; --cl-bg: rgba(245,158,11,.10); }
.callout-danger,.callout-error,.callout-bug,.callout-failure,.callout-fail,.callout-missing { --cl: #ef4444; --cl-bg: rgba(239,68,68,.08); }
.callout-question,.callout-faq,.callout-help { --cl: #8b5cf6; --cl-bg: rgba(139,92,246,.08); }
.callout-quote,.callout-cite,.callout-abstract,.callout-summary,.callout-tldr,.callout-example { --cl: #6b7280; --cl-bg: rgba(107,114,128,.08); }
kbd { background: rgba(128,128,128,.18); border: 1px solid rgba(128,128,128,.35); border-radius: 4px;
    padding: 1px 5px; font-size: .85em; font-family: "Cascadia Code", Consolas, monospace; }
details { border: 1px solid rgba(128,128,128,.25); border-radius: 6px; padding: 6px 12px; margin: 12px 0; }
summary { cursor: pointer; font-weight: 600; }
.frontmatter { border: 1px solid rgba(128,128,128,.25); border-radius: 6px; padding: 8px 14px;
    margin: 0 0 18px; background: rgba(128,128,128,.05); font-size: .9em; }
.frontmatter .fm-row { display: flex; gap: 10px; padding: 2px 0; }
.frontmatter .fm-key { min-width: 90px; font-weight: 600; opacity: .8; }
.frontmatter .fm-val { flex: 1; }
.frontmatter .fm-tag { display: inline-block; background: rgba(128,128,128,.15);
    border-radius: 10px; padding: 0 8px; margin: 0 4px 2px 0; font-size: .9em; }
"""
_FULL_CSS = f"{_THEME_CSS}\n{_PYGMENTS_CSS}\n{_WIKILINK_CSS}\n{_CALLOUT_CSS}"

# Optional user stylesheet (set from Preferences) appended after the bundled CSS
# so it can override the defaults.
_user_css = ""
_CONVERT_LOCK = threading.RLock()


def set_user_css(css: str) -> None:
    global _user_css
    with _CONVERT_LOCK:
        _user_css = css or ""
        _CONVERT_CACHE.clear()  # cached HTML embeds the old stylesheet
_FORMATTER = HtmlFormatter(style="one-dark")


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    if lang and lang.lower() == "mermaid":
        # Hand the raw diagram source to mermaid.js (rendered client-side).
        # Leading "<pre" makes markdown-it emit this verbatim, unwrapped.
        # data-diagram keeps the source so we can re-render on theme switch.
        src = escape(code.strip())
        return f'<pre class="mermaid" data-diagram="{src}">{src}</pre>'
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except Exception:
        lexer = TextLexer()
    return highlight(code, lexer, _FORMATTER)


def _mermaid_script(theme: str) -> str:
    """Inline loader for the bundled mermaid.js (no-op if the asset is absent)."""
    if not _MERMAID_JS.exists():
        return ""
    src = _MERMAID_JS.resolve().as_uri()
    mtheme = "dark" if theme == "dark" else "default"
    return (
        f'<script src="{src}"></script>\n'
        "<script>\n"
        "(function() {\n"
        "  function render() {\n"
        "    if (!window.mermaid) { return; }\n"
        f"    window.mermaid.initialize({{ startOnLoad: false, theme: {json.dumps(mtheme)} }});\n"
        '    try { window.mermaid.run({ querySelector: ".mermaid" }); }\n'
        "    catch (e) { console.error(e); }\n"
        "  }\n"
        '  if (document.readyState === "loading") {\n'
        '    document.addEventListener("DOMContentLoaded", render);\n'
        "  } else { render(); }\n"
        "})();\n"
        "</script>"
    )


def _copy_button_script() -> str:
    """Client-side 'copy' affordance for each Pygments code block."""
    return """
<style>
div.highlight { position: relative; }
div.highlight .copy-btn {
    position: absolute; top: 6px; right: 6px; opacity: 0;
    transition: opacity .15s; font-size: 12px; padding: 2px 8px;
    border-radius: 6px; border: 1px solid rgba(127,127,127,.4);
    background: rgba(127,127,127,.15); color: inherit; cursor: pointer;
}
div.highlight:hover .copy-btn { opacity: 1; }
div.highlight .copy-btn:hover { background: rgba(127,127,127,.3); }
</style>
<script>
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') { fn(); }
    else { document.addEventListener('DOMContentLoaded', fn); }
  }
  ready(function () {
    document.querySelectorAll('div.highlight').forEach(function (block) {
      if (block.querySelector('.copy-btn')) { return; }
      var btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.type = 'button';
      btn.textContent = '複製';
      btn.addEventListener('click', function () {
        var pre = block.querySelector('pre');
        var text = pre ? pre.innerText : '';
        navigator.clipboard.writeText(text).then(function () {
          btn.textContent = '已複製';
          setTimeout(function () { btn.textContent = '複製'; }, 1500);
        }).catch(function () { btn.textContent = '失敗'; });
      });
      block.appendChild(btn);
    });
  });
})();
</script>"""


def _katex_html() -> str:
    """Offline KaTeX loader; renders dollarmath ``.math`` elements client-side."""
    if not _KATEX_JS.exists() or not _KATEX_CSS.exists():
        return ""
    css = _KATEX_CSS.resolve().as_uri()
    js = _KATEX_JS.resolve().as_uri()
    return (
        f'<link rel="stylesheet" href="{css}">\n'
        f'<script src="{js}"></script>\n'
        "<script>\n"
        "(function () {\n"
        "  function render() {\n"
        "    if (!window.katex) { return; }\n"
        "    document.querySelectorAll('.math.inline, .math.block').forEach(function (el) {\n"
        "      if (el.dataset.katexDone) { return; }\n"
        "      var display = el.classList.contains('block');\n"
        "      try {\n"
        "        window.katex.render(el.textContent, el, { displayMode: display, throwOnError: false });\n"
        "        el.dataset.katexDone = '1';\n"
        "      } catch (e) { console.error(e); }\n"
        "    });\n"
        "  }\n"
        "  if (document.readyState === 'loading') {\n"
        "    document.addEventListener('DOMContentLoaded', render);\n"
        "  } else { render(); }\n"
        "})();\n"
        "</script>"
    )


_CALLOUT_RE = re.compile(r"^\[!([\w-]+)\]([+-]?)\s*(.*)$")
_CALLOUT_TITLES = {
    "note": "備註", "info": "資訊", "tip": "提示", "hint": "提示",
    "warning": "警告", "caution": "注意", "attention": "注意",
    "danger": "危險", "error": "錯誤", "bug": "錯誤",
    "important": "重要", "success": "成功", "done": "完成", "check": "完成",
    "question": "問題", "faq": "問題", "help": "問題",
    "example": "範例", "quote": "引用", "cite": "引用",
    "abstract": "摘要", "summary": "摘要", "tldr": "摘要",
    "failure": "失敗", "fail": "失敗", "missing": "缺少", "todo": "待辦",
}


def _callout_plugin(md: MarkdownIt) -> None:
    """Render Obsidian-style ``> [!note] Title`` blockquotes as callout boxes."""

    def apply_title(inline, title: str):
        children = inline.children or []
        cut = len(children)
        for idx, child in enumerate(children):
            if child.type in ("softbreak", "hardbreak"):
                cut = idx + 1
                break
        title_tok = Token("html_inline", "", 0)
        # <span> (not <div>) keeps the HTML valid inside the paragraph.
        title_tok.content = f'<span class="callout-title">{escape(title)}</span>'
        inline.children = [title_tok] + children[cut:]
        parts = inline.content.split("\n", 1)
        inline.content = parts[1] if len(parts) > 1 else ""

    def rule(state):
        tokens = state.tokens
        i = 0
        while i < len(tokens):
            if tokens[i].type != "blockquote_open":
                i += 1
                continue
            inline = None
            for k in range(i + 1, len(tokens)):
                if tokens[k].type == "inline":
                    inline = tokens[k]
                    break
                if tokens[k].type in ("blockquote_open", "blockquote_close"):
                    break
            match = _CALLOUT_RE.match(inline.content.split("\n", 1)[0].strip()) if inline else None
            if not match:
                i += 1
                continue
            ctype = match.group(1).lower()
            title = match.group(3).strip() or _CALLOUT_TITLES.get(ctype, ctype.capitalize())
            tokens[i].tag = "div"
            tokens[i].attrSet("class", f"callout callout-{ctype}")
            depth = 0
            for k in range(i, len(tokens)):
                if tokens[k].type == "blockquote_open":
                    depth += 1
                elif tokens[k].type == "blockquote_close":
                    depth -= 1
                    if depth == 0:
                        tokens[k].tag = "div"
                        break
            apply_title(inline, title)
            i += 1

    md.core.ruler.push("callout", rule)


# A deliberately tiny allowlist of safe HTML so notes can use <kbd>, <mark>,
# super/subscript, etc. without enabling raw HTML wholesale. Only these fixed
# tags pass through; no scriptable attributes are ever allowed.
_SAFE_INLINE_RE = re.compile(
    r"</?(?:kbd|sub|sup|mark|ins|del)>|<br\s*/?>|"
    r'<abbr\s+title="[^"<>]*">|</abbr>',
    re.IGNORECASE,
)
_DETAILS_OPEN_RE = re.compile(r"^<details(?:\s+open)?>$", re.IGNORECASE)
_DETAILS_CLOSE_RE = re.compile(r"^</details>$", re.IGNORECASE)
_SUMMARY_LINE_RE = re.compile(r"^<summary>(.*)</summary>$", re.IGNORECASE)
_SUMMARY_OPEN_RE = re.compile(r"^<summary>$", re.IGNORECASE)
_SUMMARY_CLOSE_RE = re.compile(r"^</summary>$", re.IGNORECASE)


def _safe_html_plugin(md: MarkdownIt) -> None:
    def inline_rule(state, silent):
        if state.src[state.pos] != "<":
            return False
        match = _SAFE_INLINE_RE.match(state.src, state.pos)
        if not match:
            return False
        if not silent:
            token = state.push("html_inline", "", 0)
            token.content = match.group(0)
        state.pos = match.end()
        return True

    def block_rule(state, start, end, silent):
        pos = state.bMarks[start] + state.tShift[start]
        line = state.src[pos:state.eMarks[start]].strip()
        summary = _SUMMARY_LINE_RE.match(line)
        if _DETAILS_OPEN_RE.match(line):
            out = line
        elif _DETAILS_CLOSE_RE.match(line):
            out = "</details>"
        elif summary:
            out = f"<summary>{escape(summary.group(1))}</summary>"
        elif _SUMMARY_OPEN_RE.match(line):
            out = "<summary>"
        elif _SUMMARY_CLOSE_RE.match(line):
            out = "</summary>"
        else:
            return False
        if silent:
            return True
        token = state.push("html_block", "", 0)
        token.map = [start, start + 1]
        token.content = out + "\n"
        state.line = start + 1
        return True

    md.inline.ruler.before("autolink", "safe_html_inline", inline_rule)
    md.block.ruler.before("paragraph", "safe_html_block", block_rule)


def _tasklist_line_plugin(md: MarkdownIt) -> None:
    """Tag each task-list checkbox with its source line (``data-line``).

    Lets the renderer tell Python which ``- [ ]`` line to rewrite when a
    checkbox is toggled in the preview.
    """

    def add_lines(state):
        for token in state.tokens:
            if token.type != "inline" or not token.map or not token.children:
                continue
            child = token.children[0]
            if child.type == "html_inline" and "task-list-item-checkbox" in child.content:
                child.content = child.content.replace(
                    "<input ", f'<input data-line="{token.map[0]}" ', 1
                )

    md.core.ruler.push("tasklist_line", add_lines)


# Token types whose renderer emits raw markup and therefore ignores the
# attributes set on the token; they need the source range spliced into the
# rendered string instead (see ``_src_range_rule``).
_RAW_OUTPUT_TOKENS = ("fence", "html_block", "math_block")
_OPEN_TAG_RE = re.compile(r"<([a-zA-Z][\w:-]*)")
# An html_block that opens an element it does not close (``<details>``) renders
# as a wrapper around blocks it does not own, so editing "it" would offer one
# line while hiding many. Only self-contained fragments get a source range.
_BALANCED_HTML_RE = re.compile(
    r"^<([a-zA-Z][\w:-]*)(?:\s[^>]*)?>.*</\1>$", re.DOTALL
)


def _inject_src_attrs(html: str, start: int, end: int) -> str:
    """Splice ``data-src-*`` into the first opening tag of *html*.

    Returns *html* unchanged when it does not begin with an opening tag (a
    lone ``</details>`` html_block, for instance).
    """
    match = _OPEN_TAG_RE.match(html)
    if not match:
        return html
    attrs = f' data-src-start="{start}" data-src-end="{end}"'
    return html[: match.end()] + attrs + html[match.end() :]


def _src_range_rule(inner):
    """Wrap a renderer rule so the token's source range lands in the output."""

    def rule(tokens, idx, options, env):
        out = inner(tokens, idx, options, env)
        src_range = (tokens[idx].meta or {}).get("srcRange")
        if not src_range:
            return out
        return _inject_src_attrs(out, src_range[0], src_range[1])

    return rule


def _source_line_plugin(md: MarkdownIt) -> None:
    """Tag every top-level block with the source lines it was built from.

    Emits ``data-src-start`` / ``data-src-end`` as **0-based, inclusive** line
    indices into the original Markdown (front matter included in the count, so
    the numbers match ``text.split("\\n")`` exactly). The preview uses them to
    ask Python for the raw Markdown behind a block and to write an inline edit
    back to the right lines. Front matter itself is deliberately untagged, and
    nested blocks are skipped so a double-click always resolves to the
    outermost block.
    """

    def add_ranges(state):
        lines = state.src.split("\n")
        for token in state.tokens:
            if token.level != 0 or token.hidden or not token.map:
                continue
            # Closing tokens carry no map; front matter must stay uneditable.
            if token.nesting < 0 or token.type == "front_matter":
                continue
            if token.type == "html_block" and not _BALANCED_HTML_RE.match(
                token.content.strip()
            ):
                continue
            start, stop = token.map[0], token.map[1]
            end = stop - 1
            # Some block maps swallow the blank separator lines that follow
            # (list blocks notably do); keep the range on real content.
            while end > start and 0 <= end < len(lines) and not lines[end].strip():
                end -= 1
            if start < 0 or end < start:
                continue
            token.meta = dict(token.meta or {})
            token.meta["srcRange"] = (start, end)
            if token.type not in _RAW_OUTPUT_TOKENS:
                token.attrSet("data-src-start", str(start))
                token.attrSet("data-src-end", str(end))

    md.core.ruler.push("source_line", add_ranges)

    for name in _RAW_OUTPUT_TOKENS:
        inner = md.renderer.rules.get(name)
        if inner is not None:
            md.renderer.rules[name] = _src_range_rule(inner)


def _wikilink_plugin(md: MarkdownIt) -> None:
    """Parse ``[[target]]`` / ``[[target|alias]]`` into wiki-link anchors.

    Rendered as ``<a class="wikilink" href="wikilink:<target>">label</a>`` so
    the renderer can intercept the click and open/create the target note.
    """

    def rule(state, silent):
        pos = state.pos
        if state.src[pos : pos + 2] != "[[":
            return False
        end = state.src.find("]]", pos + 2)
        if end < 0:
            return False
        inner = state.src[pos + 2 : end]
        if "[" in inner or "]" in inner or not inner.strip():
            return False
        if not silent:
            token = state.push("wikilink", "", 0)
            token.content = inner
        state.pos = end + 2
        return True

    def render(tokens, idx, options, env):
        inner = tokens[idx].content
        target, _, alias = inner.partition("|")
        target = target.strip()
        label = alias.strip() or target
        href = "wikilink:" + urllib.parse.quote(target, safe="")
        return f'<a class="wikilink" href="{href}">{escape(label)}</a>'

    md.inline.ruler.before("link", "wikilink", rule)
    md.renderer.rules["wikilink"] = render


def _build_parser() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {
            "html": False,
            "linkify": True,       # turn bare URLs into clickable links
            "typographer": True,   # smart quotes, dashes, ellipses
            "highlight": _highlight_code,
        },
    )
    md.enable("table")
    md.enable("strikethrough")
    md.enable("linkify")
    md = md.use(tasklists_plugin, enabled=True)  # interactive checkboxes
    md = md.use(_tasklist_line_plugin)
    md = md.use(front_matter_plugin)
    md = md.use(footnote_plugin)
    md = md.use(deflist_plugin)
    md = md.use(dollarmath_plugin)  # $inline$ and $$block$$ math
    md = md.use(_wikilink_plugin)   # [[note]] wiki-links
    md = md.use(_callout_plugin)    # > [!note] callouts
    md = md.use(_safe_html_plugin)  # small safe-HTML allowlist
    # Last: it stamps the final tags, so it must see every other plugin's
    # rewrites (callouts turn blockquotes into divs, for example).
    md = md.use(_source_line_plugin)  # data-src-start/end for inline editing
    return md


_PARSER = _build_parser()


def _slugify(text: str) -> str:
    """Convert heading text to a URL-safe anchor id."""
    text = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", text.lower())
    return re.sub(r"[\s]+", "-", text.strip())


def _inject_anchors(html: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Add id anchors to h1-h6 tags and return the heading metadata."""
    headings: list[tuple[int, str, str]] = []
    slug_count: dict[str, int] = {}

    def replace_heading(match: re.Match) -> str:
        level = int(match.group(1))
        existing = match.group(2)  # attributes already on the tag (data-src-*)
        inner = match.group(3)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        base = _slugify(text) or f"heading-{len(headings)}"
        slug_count[base] = slug_count.get(base, 0) + 1
        anchor = base if slug_count[base] == 1 else f"{base}-{slug_count[base]}"
        headings.append((level, text, anchor))
        return f'<h{level} id="{anchor}"{existing}>{inner}</h{level}>'

    result = re.sub(
        r"<h([1-6])((?:\s[^>]*)?)>(.*?)</h\1>",
        replace_heading,
        html,
        flags=re.DOTALL,
    )
    return result, headings


_FRONT_MATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_yaml_subset(raw: str) -> dict:
    """Parse the common front-matter shapes without a YAML dependency:
    ``key: value``, ``key: [a, b]``, and block lists (``key:`` then ``- item``)."""
    data: dict = {}
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        match = re.match(r"^([A-Za-z0-9_\-]+)\s*:\s*(.*)$", line)
        if not match:
            i += 1
            continue
        key, value = match.group(1).strip(), match.group(2).strip()
        if value == "":
            items, j = [], i + 1
            while j < len(lines) and re.match(r"^\s*-\s+", lines[j]):
                items.append(_strip_quotes(re.sub(r"^\s*-\s+", "", lines[j]).strip()))
                j += 1
            data[key] = items if items else ""
            i = j if items else i + 1
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_strip_quotes(x.strip()) for x in inner.split(",") if x.strip()]
        else:
            data[key] = _strip_quotes(value)
        i += 1
    return data


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Return (front_matter_dict, body_without_front_matter)."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    return _parse_yaml_subset(match.group(1)), text[match.end():]


def front_matter_tags(data: dict) -> list[str]:
    raw = data.get("tags", data.get("tag"))
    if isinstance(raw, str):
        return [p for p in re.split(r"[,\s]+", raw.strip()) if p]
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


_BACKTICK_FENCE_RE = re.compile(r"^\s*```")


def _mask_inline_code(line: str) -> str:
    """Replace backtick code spans with spaces while preserving offsets."""
    chars = list(line)
    cursor = 0
    while cursor < len(line):
        if line[cursor] != "`":
            cursor += 1
            continue
        end_ticks = cursor + 1
        while end_ticks < len(line) and line[end_ticks] == "`":
            end_ticks += 1
        marker = line[cursor:end_ticks]
        closing = line.find(marker, end_ticks)
        span_end = len(line) if closing < 0 else closing + len(marker)
        chars[cursor:span_end] = " " * (span_end - cursor)
        cursor = span_end
    return "".join(chars)


def mask_markdown_code(text: str) -> str:
    """Mask fenced and inline backtick code while preserving line boundaries."""
    visible_lines: list[str] = []
    in_fence = False

    for line in (text or "").splitlines():
        if _BACKTICK_FENCE_RE.match(line):
            in_fence = not in_fence
            visible_lines.append("")
        elif in_fence:
            visible_lines.append("")
        else:
            visible_lines.append(_mask_inline_code(line))
    return "\n".join(visible_lines)


def _ends_hashtag(char: str) -> bool:
    return char.isspace() or char == "#" or unicodedata.category(char).startswith("P")


def body_hashtags(text: str) -> list[str]:
    """Extract inline ``#tags`` outside fenced and inline code.

    A hashtag starts at the beginning of a line or after whitespace. Its value
    ends at whitespace, another ``#``, or Unicode punctuation. ATX heading
    markers do not match because their ``#`` is followed by whitespace or a
    second ``#`` rather than a tag character.
    """
    tags: list[str] = []
    seen: set[str] = set()

    for visible in mask_markdown_code(text).splitlines():
        for match in re.finditer(r"(?<!\S)#", visible):
            start = match.end()
            if start >= len(visible) or _ends_hashtag(visible[start]):
                continue
            end = start
            while end < len(visible) and not _ends_hashtag(visible[end]):
                end += 1
            tag = visible[start:end]
            key = tag.casefold()
            if tag and key not in seen:
                seen.add(key)
                tags.append(tag)
    return tags


def _front_matter_html(data: dict) -> str:
    if not data:
        return ""
    rows = []
    for key, value in data.items():
        if isinstance(value, list):
            if key.lower() in ("tags", "tag"):
                val = "".join(
                    f'<span class="fm-tag">#{escape(str(x))}</span>' for x in value
                )
            else:
                val = escape(", ".join(str(x) for x in value))
        else:
            val = escape(str(value))
        rows.append(
            f'<div class="fm-row"><span class="fm-key">{escape(str(key))}</span>'
            f'<span class="fm-val">{val}</span></div>'
        )
    return '<div class="frontmatter">' + "".join(rows) + "</div>"


def _decode_bytes(raw: bytes) -> tuple[str, str] | None:
    """Decode *raw*, honouring BOMs first, then UTF-8, Big5, GBK in order.

    A UTF-8 BOM maps to the "utf-8-sig" codec and a UTF-16 BOM to "utf-16",
    so re-encoding with the returned name round-trips the BOM on save.
    Never decodes with errors="ignore" -- undecodable bytes return None.
    """
    if raw.startswith(codecs.BOM_UTF8):
        try:
            return raw.decode("utf-8-sig"), "utf-8-sig"
        except UnicodeDecodeError:
            return None
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return raw.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None
    for encoding in ("utf-8", "cp950", "gbk"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def read_text_detailed(path: Path) -> tuple[str, str, str] | None:
    """Return (text, encoding, newline) with newlines normalized to "\\n".

    ``newline`` is "\\r\\n" when the file uses CRLF line endings (detected
    after decoding, so UTF-16 files sniff correctly too), otherwise "\\n".
    """
    result = _decode_bytes(path.read_bytes())
    if result is None:
        return None
    text, encoding = result
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline


def read_text(path: Path) -> tuple[str, str] | None:
    """Return (text, encoding); see :func:`read_text_detailed`."""
    result = read_text_detailed(path)
    if result is None:
        return None
    return result[0], result[1]


@dataclass(frozen=True)
class RenderedBody:
    """The rendered document fragment plus everything ``_wrap`` needs around it.

    ``body`` is the inner HTML of ``<body>`` only: no ``<!DOCTYPE>``, no
    ``<head>``, no stylesheet, and none of the trailing mermaid / copy-button /
    KaTeX loader scripts that ``_wrap`` appends.  The three flags say which of
    those loaders the fragment requires.

    The fragment is **independent of the preview theme** -- ``theme`` is only
    consumed by ``_wrap`` (the ``theme-light`` / ``theme-dark`` class and the
    mermaid colour scheme); the parser, the anchor injection and the front
    matter block never see it.  That is why one cached ``RenderedBody`` serves
    both themes, and why it can be patched into an already-loaded page.
    """

    body: str
    headings: list  # list of (level, text, anchor_id)
    mermaid: bool
    code_copy: bool
    math: bool


def render_body(text: str) -> RenderedBody:
    """Render raw Markdown *text* to the ``<body>`` fragment of a preview.

    This is the theme-independent half of :func:`convert_text`: the returned
    body fragment carries no document chrome and no loader scripts, so the same
    result can be wrapped for light or dark, or spliced into a live page.
    """
    with _CONVERT_LOCK:
        # Render the full text (front_matter_plugin strips the YAML from the output
        # but the source line numbers stay intact, so task-list data-line is correct).
        front, _body = parse_front_matter(text)
        body = _PARSER.render(text)
        body_with_anchors, headings = _inject_anchors(body)
        body_with_anchors = _front_matter_html(front) + body_with_anchors
        return RenderedBody(
            body=body_with_anchors,
            headings=headings,
            mermaid='class="mermaid"' in body_with_anchors,
            code_copy='class="highlight"' in body_with_anchors,
            math='class="math' in body_with_anchors,
        )


# Small cache so reopening an unchanged file (common when switching tabs/notes)
# skips the parse + Pygments work. Holds RenderedBody keyed by (path, mtime) --
# no theme in the key, because the body fragment is theme-independent; the
# per-theme document chrome is re-applied by _wrap on every call. Cleared when
# the user stylesheet changes.
_CONVERT_CACHE: dict = {}
_CONVERT_CACHE_MAX = 32


def _cached_body(path: Path) -> tuple[RenderedBody | None, str | None]:
    """Return (rendered_body, error_message) for *path*, using ``_CONVERT_CACHE``.

    Exactly one of the two is ``None``. Shared by :func:`convert` and
    :func:`convert_body` so both go through the same cache.
    """
    if not path.exists():
        return None, f"找不到檔案：{path}"

    try:
        stat = path.stat()
    except OSError:
        return None, f"無法讀取檔案：{path.name}"
    if stat.st_size > 10 * 1024 * 1024:
        return None, f"檔案超過 10MB，無法預覽：{path.name}"

    cache_key = (str(path), stat.st_mtime_ns)
    with _CONVERT_LOCK:
        cached = _CONVERT_CACHE.get(cache_key)
    if cached is not None:
        return cached, None

    result = read_text(path)
    if result is None:
        return None, f"無法讀取檔案編碼，請使用 UTF-8、Big5 或 GBK：{path.name}"
    text, _ = result
    rendered = render_body(text)
    with _CONVERT_LOCK:
        _CONVERT_CACHE[cache_key] = rendered
        if len(_CONVERT_CACHE) > _CONVERT_CACHE_MAX:
            _CONVERT_CACHE.pop(next(iter(_CONVERT_CACHE)))
    return rendered, None


def convert(filepath: str | Path, theme: str = "light") -> tuple[str, list[tuple[int, str, str]]]:
    """Return (html, headings). headings = list of (level, text, anchor_id)."""
    path = Path(filepath)
    rendered, error = _cached_body(path)
    if rendered is None:
        return _error_page(error or "", theme), []
    with _CONVERT_LOCK:
        html = _wrap(
            rendered.body,
            path.stem,
            theme,
            mermaid=rendered.mermaid,
            code_copy=rendered.code_copy,
            math=rendered.math,
        )
    return html, rendered.headings


def convert_body(filepath: str | Path) -> RenderedBody | None:
    """Return the cached ``<body>`` fragment for *filepath*, or ``None``.

    ``None`` means the file is missing, too large, or undecodable -- the cases
    :func:`convert` turns into an error page. Shares ``_CONVERT_CACHE`` with
    :func:`convert`, so opening a file and then patching it costs one parse.
    """
    rendered, _error = _cached_body(Path(filepath))
    return rendered


def convert_text(
    text: str, theme: str = "light", title: str = "preview"
) -> tuple[str, list[tuple[int, str, str]]]:
    """Render raw Markdown *text* to a self-contained HTML document.

    Used both by ``convert`` (file path) and by the live edit-mode preview,
    which has unsaved buffer text rather than a file on disk.
    """
    with _CONVERT_LOCK:
        rendered = render_body(text)
        return (
            _wrap(
                rendered.body,
                title,
                theme,
                mermaid=rendered.mermaid,
                code_copy=rendered.code_copy,
                math=rendered.math,
            ),
            rendered.headings,
        )


def _theme_class(theme: str) -> str:
    return "theme-dark" if theme == "dark" else "theme-light"


def _wrap(
    body: str,
    title: str,
    theme: str = "light",
    mermaid: bool = False,
    code_copy: bool = False,
    math: bool = False,
) -> str:
    safe_title = escape(title)
    theme_class = _theme_class(theme)
    mermaid_html = f"\n{_mermaid_script(theme)}" if mermaid else ""
    copy_html = f"\n{_copy_button_script()}" if code_copy else ""
    math_html = f"\n{_katex_html()}" if math else ""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant" class="{theme_class}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{_FULL_CSS}</style>
{f"<style>{_user_css}</style>" if _user_css else ""}
</head>
<body class="{theme_class}">
{body}{mermaid_html}{copy_html}{math_html}
</body>
</html>"""


def state_page_html(title: str, message: str, theme: str = "light", label: str = "") -> str:
    safe_title = escape(title)
    safe_message = escape(message)
    safe_label = escape(label)
    label_html = f'<div class="status-label">{safe_label}</div>' if safe_label else ""
    return _wrap(
        f'<main class="state-page">{label_html}<h1>{safe_title}</h1><p>{safe_message}</p></main>',
        title,
        theme,
    )


def _error_page(message: str, theme: str = "light") -> str:
    return state_page_html("無法預覽 Markdown", message, theme, "錯誤")
