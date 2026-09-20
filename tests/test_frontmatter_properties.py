import codecs

import pytest

from app.frontmatter_properties import PropertiesDocument


@pytest.mark.parametrize("encoding,bom", [
    ("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE), ("cp950", b""),
])
def test_property_update_keeps_body_bytes_and_unknown_fields(encoding, bom):
    front = ('---\r\n# keep comment\r\ntitle: old # inline\r\n'
             'unknown:\r\n  nested: [1, 2]\r\n---\r\n')
    body = '# 中文\r\nline\ntrailing  \r\n'
    raw = bom + (front + body).encode(encoding)
    document = PropertiesDocument.parse(raw)
    result = document.update({"title": "新標題", "tags": ["a", "b"]})
    parsed = PropertiesDocument.parse(result)
    assert parsed.values["title"] == "新標題"
    assert parsed.values["tags"] == ["a", "b"]
    assert parsed.body_bytes == document.body_bytes == body.encode(encoding)
    assert 'unknown:\r\n  nested: [1, 2]\r\n'.encode(encoding) in result
    assert '# keep comment\r\n'.encode(encoding) in result
    assert result.startswith(bom)


@pytest.mark.parametrize("source", [
    "---\ntitle: |\n  first\n  second\nnext: keep\n---\nbody",
    "---\ntitle:\n  - first\n  - second\nnext: keep\n---\nbody",
    "---\ntitle:\nnext: keep\n---\nbody",
    "---\ntitle: {a: 1}\nnext: keep\n---\nbody",
])
def test_complex_value_replacement_preserves_next_key_and_body(source):
    document = PropertiesDocument.parse(source.encode())
    parsed = PropertiesDocument.parse(document.update({"title": "line1\nline2"}))
    assert parsed.values == {"title": "line1\nline2", "next": "keep"}
    assert parsed.body_bytes == b"body"


def test_add_properties_to_plain_note_preserves_entire_body():
    raw = b"# title\r\n\ntext  \n"
    result = PropertiesDocument.parse(raw).update({"tags": ["tag"]})
    assert PropertiesDocument.parse(result).body_bytes == raw


@pytest.mark.parametrize("source", [
    "---\nbad: [\n---\nbody", "---\ntitle: missing close",
    "---\na: 1\na: 2\n---\nbody", "---\na: &ref one\nb: *ref\n---\nbody",
    "---\n- sequence\n---\nbody", "---\na: !!python/object:os.system ''\n---\nbody",
])
def test_invalid_or_unsafe_yaml_refuses_edits(source):
    with pytest.raises(ValueError):
        PropertiesDocument.parse(source.encode())


def test_unknown_date_and_comments_remain_verbatim_when_other_value_changes():
    raw = b"---\ndate: 2026-09-21 # keep\ntitle: old\n---\nbody"
    result = PropertiesDocument.parse(raw).update({"title": "new"})
    assert b"date: 2026-09-21 # keep\n" in result


@pytest.mark.parametrize("indent", [b"", b"  ", b"    "])
def test_comment_before_unknown_key_survives_collection_replacement(indent):
    comment = indent + b"# important unknown field comment\n"
    raw = b"---\ntitle:\n  - one\n\n" + comment + b"unknown: keep\n---\nbody"
    result = PropertiesDocument.parse(raw).update({"title": "new"})
    assert comment + b"unknown: keep\n" in result
    assert PropertiesDocument.parse(result).body_bytes == b"body"
