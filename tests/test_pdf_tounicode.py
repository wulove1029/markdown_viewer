"""ToUnicode repair for CJK glyphs that Chromium reverse-maps to radicals.

The PDFs here are built by hand with the same structure Skia writes: a Type0
font whose ToUnicode maps a shared glyph to a Kangxi radical, and a
``/Span <</ActualText ...>> BDC`` around each use of that glyph.
"""

from __future__ import annotations

import re

import fitz
import pytest

from app import atomic_io, pdf_tounicode
from app.pdf_tounicode import repair_radical_tounicode, repair_radical_tounicode_bytes

_FONT = (
    b"<< /Type /Font /Subtype /Type0 /BaseFont /Shared /Encoding /Identity-H"
    b" /DescendantFonts [6 0 R] /ToUnicode 7 0 R >>"
)
_CID_FONT = (
    b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Shared /CIDToGIDMap /Identity"
    b" /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
    b" /FontDescriptor 8 0 R >>"
)
_DESCRIPTOR = (
    b"<< /Type /FontDescriptor /FontName /Shared /Flags 4 /FontBBox [0 0 1000 1000]"
    b" /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >>"
)
_CMAP_HEAD = (
    "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
    "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
    "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
)
_CMAP_TAIL = "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"


def _stream(data: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def _cmap(body: str) -> bytes:
    return (_CMAP_HEAD + body + _CMAP_TAIL).encode("ascii")


def _pdf(content: bytes, cmap_body: str, form: bytes | None = None) -> bytes:
    resources = b"/Font << /F1 5 0 R >>"
    if form is not None:
        resources += b" /XObject << /X1 9 0 R >>"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << "
        + resources + b" >> /Contents 4 0 R >>",
        _stream(content),
        _FONT,
        _CID_FONT,
        _stream(_cmap(cmap_body)),
        _DESCRIPTOR,
    ]
    if form is not None:
        objects.append(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 300 100]"
            b" /Resources << /Font << /F1 5 0 R >> >> /Length %d >>\nstream\n" % len(form)
            + form + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref)
    return bytes(out)


def _tounicode_text(data: bytes) -> str:
    """What extractors that ignore /ActualText (pypdf and others) return."""
    flags = fitz.TEXTFLAGS_TEXT | fitz.TEXT_IGNORE_ACTUALTEXT
    with fitz.open(stream=data, filetype="pdf") as document:
        return "".join(page.get_text(flags=flags) for page in document).strip()


def _cmap_text(data: bytes) -> str:
    with fitz.open(stream=data, filetype="pdf") as document:
        return document.xref_stream(7).decode("latin-1")


SPAN_ONE = b"/Span<</ActualText <FEFF4E00> >> BDC\n<0001> Tj\nEMC\n"


def test_shared_glyph_is_mapped_back_to_the_unified_ideograph():
    content = b"BT\n/F1 12 Tf\n1 0 0 1 10 50 Tm\n" + SPAN_ONE + b"<0002> Tj\nET"
    original = _pdf(content, "2 beginbfchar\n<0001> <2F00>\n<0002> <3001>\nendbfchar\n")
    assert _tounicode_text(original) == "⼀、"

    fixed, repaired = repair_radical_tounicode_bytes(original)

    assert fixed == 1
    assert _tounicode_text(repaired) == "一、"
    assert "<0001> <4E00>" in _cmap_text(repaired)
    # Rendering inputs are untouched: same page content, ActualText kept.
    with fitz.open(stream=repaired, filetype="pdf") as document:
        assert document.xref_stream(4) == content
        assert document[0].get_text().strip() == "一、"


def test_bfrange_entries_are_split_and_other_codes_survive():
    # Font switch inside q/Q, a TJ array and a literal ActualText string.
    content = (
        b"q BT /F9 12 Tf ET Q\n"
        b"BT /F1 12 Tf 10 50 Td\n"
        b"/Span<</ActualText (\\376\\377\\166\\356)>> BDC [<0003>] TJ EMC\n"
        b"<0001> Tj ET"
    )
    original = _pdf(content, "1 beginbfrange\n<0001> <0003> <2F6A>\nendbfrange\n")
    assert _tounicode_text(original) == "⽬⽪"

    fixed, repaired = repair_radical_tounicode_bytes(original)

    assert fixed == 1
    cmap = _cmap_text(repaired)
    assert "<0003> <76EE>" in cmap
    # Codes with no ActualText evidence keep Skia's original mapping.
    assert "<0001> <2F6A>" in cmap and "<0002> <2F6B>" in cmap
    assert "beginbfrange" not in cmap
    assert _tounicode_text(repaired) == "目⽪"


def test_text_inside_form_xobjects_is_used_as_evidence():
    form = b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET"
    original = _pdf(b"/X1 Do", "1 beginbfchar\n<0001> <2F00>\nendbfchar\n", form=form)

    fixed, repaired = repair_radical_tounicode_bytes(original)

    assert fixed == 1
    assert _tounicode_text(repaired) == "一"


@pytest.mark.parametrize(
    "content",
    [
        # The document really contains the radical: no ActualText at all.
        b"BT /F1 12 Tf 10 50 Td <0001> Tj ET",
        # Two different unified ideographs claim one glyph: no safe choice.
        b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE
        + b"/Span<</ActualText <FEFF4E8C> >> BDC <0001> Tj EMC ET",
        # A multi-glyph span is not evidence for any single glyph.
        b"BT /F1 12 Tf 10 50 Td /Span<</ActualText <FEFF4E004E00> >> BDC"
        b" <00010001> Tj EMC ET",
        # Evidence that is itself a radical changes nothing.
        b"BT /F1 12 Tf 10 50 Td /Span<</ActualText <FEFF2F00> >> BDC <0001> Tj EMC ET",
        # ...not even when an ideograph also claims the glyph.
        b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE
        + b"/Span<</ActualText <FEFF2F00> >> BDC <0001> Tj EMC ET",
    ],
    ids=["radical-only", "two-ideographs", "multi-glyph", "radical-evidence",
         "radical-and-ideograph-evidence"],
)
def test_without_unambiguous_evidence_the_pdf_is_left_byte_identical(content):
    original = _pdf(content, "1 beginbfchar\n<0001> <2F00>\nendbfchar\n")

    assert repair_radical_tounicode_bytes(original) == (0, original)


def test_repair_rewrites_the_file_in_place(tmp_path):
    path = tmp_path / "export.pdf"
    path.write_bytes(_pdf(b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET",
                          "1 beginbfchar\n<0001> <2F00>\nendbfchar\n"))

    assert repair_radical_tounicode(path) == 1
    assert _tounicode_text(path.read_bytes()) == "一"
    assert repair_radical_tounicode(path) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export.pdf"]


def test_broken_or_missing_files_are_never_touched(tmp_path, monkeypatch):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\n/ToUnicode garbage")
    assert repair_radical_tounicode(broken) == 0
    assert broken.read_bytes() == b"%PDF-1.4\n/ToUnicode garbage"
    assert repair_radical_tounicode(tmp_path / "missing.pdf") == 0

    monkeypatch.setattr(pdf_tounicode, "load_pymupdf", lambda: None)
    data = _pdf(b"BT /F1 12 Tf " + SPAN_ONE + b"ET",
                "1 beginbfchar\n<0001> <2F00>\nendbfchar\n")
    assert repair_radical_tounicode_bytes(data) == (0, data)

ONE_RADICAL = "1 beginbfchar\n<0001> <2F00>\nendbfchar\n"


@pytest.mark.parametrize(
    ("content", "form"),
    [
        # 一⼀一: the middle use is bare, so Chromium knew it was the radical.
        (b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"<0001> Tj " + SPAN_ONE + b"ET", None),
        # The bare use sits in another stream that carries no ActualText.
        (b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET /X1 Do",
         b"BT /F1 12 Tf 10 20 Td <0001> Tj ET"),
        # Tagged content without ActualText is still a bare use.
        (b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE
         + b"/P <</MCID 0>> BDC [<0001>] TJ EMC ET", None),
    ],
    ids=["same-stream", "other-stream", "tagged-content"],
)
def test_author_typed_radicals_keep_their_glyph_unchanged(content, form):
    original = _pdf(content, ONE_RADICAL, form=form)

    assert repair_radical_tounicode_bytes(original) == (0, original)


@pytest.mark.parametrize(
    ("actual_texts", "expected"),
    [
        # 一 / ⼀ / ㇐ (U+31D0) share one glyph in Microsoft JhengHei.
        (["4E00", "31D0"], "4E00"),
        (["31D0", "4E00"], "4E00"),
        # Extension B ideograph (surrogate pair) against a CJK stroke.
        (["D841DE28", "31D6"], "D841DE28"),
    ],
)
def test_conflicting_evidence_prefers_the_unified_ideograph(actual_texts, expected):
    spans = b"".join(
        b"/P <</MCID 0>> BDC /Span<</ActualText <FEFF%s> >> BDC <0001> Tj EMC EMC "
        % text.encode("ascii")
        for text in actual_texts
    )
    original = _pdf(b"BT /F1 12 Tf 10 50 Td " + spans + b"ET", ONE_RADICAL)

    fixed, repaired = repair_radical_tounicode_bytes(original)

    assert fixed == 1
    assert f"<0001> <{expected}>" in _cmap_text(repaired)
    assert _tounicode_text(repaired) == bytes.fromhex(expected).decode("utf-16-be") * 2


def test_encrypted_files_are_never_rewritten(tmp_path):
    plain = _pdf(b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET", ONE_RADICAL)
    assert repair_radical_tounicode_bytes(plain)[0] == 1
    with fitz.open(stream=plain, filetype="pdf") as document:
        # Owner password only: opens without a password, permissions restricted.
        locked = document.tobytes(
            encryption=fitz.PDF_ENCRYPT_AES_128, owner_pw="owner", user_pw="",
            permissions=fitz.PDF_PERM_PRINT,
        )
    with fitz.open(stream=locked, filetype="pdf") as document:
        assert not document.needs_pass

    assert repair_radical_tounicode_bytes(locked) == (0, locked)
    path = tmp_path / "locked.pdf"
    path.write_bytes(locked)
    assert repair_radical_tounicode(path) == 0
    assert path.read_bytes() == locked


def test_failed_replace_leaves_the_original_file(tmp_path, monkeypatch):
    path = tmp_path / "export.pdf"
    original = _pdf(b"BT /F1 12 Tf 10 50 Td " + SPAN_ONE + b"ET", ONE_RADICAL)
    path.write_bytes(original)

    def refuse(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(atomic_io.os, "replace", refuse)

    assert repair_radical_tounicode(path) == 0
    assert path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export.pdf"]


def test_rebuilt_cmap_is_split_into_blocks_of_at_most_100_entries():
    # 150 codes from one bfrange plus the radical: 151 bfchar entries.
    body = (
        "1 beginbfrange\n<0001> <0096> <3400>\nendbfrange\n"
        "1 beginbfchar\n<0097> <2F00>\nendbfchar\n"
    )
    content = (
        b"BT /F1 12 Tf 10 50 Td <000100640096> Tj "
        b"/Span<</ActualText <FEFF4E00> >> BDC <0097> Tj EMC ET"
    )
    original = _pdf(content, body)

    fixed, repaired = repair_radical_tounicode_bytes(original)

    assert fixed == 1
    cmap = _cmap_text(repaired)
    counts = [int(n) for n in re.findall(r"(\d+) beginbfchar", cmap)]
    assert counts == [100, 51]
    assert "beginbfrange" not in cmap
    assert "<0001> <3400>" in cmap and "<0096> <3495>" in cmap and "<0097> <4E00>" in cmap
    assert _tounicode_text(repaired) == "\u3400\u3463\u3495一"
