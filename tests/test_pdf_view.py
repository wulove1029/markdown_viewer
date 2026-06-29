"""Tests for PDF outline extraction (PyMuPDF-backed, no Qt widget needed)."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_view import extract_outline


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    doc.set_toc(
        [
            [1, "Chapter 1", 1],
            [2, "Section 1.1", 2],
            [1, "Chapter 2", 3],
        ]
    )
    doc.save(str(path))
    doc.close()
    return path


def test_outline_extraction(sample_pdf):
    # 1-based PDF pages become 0-based for QPdfPageNavigator.jump().
    assert extract_outline(sample_pdf) == [
        (1, "Chapter 1", 0),
        (2, "Section 1.1", 1),
        (1, "Chapter 2", 2),
    ]


def test_outline_empty_when_no_toc(tmp_path):
    path = tmp_path / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    doc.close()
    assert extract_outline(path) == []


def test_outline_none_path_is_empty():
    assert extract_outline(None) == []


def test_outline_bad_file_is_empty(tmp_path):
    bad = tmp_path / "notreally.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    assert extract_outline(bad) == []
