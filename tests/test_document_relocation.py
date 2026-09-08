"""Relocation must preserve resource identity and all unrelated source bytes."""

import codecs
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from app.document_relocation import rebase_markdown_bytes, rebase_markdown_links


def _paths(tmp_path):
    old = tmp_path / "note.md"
    folder = tmp_path / "archive"
    folder.mkdir(exist_ok=True)
    return old, folder / old.name


def test_inline_links_keep_labels_titles_queries_fragments_and_special_names(tmp_path):
    old, new = _paths(tmp_path)
    original = (
        '[my label](assets/a%20b%23%3F%28%29.png?download=1#part "same title")\n'
        '![image](<assets/中文 a.png>)\n'
        r'[escaped](assets/a\(b\).pdf)' + '\n'
        '[self](note.md#heading)\n'
        '[web](https://example.com/a?q=x#p) [mail](mailto:a@example.com)\n'
        '[anchor](#heading) [root](/assets/a.png) [net](//example.com/a)\n'
    )
    result = rebase_markdown_links(original, old, new)
    assert '[my label](../assets/a%20b%23%3F%28%29.png?download=1#part "same title")' in result
    assert '![image](<../assets/%E4%B8%AD%E6%96%87%20a.png>)' in result
    assert '[escaped](../assets/a%28b%29.pdf)' in result
    assert '[self](note.md#heading)' in result
    assert result.splitlines()[-2:] == original.splitlines()[-2:]


def test_nested_image_and_multiline_destination_are_rebased(tmp_path):
    old, new = _paths(tmp_path)
    original = '[![alt](assets/a.png)](other.md)\n\n[long](\n <assets/my file.pdf>\n "a title"\n)\n'
    result = rebase_markdown_links(original, old, new)
    assert '[![alt](../assets/a.png)](../other.md)' in result
    assert '[long](\n <../assets/my%20file.pdf>\n "a title"\n)' in result


@pytest.mark.parametrize('suffix', ['?x=1&amp;copy;=2#hi', '&#63;x=1&amp;y=2&#35;part', '?x=1&y=2#part'])
def test_query_entities_are_not_decoded_twice(tmp_path, suffix):
    old, new = _paths(tmp_path)
    source = f'[x](assets/a.png{suffix})'
    result = rebase_markdown_links(source, old, new)
    assert result == f'[x](../assets/a.png{suffix})'
    md = MarkdownIt()
    before = next(token.attrGet('href') for token in md.parse(source)[1].children if token.type == 'link_open')
    after = next(token.attrGet('href') for token in md.parse(result)[1].children if token.type == 'link_open')
    assert after == '../' + before


def test_references_including_duplicates_and_quoted_continuation(tmp_path):
    old, new = _paths(tmp_path)
    original = (
        '[x][id] ![y][pic]\n\n'
        '[id]: <assets/my file.pdf> "keep title"\n'
        '[pic]: assets/photo.png\n'
        '[id]: assets/unused.pdf\n\n'
        '> [quoted]:\n>   assets/quoted.pdf\n> [z][quoted]\n'
    )
    result = rebase_markdown_links(original, old, new)
    assert '[x][id] ![y][pic]' in result
    assert '[id]: <../assets/my%20file.pdf> "keep title"' in result
    assert '[pic]: ../assets/photo.png' in result
    assert '[id]: ../assets/unused.pdf' in result
    assert '>   ../assets/quoted.pdf' in result


def test_code_spans_fences_indented_code_and_comments_remain_identical(tmp_path):
    old, new = _paths(tmp_path)
    protected = (
        '`[a](assets/a.png)`\n\n'
        '``a ` [b](assets/b.png)\nnext line``\n\n'
        '~~~md\n[c](assets/c.png)\n~~~\n\n'
        '> ```md\n> [d](assets/d.png)\n> ```\n\n'
        '    [e](assets/e.png)\n\n'
        '<!-- [f](assets/f.png) <img src="assets/f.png"> -->\n\n'
        '<pre>[g](assets/g.png)<img src="assets/g.png"></pre>\n\n'
        '<code>[h](assets/h.png)</code>\n\n'
        '`<img src="assets/i.png">`\n\n'
    )
    assert rebase_markdown_links(protected + '[real](assets/real.png)', old, new) == protected + '[real](../assets/real.png)'


def test_unmatched_backticks_cannot_form_code_across_paragraph_boundaries(tmp_path):
    old, new = _paths(tmp_path)
    source = 'Unmatched backtick `\n\n[real](assets/a.png)\n\nAnother `'
    assert rebase_markdown_links(source, old, new) == source.replace('(assets/a.png)', '(../assets/a.png)')


def test_html_resources_preserve_other_attributes_and_remote_urls(tmp_path):
    old, new = _paths(tmp_path)
    original = (
        '<img alt="unchanged title" src="assets/a%20b.png?x=1&amp;y=2#part">\n\n'
        'Text <a href="docs/a.pdf">open</a> <img src=assets/plain.png>\n\n'
        '<video poster="assets/poster.png" src="https://example.com/video.mp4"></video>\n'
    )
    result = rebase_markdown_links(original, old, new)
    assert 'alt="unchanged title"' in result
    assert 'src="../assets/a%20b.png?x=1&amp;y=2#part"' in result
    assert 'href="../docs/a.pdf"' in result
    assert 'src=../assets/plain.png' in result
    assert 'poster="../assets/poster.png"' in result
    assert 'src="https://example.com/video.mp4"' in result


def test_markdown_like_text_within_html_attributes_is_preserved(tmp_path):
    old, new = _paths(tmp_path)
    original = '<img alt="[fake](assets/not-a-link.png)" src="assets/a.png">'
    assert rebase_markdown_links(original, old, new) == original.replace('src="assets/a.png"', 'src="../assets/a.png"')


@pytest.mark.parametrize('protected', [
    '$[x](assets/a.png)$', '$$\n[x](assets/a.png)\n$$',
    '---\ntitle: "[x](assets/a.png)"\n---',
])
def test_math_and_front_matter_are_preserved_verbatim(tmp_path, protected):
    old, new = _paths(tmp_path)
    original = protected + '\n\n[real](assets/real.png)'
    assert rebase_markdown_links(original, old, new) == protected + '\n\n[real](../assets/real.png)'


@pytest.mark.parametrize("text", [
    '[[ambiguous wiki]]',
    '<img srcset="assets/a.png 1x, assets/b.png 2x">',
    '<img style="background:url(assets/a.png)">',
    '<style>body { background:url(assets/a.png) }</style>',
    '<base href="assets/">',
])
def test_unsupported_resource_syntax_refuses_cross_folder_but_not_same_folder(tmp_path, text):
    old, new = _paths(tmp_path)
    with pytest.raises(OSError):
        rebase_markdown_links(text, old, new)
    assert rebase_markdown_links(text, old, old.with_name('renamed.md')) == text


@pytest.mark.parametrize("encoding,bom", [
    ('utf-8', b''), ('utf-8', codecs.BOM_UTF8), ('cp950', b''),
    ('gbk', b''), ('utf-16-le', codecs.BOM_UTF16_LE), ('utf-16-be', codecs.BOM_UTF16_BE),
])
def test_rebase_bytes_preserves_encoding_bom_and_mixed_line_endings(tmp_path, encoding, bom):
    old, new = _paths(tmp_path)
    original = '# 筆記\r\n[x](assets/a.pdf)\r\n\r\n[x][ref]\n\n[ref]: assets/b.pdf\r'
    expected = original.replace('(assets/a.pdf)', '(../assets/a.pdf)').replace(': assets/b.pdf', ': ../assets/b.pdf')
    assert rebase_markdown_bytes(bom + original.encode(encoding), old, new) == bom + expected.encode(encoding)


def test_unrecognized_or_binary_encoding_refuses_relocation(tmp_path):
    old, new = _paths(tmp_path)
    with pytest.raises(OSError):
        rebase_markdown_bytes(b'\x00\x01\x02', old, new)


def test_local_resource_identity_does_not_require_existing_target(tmp_path):
    old, new = _paths(tmp_path)
    assert rebase_markdown_links('[later](future/note.md)', old, new) == '[later](../future/note.md)'


def test_same_folder_rename_preserves_exact_bytes(tmp_path):
    old = tmp_path / 'note.md'
    data = b'\xff\x00\x80'
    assert rebase_markdown_bytes(data, old, old.with_name('renamed.md')) == data
