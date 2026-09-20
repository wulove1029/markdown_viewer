"""Prepare byte-preserving wiki backlink edits before a confirmed rename."""

import re
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.common.html_re import HTML_TAG_RE

from .links import WIKILINK_RE, LinkIndex, collect_markdown_files
from .md_converter import _decode_bytes


def _visible_wikilinks(text):
    lines = text.splitlines(keepends=True)
    for token in MarkdownIt("commonmark").parse(text):
        if token.type in {"fence", "code_block", "html_block"} and token.map:
            for index in range(*token.map):
                lines[index] = "".join(c if c in "\r\n" else " " for c in lines[index])
    visible = "".join(lines)
    chars = list(visible)
    cursor = 0
    while cursor < len(visible):
        if visible[cursor] == "\\" and cursor + 1 < len(visible):
            if visible[cursor + 1] in "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~":
                cursor += 2
                continue
        if visible[cursor] == "<":
            tag = HTML_TAG_RE.match(visible[cursor:])
            if tag:
                end = cursor + tag.end()
                chars[cursor:end] = " " * (end - cursor)
                cursor = end
                continue
        if visible[cursor] == "`":
            opening = re.match(r"`+", visible[cursor:]).group()
            closing = re.search(r"(?<!`)" + opening + r"(?!`)",
                                visible[cursor + len(opening):])
            if closing:
                end = cursor + len(opening) + closing.end()
                chars[cursor:end] = " " * (end - cursor)
                cursor = end
                continue
            cursor += len(opening)
            continue
        cursor += 1
    visible = "".join(chars)
    for match in WIKILINK_RE.finditer(visible):
        # Escaped opening brackets are literal text.
        prefix = visible[:match.start()]
        if (len(prefix) - len(prefix.rstrip("\\"))) % 2 == 0:
            yield match


def prepare_backlink_updates(old: Path, new: Path, roots) -> dict[Path, tuple[bytes, bytes]]:
    """Build a fresh LinkIndex; fail closed if any scoped note cannot be read.

    The returned before/after bytes also serve as optimistic concurrency checks.
    No file is changed while preparing or confirming this plan.
    """
    old, new = Path(old).resolve(), Path(new).resolve()
    files = collect_markdown_files([*roots, old.parent], strict=True)
    if len(files) >= 8000:
        raise OSError("文件庫超過安全索引上限，無法完整確認反向連結；尚未改名。")
    originals, docs, codecs = {}, [], {}
    for candidate in files:
        path = candidate.resolve()
        if candidate.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise OSError(f"無法完整確認反向連結（連結或大型文件）：{candidate}")
        raw = path.read_bytes()
        decoded = _decode_bytes(raw)
        if decoded is None:
            raise OSError(f"無法解碼文件以確認反向連結：{path}")
        text, encoding = decoded
        # Preserve UTF-16 endianness instead of the host-native utf-16 codec.
        if raw.startswith(b"\xfe\xff"):
            text, encoding = raw.decode("utf-16-be"), "utf-16-be"
        if text.encode(encoding) != raw:
            raise OSError(f"文件編碼無法無損往返：{path}")
        originals[path], codecs[path] = raw, encoding
        docs.append((path, text))
    if old not in originals:
        raise OSError("來源文件不在完整索引內，尚未改名。")
    index = LinkIndex()
    index.build([(path, "\n".join(match.group() for match in _visible_wikilinks(text)))
                 for path, text in docs])
    after_index = LinkIndex()
    after_index.build([(new if path == old else path, "") for path, _text in docs])
    sources = {Path(path) for path in index.backlinks(old)} | {old}
    updates = {}
    for path, text in docs:
        if path not in sources:
            continue
        replacements = []
        for match in _visible_wikilinks(text):
            target = match.group(1)
            if index.resolve(target, path) != old:
                continue
            name, separator, heading = target.partition("#")
            trailing = name[len(name.rstrip()):]
            name = name.rstrip()
            suffix = name[-3:] if name.lower().endswith(".md") else ""
            stem = name[:-3] if suffix else name
            cut = max(stem.rfind("/"), stem.rfind("\\")) + 1
            replacement = stem[:cut] + new.stem + suffix + trailing + separator + heading
            if after_index.resolve(replacement, new if path == old else path) != new:
                replacement = (new.with_suffix("").as_posix()
                               + suffix + trailing + separator + heading)
            parsed = WIKILINK_RE.fullmatch("[[" + replacement + "]]")
            if (parsed is None or parsed.group(1).strip() != replacement.strip()
                    or after_index.resolve(replacement, new if path == old else path) != new):
                raise OSError(f"新檔名無法安全表示為 wikilink，尚未改名：{new.name}")
            replacements.append((match.start(1), match.end(1), replacement))
        edited = text
        for start, end, replacement in reversed(replacements):
            edited = edited[:start] + replacement + edited[end:]
        if edited != text:
            try:
                updates[path] = (originals[path], edited.encode(codecs[path]))
            except UnicodeError as exc:
                raise OSError(f"新名稱無法以原文件編碼儲存：{path}") from exc
    return updates
