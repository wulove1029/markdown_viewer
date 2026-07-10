"""Tests for wiki-link extraction and the forward/back link index."""

from pathlib import Path

from app.links import LinkIndex, extract_wikilinks


def test_extract_plain_and_aliased():
    links = extract_wikilinks("a [[One]] b [[Two|second]] c")
    assert links == [("One", None), ("Two", "second")]


def test_extract_keeps_heading_and_path():
    links = extract_wikilinks("[[Note#Section]] and [[sub/Other]]")
    assert links == [("Note#Section", None), ("sub/Other", None)]


def test_extract_ignores_single_brackets():
    assert extract_wikilinks("[not a wikilink](x) and [single]") == []


def test_extract_ignores_inline_code():
    text = "real [[One]] and `[[Hidden]]` and ``[[Also hidden]]``"
    assert extract_wikilinks(text) == [("One", None)]


def test_extract_ignores_fenced_code():
    text = "before [[Visible]]\n```md\n[[Hidden]]\n```\nafter [[Also|alias]]"
    assert extract_wikilinks(text) == [("Visible", None), ("Also", "alias")]


def test_extract_does_not_match_across_lines():
    assert extract_wikilinks("[[Never\nmatches]] and [[Yes]]") == [("Yes", None)]


def _index(docs):
    idx = LinkIndex()
    idx.build([(Path(p), t) for p, t in docs])
    return idx


def test_forward_and_backlinks():
    idx = _index([
        ("/v/a.md", "see [[b]] and [[sub/c]]"),
        ("/v/b.md", "see [[a]]"),
        ("/v/sub/c.md", "leaf note"),
    ])
    assert {Path(p).name for p in idx.forward[str(Path("/v/a.md"))]} == {"b.md", "c.md"}
    assert [Path(p).name for p in idx.backlinks(Path("/v/b.md"))] == ["a.md"]
    assert [Path(p).name for p in idx.backlinks(Path("/v/sub/c.md"))] == ["a.md"]


def test_resolve_basename_and_extension():
    idx = _index([("/v/note.md", ""), ("/v/x.md", "")])
    assert idx.resolve("note") == Path("/v/note.md")
    assert idx.resolve("note.md") == Path("/v/note.md")
    assert idx.resolve("Note") == Path("/v/note.md")  # case-insensitive
    assert idx.resolve("missing") is None


def test_resolve_prefers_same_folder_on_collision():
    idx = _index([
        ("/v/a/dup.md", ""),
        ("/v/b/dup.md", ""),
        ("/v/b/here.md", "[[dup]]"),
    ])
    assert idx.resolve("dup", Path("/v/b/here.md")) == Path("/v/b/dup.md")


def test_resolve_folder_qualified():
    idx = _index([("/v/a/note.md", ""), ("/v/b/note.md", "")])
    assert idx.resolve("b/note") == Path("/v/b/note.md")


def test_self_links_excluded_from_forward():
    idx = _index([("/v/a.md", "I link to [[a]] myself")])
    assert idx.forward[str(Path("/v/a.md"))] == set()


def test_heading_link_resolves_to_file():
    idx = _index([("/v/guide.md", ""), ("/v/x.md", "[[guide#install]]")])
    assert idx.resolve("guide#install", Path("/v/x.md")) == Path("/v/guide.md")
