"""Tests for Markdown -> Word (.docx) export."""

import pytest

pytest.importorskip("docx")

from docx import Document
from docx.oxml.ns import qn

from app.docx_export import BODY_FONT, CODE_FONT, export_markdown_to_docx


def _text(doc):
    return "\n".join(p.text for p in doc.paragraphs)


def _east_asia_font(run):
    r_pr = run._element.rPr
    if r_pr is None or r_pr.rFonts is None:
        return None
    return r_pr.rFonts.get(qn("w:eastAsia"))


@pytest.fixture
def png_fixture(tmp_path):
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 24))
    pix.clear_with(210)
    path = tmp_path / "image.png"
    pix.save(str(path))
    return path


def test_export_roundtrip_heading_table_code_chinese_and_image(tmp_path, png_fixture):
    md = (
        "# 標題\n\n"
        "中文段落 with **bold** text.\n\n"
        "```py\nprint('hi')\n```\n\n"
        "| A | B |\n| - | - |\n| 1 | 2 |\n\n"
        f"![sample]({png_fixture.name})\n"
    )
    out = tmp_path / "doc.docx"

    count = export_markdown_to_docx(md, out, base_dir=tmp_path)

    assert count >= 5
    doc = Document(str(out))
    assert doc.paragraphs[0].style.name == "Heading 1"
    assert doc.paragraphs[0].text == "標題"
    assert any(run.text.startswith("中文段落") and _east_asia_font(run) == BODY_FONT
               for p in doc.paragraphs for run in p.runs)

    assert len(doc.tables) == 1
    table = doc.tables[0]
    assert table.cell(0, 0).text == "A"
    assert table.cell(1, 1).text == "2"

    code_runs = [run for p in doc.paragraphs for run in p.runs if "print('hi')" in run.text]
    assert code_runs
    assert _east_asia_font(code_runs[0]) == CODE_FONT
    assert code_runs[0].font.name == CODE_FONT

    assert len(doc.inline_shapes) == 1


def test_image_provider_embeds_mermaid_and_omits_source(tmp_path, png_fixture):
    md = "## Diagram\n\n```mermaid\ngraph TD; A-->B;\n```\n"
    out = tmp_path / "mermaid.docx"
    calls = []

    def provider(kind, source):
        calls.append((kind, source))
        return str(png_fixture)

    export_markdown_to_docx(md, out, image_provider=provider)
    doc = Document(str(out))

    assert calls == [("mermaid", "graph TD; A-->B;")]
    assert len(doc.inline_shapes) == 1
    assert "graph TD" not in _text(doc)


def test_provider_none_falls_back_to_source_text(tmp_path):
    md = "## Diagram\n\n```mermaid\ngraph LR; X-->Y;\n```\n"
    out = tmp_path / "fallback.docx"

    export_markdown_to_docx(md, out, image_provider=lambda kind, source: None)
    doc = Document(str(out))

    assert len(doc.inline_shapes) == 0
    assert "Mermaid diagram source" in _text(doc)
    assert "graph LR; X-->Y;" in _text(doc)


def test_missing_image_falls_back_to_placeholder(tmp_path):
    md = "![missing alt](missing.png)\n"
    out = tmp_path / "missing.docx"

    export_markdown_to_docx(md, out, base_dir=tmp_path)
    doc = Document(str(out))

    assert "[Image: missing alt]" in _text(doc)
