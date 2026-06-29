"""Markdown to self-contained HTML converter."""

from html import escape
import json
import re
import urllib.parse
from pathlib import Path

from markdown_it import MarkdownIt
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
_FULL_CSS = f"{_THEME_CSS}\n{_PYGMENTS_CSS}\n{_WIKILINK_CSS}"
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
        inner = match.group(2)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        base = _slugify(text) or f"heading-{len(headings)}"
        slug_count[base] = slug_count.get(base, 0) + 1
        anchor = base if slug_count[base] == 1 else f"{base}-{slug_count[base]}"
        headings.append((level, text, anchor))
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    result = re.sub(
        r"<h([1-6])>(.*?)</h\1>",
        replace_heading,
        html,
        flags=re.DOTALL,
    )
    return result, headings


def read_text(path: Path) -> tuple[str, str] | None:
    """Return (text, encoding), trying UTF-8, Big5, GBK in order."""
    for encoding in ("utf-8", "cp950", "gbk"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def convert(filepath: str | Path, theme: str = "light") -> tuple[str, list[tuple[int, str, str]]]:
    """Return (html, headings). headings = list of (level, text, anchor_id)."""
    path = Path(filepath)

    if not path.exists():
        return _error_page(f"找不到檔案：{path}", theme), []

    if path.stat().st_size > 10 * 1024 * 1024:
        return _error_page(f"檔案超過 10MB，無法預覽：{path.name}", theme), []

    result = read_text(path)
    if result is None:
        return _error_page(
            f"無法讀取檔案編碼，請使用 UTF-8、Big5 或 GBK：{path.name}",
            theme,
        ), []
    text, _ = result
    return convert_text(text, theme, title=path.stem)


def convert_text(
    text: str, theme: str = "light", title: str = "preview"
) -> tuple[str, list[tuple[int, str, str]]]:
    """Render raw Markdown *text* to a self-contained HTML document.

    Used both by ``convert`` (file path) and by the live edit-mode preview,
    which has unsaved buffer text rather than a file on disk.
    """
    body = _PARSER.render(text)
    body_with_anchors, headings = _inject_anchors(body)
    needs_mermaid = 'class="mermaid"' in body_with_anchors
    has_code = 'class="highlight"' in body_with_anchors
    needs_math = 'class="math' in body_with_anchors
    return (
        _wrap(
            body_with_anchors,
            title,
            theme,
            mermaid=needs_mermaid,
            code_copy=has_code,
            math=needs_math,
        ),
        headings,
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
