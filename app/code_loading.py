"""Keep large syntax trees out of the initial preview layout."""

import hashlib
import json
import re
import sys
import threading
from collections import OrderedDict
from pathlib import Path

_SCRIPT = (Path(__file__).parent.parent / "assets" / "code_loading.js").read_text(encoding="utf-8")
_PRE = re.compile(r'(<div\b[^>]*\bclass="highlight"[^>]*>\s*<pre[^>]*>)(.*?)(</pre>)', re.S)
_SPAN = re.compile(r'</?span(?: class="[a-zA-Z0-9 -]+")?>')
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 64 * 1024 * 1024


def _encode_blocks(blocks: list[str]) -> str:
    """Share repeated highlighted lines without changing a single character."""
    lines, indices, references = [], {}, []
    for block in blocks:
        rows = []
        for line in block.splitlines(keepends=True):
            index = indices.get(line)
            if index is None:
                index = len(lines)
                indices[line] = index
                lines.append(line)
            rows.append(index)
        references.append(rows)
    compact = json.dumps({'lines': lines, 'blocks': references},
                         ensure_ascii=True, separators=(',', ':'))
    original = json.dumps(blocks, ensure_ascii=True, separators=(',', ':'))
    # Unique-line documents must not pay for a larger dictionary representation.
    return min((compact, original), key=len).replace('<', r'\u003c')


def defer_code_highlighting(html: str) -> str:
    """Retain complete escaped code text; defer only Pygments' span markup."""
    if len(html) < 2 * 1024 * 1024:
        return html
    key = hashlib.sha256(html.encode('utf-8')).digest()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    blocks = []

    def replace(match):
        markup = match[2]
        if markup.count('<span') < 100:
            return match[0]
        plain = _SPAN.sub('', markup)
        # Do not reinterpret arbitrary HTML or custom highlighter output.
        if '<' in plain:
            return match[0]
        index = len(blocks)
        blocks.append(markup)
        opening = match[1][:-1] + f' data-md-code="{index}">'
        # HTML parsing strips a newline immediately after an opening pre tag.
        return opening + '<span></span>' + plain + match[3]

    body = _PRE.sub(replace, html)
    if not blocks:
        return html
    payload = _encode_blocks(blocks)
    script = '<script id="md-code-payload" type="application/json">' + payload + '</script>'
    before, closing, after = body.rpartition('</body>')
    if not closing:
        return html
    result = before + script + '<script>' + _SCRIPT + '</script>' + closing + after
    size = sys.getsizeof(result) + sys.getsizeof(key)
    if size <= _CACHE_BYTES:
        with _CACHE_LOCK:
            _CACHE[key] = result
            _CACHE.move_to_end(key)
            total = sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in _CACHE.items())
            while total > _CACHE_BYTES:
                old_key, old_value = _CACHE.popitem(last=False)
                total -= sys.getsizeof(old_key) + sys.getsizeof(old_value)
    return result
