"""Markdown → self-contained HTML converter."""

import re
from pathlib import Path
from markdown_it import MarkdownIt
from mdit_py_plugins.tasklists import tasklists_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter

_CSS_PATH = Path(__file__).parent.parent / "assets" / "obsidian-light.css"
_PYGMENTS_CSS = HtmlFormatter(style="one-dark").get_style_defs(".highlight")

try:
    _THEME_CSS = _CSS_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    _THEME_CSS = "body { font-family: sans-serif; padding: 2em; }"

_FULL_CSS = f"{_THEME_CSS}\n{_PYGMENTS_CSS}"
_FORMATTER = HtmlFormatter(style="one-dark")


def _highlight_code(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except Exception:
        lexer = TextLexer()
    return highlight(code, lexer, _FORMATTER)


def _build_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"highlight": _highlight_code})
    md.enable("table")
    md.enable("strikethrough")
    md = md.use(tasklists_plugin)
    md = md.use(front_matter_plugin)
    return md


_PARSER = _build_parser()


def _slugify(text: str) -> str:
    """Convert heading text to a URL-safe anchor id."""
    text = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', text.lower())
    return re.sub(r'[\s]+', '-', text.strip())


def _inject_anchors(html: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Add id anchors to <h1>-<h6> tags. Returns modified html and heading list."""
    headings: list[tuple[int, str, str]] = []  # (level, text, anchor_id)
    slug_count: dict[str, int] = {}

    def replace_heading(m: re.Match) -> str:
        level = int(m.group(1))
        inner = m.group(2)
        text = re.sub(r'<[^>]+>', '', inner).strip()
        base = _slugify(text) or f"heading-{len(headings)}"
        slug_count[base] = slug_count.get(base, 0) + 1
        anchor = base if slug_count[base] == 1 else f"{base}-{slug_count[base]}"
        headings.append((level, text, anchor))
        return f'<h{level} id="{anchor}">{inner}</h{level}>'

    result = re.sub(r'<h([1-6])>(.*?)</h\1>', replace_heading, html,
                    flags=re.DOTALL)
    return result, headings


def _read_text(path: Path) -> str | None:
    for enc in ('utf-8', 'cp950', 'gbk'):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return None


def convert(filepath: str | Path) -> tuple[str, list[tuple[int, str, str]]]:
    """Return (html, headings). headings = list of (level, text, anchor_id)."""
    path = Path(filepath)

    if not path.exists():
        return _error_page(f"找不到檔案：{path}"), []

    if path.stat().st_size > 10 * 1024 * 1024:
        return _error_page(f"檔案過大（>10MB），無法預覽：{path.name}"), []

    text = _read_text(path)
    if text is None:
        return _error_page(f"無法讀取檔案：編碼不支援（{path.name}）"), []

    body = _PARSER.render(text)
    body_with_anchors, headings = _inject_anchors(body)
    return _wrap(body_with_anchors, path.stem), headings


def _wrap(body: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_FULL_CSS}</style>
</head>
<body>
{body}
</body>
</html>"""


def _error_page(message: str) -> str:
    return _wrap(f'<p style="color:#cf222e;font-weight:bold;">⚠ {message}</p>', "Error")
