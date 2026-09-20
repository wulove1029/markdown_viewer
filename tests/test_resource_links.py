from pathlib import Path

import pytest
from markdown_it import MarkdownIt

from app.resource_links import decode_resource_path, encode_resource_path, markdown_resource_link


@pytest.mark.parametrize("path", ["assets/a b#c%.png", "附件/圖 (1)[2].png", "../a/b.md"])
@pytest.mark.parametrize("image", [False, True])
def test_resource_links_round_trip_through_markdown_parser(path, image):
    label = r"a[b]\c"
    source = markdown_resource_link(Path(path), label=label, image=image)
    tokens = MarkdownIt().parse(source)[1].children
    target = next(t for t in tokens if t.type == ("image" if image else "link_open"))
    destination = target.attrGet("src" if image else "href")
    assert decode_resource_path(destination) == Path(path).as_posix()
    assert "#" not in encode_resource_path(path)
    if image:
        assert target.content == r"a\[b\]\\c"
    else:
        assert next(t for t in tokens if t.type == "text").content == label
