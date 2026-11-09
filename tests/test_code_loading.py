"""Large-code previews retain source text and safe syntax payloads."""

import json
import re
from html import unescape

from app.code_loading import defer_code_highlighting


def large_page(markup=None):
    markup = markup or '<span class="k">return</span> &lt;/script&gt;\n' * 110
    return ('<html><body>' + '<!--' + 'x' * (2 * 1024 * 1024) + '-->'
            + '<div data-src-start="2" class="highlight"><pre><span></span>'
            + markup + '</pre></div></body></html>')


def test_deferred_code_preserves_text_attributes_and_safe_payload():
    html = large_page()
    result = defer_code_highlighting(html)
    plain = re.search(r'<pre[^>]*>(.*?)</pre>', result, re.S)[1]
    assert plain.startswith('<span></span>')
    plain = plain.removeprefix('<span></span>')
    assert unescape(plain) == 'return </script>\n' * 110
    assert 'data-src-start="2"' in result
    assert 'data-md-code="0"' in result
    payload = re.search(r'type="application/json">(.*?)</script>', result, re.S)[1]
    assert '<' not in payload
    decoded = json.loads(payload)
    block = (decoded[0] if isinstance(decoded, list) else
             ''.join(decoded['lines'][i] for i in decoded['blocks'][0]))
    assert block.startswith('<span></span><span class="k">')


def test_shared_lines_roundtrip_exact_unicode_and_line_endings():
    from app.code_loading import _encode_blocks

    line = '<span class="k">\u4e2d\u6587 \U0001f680 &lt;/script&gt;</span>\r\n'
    blocks = [line * 120 + '\nend\r', '\n' + line * 120, '']
    encoded = _encode_blocks(blocks)
    data = json.loads(encoded)
    assert isinstance(data, dict)
    assert [''.join(data['lines'][i] for i in rows) for rows in data['blocks']] == blocks
    assert '<' not in encoded
    assert len(encoded) < len(json.dumps(blocks)) / 4


def test_unique_short_blocks_use_the_original_representation():
    from app.code_loading import _encode_blocks

    blocks = ['unique one', 'unique two']
    assert json.loads(_encode_blocks(blocks)) == blocks


def test_small_or_unrecognized_markup_is_untouched():
    small = '<body><pre>small</pre></body>'
    assert defer_code_highlighting(small) == small
    for markup in ('text', '<span class="k">x</span>' * 110 + '<img src="x">'):
        html = large_page(markup)
        assert defer_code_highlighting(html) == html


def test_missing_body_close_is_untouched():
    html = large_page().replace('</body>', '')
    assert defer_code_highlighting(html) == html


def test_cache_keeps_documents_distinct_and_obeys_byte_limit(monkeypatch):
    import sys

    from app import code_loading

    code_loading._CACHE.clear()
    first = large_page()
    result = defer_code_highlighting(first)
    monkeypatch.setattr(code_loading, '_CACHE_BYTES', sys.getsizeof(result) + 200)
    second = first.replace('<body>', '<body><h1>Changed title</h1>')
    assert 'Changed title' in defer_code_highlighting(second)
    assert len(code_loading._CACHE) == 1
    assert 'Changed title' not in defer_code_highlighting(first)
    assert len(code_loading._CACHE) == 1
    code_loading._CACHE.clear()


def test_native_find_waits_for_freeze_and_ignores_retired_page():
    from types import SimpleNamespace

    from app.renderer import RendererView

    calls, replies = [], []
    page = SimpleNamespace(
        runJavaScript=lambda script, callback: (calls.append(script), replies.append(callback)),
        findText=lambda *args: calls.append(args),
    )
    view = SimpleNamespace(_render_generation=4, _page_epoch=2,
                           _has_deferred_code=True, page=lambda: page)
    RendererView._find_with_stable_code(view, 'needle', 0)
    assert len(calls) == 1 and 'freeze' in calls[0]
    replies.pop()(None)
    assert calls[-1] == ('needle', 0)
    RendererView._find_with_stable_code(view, 'stale', 0)
    view._render_generation += 1
    replies.pop()(None)
    assert not any(isinstance(c, tuple) and c[0] == 'stale' for c in calls)
    RendererView._find_with_stable_code(view, '', 0)
    assert 'resume' in calls[-1]
    view._page_epoch += 1
    replies.pop()(None)
    assert not any(isinstance(c, tuple) and c[0] == '' for c in calls)


def test_transform_error_emits_an_error_page(qapp, monkeypatch):
    from app import code_loading
    from app.renderer import _MarkdownRenderWorker

    def fail(_html):
        raise ValueError('transform failure')

    monkeypatch.setattr(code_loading, 'defer_code_highlighting', fail)
    results = []
    worker = _MarkdownRenderWorker(18, text='# Source')
    worker.on_ready = lambda *args: results.append(args)
    worker.run()
    assert len(results) == 1
    assert results[0][0] == 18
    assert 'markdown-viewer-render-error' in results[0][2]
    assert 'transform failure' in results[0][2]
    results.clear()
    worker = _MarkdownRenderWorker(19, text='# Source', defer_code=False)
    worker.on_ready = lambda *args: results.append(args)
    worker.run()
    assert 'markdown-viewer-render-error' not in results[0][2]
    assert '<h1' in results[0][2]
