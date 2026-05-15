"""Markdown to self-contained HTML converter."""

from html import escape
import re
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name

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
    md = MarkdownIt("commonmark", {"html": False, "highlight": _highlight_code})
    md.enable("table")
    md.enable("strikethrough")
    md = md.use(tasklists_plugin)
    md = md.use(front_matter_plugin)
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


def _read_text(path: Path) -> str | None:
    for encoding in ("utf-8", "cp950", "gbk"):
        try:
            return path.read_text(encoding=encoding)
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

    text = _read_text(path)
    if text is None:
        return _error_page(
            f"無法讀取檔案編碼，請使用 UTF-8、Big5 或 GBK：{path.name}",
            theme,
        ), []

    body = _PARSER.render(text)
    body_with_anchors, headings = _inject_anchors(body)
    return _wrap(body_with_anchors, path.stem, theme), headings


def _theme_class(theme: str) -> str:
    return "theme-dark" if theme == "dark" else "theme-light"


def _wrap(body: str, title: str, theme: str = "light") -> str:
    safe_title = escape(title)
    theme_class = _theme_class(theme)
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{_FULL_CSS}</style>
</head>
<body class="{theme_class}">
{body}
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
