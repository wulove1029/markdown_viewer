"""Source-preserving rebasing of local resources when a note changes folders.

Only destinations are changed. Markdown-it supplies the Markdown grammar;
source offsets, original line endings, labels, titles and code remain intact.
Unsupported resource syntax fails before any filesystem mutation.
"""

from __future__ import annotations

from bisect import bisect_left
import codecs
from html import escape, unescape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt
from markdown_it.common.utils import UNESCAPE_ALL_RE, unescapeAll
from markdown_it.rules_inline import image as image_rule, link as link_rule
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.dollarmath.index import math_inline_dollar
from mdit_py_plugins.front_matter import front_matter_plugin


_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_ATTRIBUTE = re.compile(
    r'''(?P<name>[^\s=<>/'"]+)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|(?P<bare>[^\s>]+))'''
)
_RAW_TAGS = {"code", "pre", "script", "style", "textarea"}


def _raw_suffix_start(raw: str) -> int:
    """Locate the first decoded query/fragment delimiter in original markup.

    A literal '#' inside '&#63;' is part of an entity, not a URL fragment.
    Retaining the original suffix avoids decoding '&amp;copy;' twice.
    """
    cursor = 0
    for match in UNESCAPE_ALL_RE.finditer(raw):
        literal = raw[cursor:match.start()]
        positions = [index for marker in "?#" if (index := literal.find(marker)) >= 0]
        if positions:
            return cursor + min(positions)
        if any(marker in unescapeAll(match.group()) for marker in "?#"):
            return match.start()
        cursor = match.end()
    return min((index for marker in "?#" if (index := raw.find(marker, cursor)) >= 0), default=len(raw))


def _same_folder(old: Path, new: Path) -> bool:
    return os.path.normcase(str(old.parent.resolve())) == os.path.normcase(
        str(new.parent.resolve())
    )


def _destination(value: str, old: Path, new: Path) -> str:
    """Rebase a decoded URL while retaining its query and fragment verbatim."""
    if not value or value.startswith(("#", "?", "/", "\\")) or _SCHEME.match(value):
        return value
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise OSError("連結含無法安全搬移的控制字元，原始文件未變更。")
    try:
        path_end = min((index for marker in "?#" if (index := value.find(marker)) >= 0), default=len(value))
        local_path = unquote(value[:path_end], errors="strict")
        if "\\" in local_path:
            raise ValueError("ambiguous backslash in a relative URL")
        resource = (old.parent / local_path).absolute()
        # A document linking to itself follows its new name as well.
        if os.path.normcase(str(resource)) == os.path.normcase(str(old.absolute())):
            resource = new.absolute()
        try:
            relative = Path(os.path.relpath(resource, new.parent)).as_posix()
            encoded = quote(relative, safe="/-._~")
        except ValueError as exc:  # Different Windows drives have no relative paths.
            raise OSError(
                "文件含相對圖片或附件，無法安全跨磁碟搬移。"
                "請連同資料夾與附件複製；原始文件與草稿已保留。"
            ) from exc
    except (ValueError, UnicodeError) as exc:
        raise OSError(f"無法安全重算相對連結：{value}") from exc
    # Preserve delimiters too (including an empty '?' or '#').
    suffix = value[path_end:]
    return encoded + suffix


def _blank(chars: list[str], start: int, end: int) -> None:
    chars[start:end] = ["\n" if char == "\n" else " " for char in chars[start:end]]


class _HtmlResources(HTMLParser):
    def __init__(self, source, offsets, old, new):
        super().__init__(convert_charrefs=False)
        self.source, self.offsets, self.old, self.new = source, offsets, old, new
        self.protected: list[tuple[int, int]] = []
        self.replacements: list[tuple[int, int, str]] = []
        self.raw_tag: str | None = None
        self.raw_start = 0

    def _position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self._position()
        raw = self.get_starttag_text()
        self.protected.append((start, start + len(raw)))
        if self.raw_tag:
            return
        for match in _ATTRIBUTE.finditer(raw):
            name = match.group("name").lower()
            group = next(key for key in ("double", "single", "bare") if match.group(key) is not None)
            value = unescape(match.group(group))
            if name == "srcset" or (name == "style" and re.search(r"url\s*\(", value, re.I)):
                raise OSError("文件含 srcset 或 CSS 圖片路徑，尚無法安全重算；請連同資料夾搬移。")
            if name not in {"src", "href", "poster", "data"}:
                continue
            if tag == "base":
                raise OSError("文件含 HTML base 路徑，尚無法安全重算；原始文件未變更。")
            rewritten = _destination(value, self.old, self.new)
            if rewritten != value:
                self.replacements.append((start + match.start(group), start + match.end(group), escape(rewritten, quote=True)))
        if tag in _RAW_TAGS:
            self.raw_tag, self.raw_start = tag, start

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.raw_tag == tag:
            self.raw_tag = None

    def handle_endtag(self, tag):
        start = self._position()
        end = self.source.find(">", start) + 1
        self.protected.append((start, end))
        if tag == self.raw_tag:
            if tag == "style" and re.search(r"url\s*\(|@import", self.source[self.raw_start:end], re.I):
                raise OSError("文件含 CSS 相對資源，尚無法安全重算；請連同資料夾搬移。")
            self.protected.append((self.raw_start, end))
            self.raw_tag = None

    def handle_comment(self, data):
        start = self._position()
        self.protected.append((start, start + len(data) + 7))


def rebase_markdown_links(text: str, old_path: str | Path, new_path: str | Path) -> str:
    """Return Markdown using the new folder as its resource base.

    This pure function also applies to unsaved editor buffers. Call it before
    committing a relocation; OSError means the caller must keep the old path.
    Folder renames must not call this for their individual child documents.
    """
    old, new = Path(old_path), Path(new_path)
    if _same_folder(old, new):
        return text
    crlf_positions = []
    for match in re.finditer("\r\n", text):
        crlf_positions.append(match.start() - len(crlf_positions))
    source = text.replace("\r\n", "\n").replace("\r", "\n")
    offsets = [0] + [match.end() for match in re.finditer("\n", source)]
    md = MarkdownIt("commonmark", {"html": True}).use(front_matter_plugin).use(dollarmath_plugin)
    env: dict = {}
    tokens = md.parse(source, env)
    inline_ranges = [
        (offsets[token.map[0]], offsets[token.map[1]] if token.map[1] < len(offsets) else len(source))
        for token in tokens if token.type == "inline" and token.map
    ]
    chars = list(source)
    for token in tokens:
        if token.type in {"fence", "code_block", "front_matter", "math_block", "math_block_label"} and token.map:
            start, end = token.map
            _blank(chars, offsets[start], offsets[end] if end < len(offsets) else len(source))

    # Let the actual inline parser identify multi-line and variable-width code
    # spans. Its token rule sees original source offsets, unlike block content.
    definitions = list(env.get("references", {}).values()) + env.get("duplicate_refs", [])
    detection_chars = chars.copy()
    for reference in definitions:
        start, end = reference["map"]
        _blank(detection_chars, offsets[start], offsets[end] if end < len(offsets) else len(source))
    code_source = "".join(detection_chars)
    active_source, active_offset = "", 0
    from markdown_it.rules_inline import backtick, html_inline
    html_ranges = []
    math_rule = math_inline_dollar(True, True, False)

    def protect_math(state, silent):
        start = state.pos
        accepted = math_rule(state, silent)
        if accepted and not silent and state.src is active_source:
            _blank(chars, active_offset + start, active_offset + state.pos)
        return accepted

    def detect_html(state, silent):
        start = state.pos
        accepted = html_inline(state, silent)
        if accepted and not silent and state.src is active_source:
            html_ranges.append((active_offset + start, active_offset + state.pos))
        return accepted

    def protect_code(state, silent):
        start, count = state.pos, len(state.tokens)
        accepted = backtick(state, silent)
        if accepted and not silent and state.src is active_source:
            if len(state.tokens) > count and state.tokens[-1].type == "code_inline":
                _blank(chars, active_offset + start, active_offset + state.pos)
        return accepted

    md.inline.ruler.at("backticks", protect_code)
    md.inline.ruler.at("math_inline", protect_math)
    md.inline.ruler.at("html_inline", detect_html)
    for start, end in inline_ranges:
        active_source, active_offset = code_source[start:end], start
        md.inline.parse(active_source, md, env, [])
    for token in tokens:
        if token.type == "html_block" and token.map:
            start, end = token.map
            html_ranges.append((offsets[start], offsets[end] if end < len(offsets) else len(source)))
    html_chars = ["\n" if char == "\n" else " " for char in chars]
    for start, end in html_ranges:
        html_chars[start:end] = chars[start:end]
    html_source = "".join(html_chars)
    html = _HtmlResources(html_source, offsets, old, new)
    html.feed(html_source)
    html.close()
    if html.raw_tag:
        if html.raw_tag == "style" and re.search(r"url\s*\(|@import", html_source[html.raw_start:], re.I):
            raise OSError("文件含 CSS 相對資源，尚無法安全重算；請連同資料夾搬移。")
        html.protected.append((html.raw_start, len(source)))
    for start, end in html.protected:
        _blank(chars, start, end)
    reference_source = "".join(chars)
    for reference in definitions:
        start, end = reference["map"]
        _blank(chars, offsets[start], offsets[end] if end < len(offsets) else len(source))
    visible = "".join(chars)
    if re.search(r"(?<!\\)!?\[\[[^\]\n]+\]\]", visible):
        raise OSError("文件含 wiki-links，移動單一文件可能改變解析目標；請連同資料夾搬移。")
    replacements = list(html.replacements)

    def record_destination(start, result, offset=0):
        start, end = start + offset, result.pos + offset
        if source[start:start + 1] == "<":
            start, end = start + 1, end - 1
        rewritten = _destination(result.str, old, new)
        if rewritten != result.str:
            raw = source[start:end]
            suffix_start = _raw_suffix_start(raw)
            decoded_suffix_start = min((index for marker in "?#" if (index := result.str.find(marker)) >= 0), default=len(result.str))
            suffix_length = len(result.str) - decoded_suffix_start
            rewritten = rewritten[:len(rewritten) - suffix_length] + raw[suffix_start:]
            replacements.append((start, end, rewritten))

    def track_rule(rule, image=False):
        def tracked(state, silent):
            start = state.pos
            candidate = None
            marker = "![" if image else "["
            if state.src is active_source and not silent and active_source.startswith(marker, start):
                label = start + (1 if image else 0)
                end = md.helpers.parseLinkLabel(state, label, not image)
                if end >= 0 and active_source[end + 1:end + 2] == "(":
                    pos = end + 2
                    while pos < len(active_source) and active_source[pos].isspace():
                        pos += 1
                    candidate = (pos, md.helpers.parseLinkDestination(active_source, pos, len(active_source)))
            accepted = rule(state, silent)
            if accepted and candidate and candidate[1].ok:
                record_destination(*candidate, offset=active_offset)
            return accepted
        return tracked

    md.inline.ruler.at("link", track_rule(link_rule))
    md.inline.ruler.at("image", track_rule(image_rule, True))
    for start, end in inline_ranges:
        active_source, active_offset = visible[start:end], start
        md.inline.parse(active_source, md, env, [])
    for reference in definitions:
        line, end_line = reference["map"]
        start = offsets[line]
        end = offsets[end_line] if end_line < len(offsets) else len(source)
        match = re.search(r"\[(?:\\.|[^\]\n])+\]:[ \t\n]*", reference_source[start:end])
        if match is None:
            if _destination(reference["href"], old, new) != reference["href"]:
                raise OSError("無法定位參照式連結的原始路徑，文件未搬移。")
            continue
        pos = start + match.end()
        # Reference destinations can continue on the next quoted line.
        while reference_source[pos:pos + 1] == ">":
            pos += 1
            while pos < end and reference_source[pos] in " \t":
                pos += 1
        result = md.helpers.parseLinkDestination(reference_source, pos, end)
        if not result.ok or md.normalizeLink(result.str) != reference["href"]:
            raise OSError("無法安全重算參照式連結，文件未搬移。")
        record_destination(pos, result)

    # Reject overlapping rewrites instead of risking source corruption.
    ordered = sorted(set(replacements))
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] > current[0]:
            raise OSError("連結來源範圍重疊，無法安全搬移文件。")
    for start, end, replacement in reversed(ordered):
        original_start = start + bisect_left(crlf_positions, start)
        original_end = end + bisect_left(crlf_positions, end)
        text = text[:original_start] + replacement + text[original_end:]
    # A conservative final grammar check catches links whose source layout
    # cannot be mapped safely (for example unusual container continuations).
    validator = MarkdownIt("commonmark", {"html": True}).use(front_matter_plugin).use(dollarmath_plugin)

    def resource_urls(items, *, expected=False, raw_tags=None):
        raw_tags = [] if raw_tags is None else raw_tags
        urls = []
        for token in items:
            if token.type in {"html_inline", "html_block"}:
                for tag in re.finditer(r"<(/?)(code|pre|script|style|textarea)\b[^>]*>", token.content, re.I):
                    if tag.group(1):
                        if raw_tags and raw_tags[-1] == tag.group(2).lower():
                            raw_tags.pop()
                    elif not tag.group().endswith("/>"):
                        raw_tags.append(tag.group(2).lower())
            if token.type in {"link_open", "image"}:
                url = token.attrGet("href" if token.type == "link_open" else "src") or ""
                if expected and not raw_tags:
                    url = validator.normalizeLink(_destination(url, old, new))
                urls.append(url)
            if token.children:
                urls.extend(resource_urls(token.children, expected=expected, raw_tags=raw_tags))
        return urls

    expected = resource_urls(tokens, expected=True)
    actual = resource_urls(validator.parse(text))
    if expected != actual:
        raise OSError("文件含無法安全定位的 Markdown 連結，已取消搬移並保留原始內容。")
    # The viewer also has tables, footnotes, definition lists and callouts.
    # Re-parse with its actual grammar: syntax we could not map exactly must
    # fail closed rather than rewriting another construct as a local URL.
    from .md_converter import _build_parser

    # Explicit HTML has its own attribute grammar above. The viewer's safe
    # HTML mode treats unsupported tags as text, which must not reinterpret
    # attribute strings/comments/preformatted content as Markdown here.
    viewer_chars = list(source)
    for start, end in html.protected:
        _blank(viewer_chars, start, end)
    viewer_before = "".join(viewer_chars)
    viewer_after = viewer_before
    for start, end, replacement in reversed(ordered):
        if any(left <= start and end <= right for left, right in html.protected):
            continue
        viewer_after = viewer_after[:start] + replacement + viewer_after[end:]
    viewer_parser = _build_parser()
    viewer_expected = resource_urls(viewer_parser.parse(viewer_before), expected=True)
    viewer_actual = resource_urls(viewer_parser.parse(viewer_after))
    if viewer_expected != viewer_actual:
        raise OSError("文件使用的 Markdown 延伸語法無法安全重算連結，已保留原始內容。")
    return text


def rebase_markdown_bytes(data: bytes, old_path, new_path) -> bytes:
    """Preserve the original codec, BOM (including UTF-16 BE), and newlines."""
    if _same_folder(Path(old_path), Path(new_path)):
        return data
    bom = b""
    encoding = None
    for prefix, codec in ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")):
        if data.startswith(prefix):
            bom, encoding = prefix, codec
            break
    raw = data[len(bom):]
    for codec in ([encoding] if encoding else ["utf-8", "cp950", "gbk"]):
        try:
            text = raw.decode(codec)
            if "\x00" in text or text.encode(codec) != raw:
                continue
            return bom + rebase_markdown_links(text, old_path, new_path).encode(codec)
        except UnicodeError:
            continue
    raise OSError("無法確認 Markdown 的原始編碼，為保護內容已取消搬移。")
