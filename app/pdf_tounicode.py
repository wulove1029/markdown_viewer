"""Repair the text layer of Chromium PDFs for CJK fonts with shared radical glyphs.

Fonts such as Microsoft JhengHei, Yu Gothic and Noto Sans TC map a Kangxi
radical (U+2F00-U+2FDF) or a CJK radicals supplement code point
(U+2E80-U+2EFF) and the ordinary ideograph to the *same* glyph.  When
QWebEnginePage.printToPdf writes a PDF, Chromium's Skia backend builds each
font's ToUnicode CMap by reverse-mapping every glyph to the lowest code point
that reaches it, so the glyph shared by "一" (U+4E00) and "⼀" (U+2F00) is
recorded as U+2F00.  Skia does wrap each such glyph in a ``/Span
<</ActualText ...>> BDC`` marked-content sequence carrying the real character,
but many viewers and extractors read only ToUnicode, so copy, search and
downstream text extraction return the radical instead of the ideograph.

``repair_radical_tounicode`` rewrites only those ToUnicode entries, using the
ActualText spans in the same file as the source of truth.  A glyph that is
also shown *without* ActualText (Chromium then knew the text really was the
radical) is left alone, so radicals the author typed stay radicals.  Page
content, fonts and therefore the rendered appearance are left untouched;
encrypted files are never rewritten.
"""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
from array import array
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .atomic_io import atomic_write_bytes
from .pymupdf_loader import load_pymupdf

log = logging.getLogger(__name__)

# CJK Radicals Supplement + Kangxi Radicals (+ the unassigned tail of that block).
RADICAL_FIRST = 0x2E80
RADICAL_LAST = 0x2FDF

_ENTRIES_PER_BLOCK = 100  # PDF CMap limit for one bfchar block.

_CMAP_BLOCK = re.compile(r"(\d+)\s+begin(bfchar|bfrange)(.*?)end\2", re.S)
_CMAP_TOKEN = re.compile(r"<([0-9A-Fa-f\s]*)>|\[|\]")
_REF = re.compile(r"/([^\s/<>\[\]()%{}]+)\s*(\d+)\s+\d+\s+R")
# Only the tokens evidence collection needs are matched: numbers, arrays,
# dictionaries and every other operator are skipped by the regex engine.
# Names, strings and comments are still consumed whole, so operator-like bytes
# inside them are never misread as operators.
_CONTENT_TOKEN = re.compile(
    rb"%[^\r\n]*"
    rb"|<(?P<hex>[0-9A-Fa-f\s]*)>"
    rb"|\((?P<lit>(?:[^\\()]|\\.|\((?:[^\\()]|\\.)*\))*)\)"
    rb"|(?P<name>/[^\s/<>\[\]()%{}]*)"
    rb"|(?<![A-Za-z0-9*'\"])"
    rb"(?P<op>Tf|Tj|TJ|BDC|BMC|EMC|DP|MP|ID|q|Q|'|\")"
    rb"(?![A-Za-z0-9*'\"])",
    re.S,
)
_INLINE_IMAGE_END = re.compile(rb"\sEI(?=[\s/<\[(%]|$)")
_LITERAL_ESCAPES = {
    ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b",
    ord("f"): b"\f", ord("("): b"(", ord(")"): b")", ord("\\"): b"\\",
}


def is_radical(text: str) -> bool:
    """True for a single code point in the radical blocks Skia prefers."""
    return len(text) == 1 and RADICAL_FIRST <= ord(text) <= RADICAL_LAST


def is_unified_ideograph(text: str) -> bool:
    """True for one CJK unified ideograph, extension blocks included."""
    return len(text) == 1 and unicodedata.name(text, "").startswith("CJK UNIFIED IDEOGRAPH-")


def repair_radical_tounicode_strict(path: str | Path) -> int:
    """Like :func:`repair_radical_tounicode` but lets failures propagate.

    The corrected file is written to a temporary file and swapped in with
    ``os.replace`` (``atomic_write_bytes``), so the PDF is never left half
    written.  If the file changed on disk while it was being analysed (another
    export to the same path finished meanwhile) it is left alone: replacing it
    would overwrite the newer PDF with a repaired copy of the older one.
    """
    target = Path(path)
    before = target.stat()
    data = target.read_bytes()
    fixed, repaired = repair_radical_tounicode_bytes(data)
    if not fixed:
        return 0
    after = target.stat()
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        log.info("PDF %s changed during text repair; leaving it as is", target)
        return 0
    atomic_write_bytes(target, repaired, backup=False)
    return fixed


def repair_radical_tounicode(path: str | Path) -> int:
    """Fix radical ToUnicode entries of the PDF at *path* in place.

    Returns the number of corrected glyph mappings (0 when the file needed no
    change).  Best effort: any failure leaves the file exactly as it was.
    """
    target = Path(path)
    try:
        return repair_radical_tounicode_strict(target)
    except OSError as exc:
        log.warning("Could not repair PDF text mapping of %s: %s", target, exc)
    except Exception:
        log.warning("Could not repair PDF text mapping of %s", target, exc_info=True)
    return 0


def repair_radical_tounicode_bytes(data: bytes) -> tuple[int, bytes]:
    """Return ``(corrected mappings, pdf bytes)``; *data* is returned unchanged
    when nothing needs fixing."""
    if b"/ToUnicode" not in data:
        return 0, data
    fitz = load_pymupdf()
    if fitz is None:
        return 0, data
    with fitz.open(stream=data, filetype="pdf") as doc:
        # An owner-password-only file opens without a password, but saving it
        # would silently drop its encryption and permission restrictions.
        if not doc.is_pdf or doc.needs_pass or doc.is_encrypted:
            return 0, data
        if doc.xref_get_key(-1, "Encrypt")[0] != "null":
            return 0, data
        fixed = _repair_document(doc)
        if not fixed:
            return 0, data
        return fixed, doc.tobytes()


# --- document level ---------------------------------------------------------


class _CMap:
    """One ToUnicode stream parsed into ``code -> text``."""

    def __init__(self, text: str, width: int, mapping: dict[int, str]):
        self.text = text
        self.width = width
        self.mapping = mapping
        # Codes whose ToUnicode is a radical: the only candidates for repair.
        self.suspect = {code for code, value in mapping.items() if is_radical(value)}
        # ActualText seen on single-glyph spans, per suspect code.
        self.evidence: dict[int, set[str]] = {}
        # Suspect codes also shown outside any ActualText span.
        self.bare: set[int] = set()

    def correction(self, code: int) -> str | None:
        """The character *code* should map to, or None to keep Skia's choice."""
        if code in self.bare:
            return None  # Chromium showed this glyph as the radical itself
        seen = self.evidence.get(code, set())
        if any(is_radical(text) for text in seen):
            return None  # an author-typed radical shares the glyph
        candidates = {text for text in seen if len(text) == 1}
        if len(candidates) == 1:
            return next(iter(candidates))
        # One glyph can serve 一, ⼀ and the stroke ㇐ (U+31D0): when several
        # characters really share it, ToUnicode can hold one, so prefer the
        # unified ideograph; the others keep their own ActualText.
        unified = [text for text in candidates if is_unified_ideograph(text)]
        return unified[0] if len(unified) == 1 else None


def _repair_document(doc: Any) -> int:
    cmaps: dict[int, _CMap] = {}
    clean: set[int] = set()
    font_cmap: dict[int, int] = {}
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Type") != ("name", "/Font"):
            continue
        kind, value = doc.xref_get_key(xref, "ToUnicode")
        if kind != "xref":
            continue
        cmap_xref = int(value.split()[0])
        if cmap_xref in clean:
            continue
        if cmap_xref not in cmaps:
            subtype = doc.xref_get_key(xref, "Subtype")[1]
            parsed = _parse_cmap(doc.xref_stream(cmap_xref), subtype == "/Type0")
            if parsed is None or not parsed.suspect:
                clean.add(cmap_xref)
                continue
            cmaps[cmap_xref] = parsed
        font_cmap[xref] = cmap_xref
    if not cmaps:
        return 0

    # Every stream is scanned, even one without ActualText: its bare uses of
    # a glyph are what keep author-typed radicals from being rewritten.
    for content, resources_owner in _content_streams(doc):
        fonts = {
            name: font_cmap[xref]
            for name, xref in _resource_refs(doc, resources_owner, "Font").items()
            if xref in font_cmap
        }
        if fonts:
            _collect_evidence(content, fonts, cmaps)

    fixed = 0
    for cmap_xref, cmap in cmaps.items():
        changes = {}
        for code in cmap.suspect:
            actual = cmap.correction(code)
            if actual is not None and not is_radical(actual):
                changes[code] = actual
        if not changes:
            continue
        cmap.mapping.update(changes)
        rebuilt = _rebuild_cmap(cmap)
        if rebuilt is None:
            continue
        doc.update_stream(cmap_xref, rebuilt.encode("latin-1"))
        fixed += len(changes)
    return fixed


def _content_streams(doc: Any) -> Iterator[tuple[bytes, int]]:
    """Yield ``(decoded content, xref owning its resources)`` for every page
    and every form XObject."""
    for page in doc:
        streams = page.get_contents()
        if streams:
            content = b"\n".join(doc.xref_stream(xref) or b"" for xref in streams)
            yield content, _resources_owner(doc, page.xref)
    for xref in range(1, doc.xref_length()):
        if (
            doc.xref_get_key(xref, "Subtype") == ("name", "/Form")
            and doc.xref_is_stream(xref)
        ):
            yield doc.xref_stream(xref) or b"", xref


def _resources_owner(doc: Any, page_xref: int) -> int:
    """Pages may inherit Resources from an ancestor /Pages node."""
    xref = page_xref
    for _ in range(64):
        if doc.xref_get_key(xref, "Resources")[0] != "null":
            return xref
        kind, value = doc.xref_get_key(xref, "Parent")
        if kind != "xref":
            break
        xref = int(value.split()[0])
    return page_xref


def _resource_refs(doc: Any, owner: int, category: str) -> dict[str, int]:
    kind, value = doc.xref_get_key(owner, f"Resources/{category}")
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    elif kind != "dict":
        return {}
    return {name: int(xref) for name, xref in _REF.findall(value)}


# --- ToUnicode CMap ---------------------------------------------------------


def _hex_bytes(token: str) -> bytes:
    digits = "".join(token.split())
    if len(digits) % 2:
        digits += "0"
    return bytes.fromhex(digits)


def _parse_cmap(raw: bytes | None, two_byte_default: bool) -> _CMap | None:
    if not raw:
        return None
    text = raw.decode("latin-1")
    space = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", text)
    if space:
        width = max(1, len(space.group(1)) // 2)
    else:
        width = 2 if two_byte_default else 1
    mapping: dict[int, str] = {}
    try:
        for match in _CMAP_BLOCK.finditer(text):
            tokens = [
                m.group(1) if m.group(1) is not None else m.group(0)
                for m in _CMAP_TOKEN.finditer(match.group(3))
            ]
            if match.group(2) == "bfchar":
                if len(tokens) % 2 or "[" in tokens or "]" in tokens:
                    return None
                for src, dst in zip(tokens[::2], tokens[1::2]):
                    mapping[int.from_bytes(_hex_bytes(src), "big")] = _utf16(_hex_bytes(dst))
            else:
                _parse_bfrange(tokens, mapping)
    except (ValueError, IndexError, UnicodeDecodeError):
        return None
    return _CMap(text, width, mapping)


def _parse_bfrange(tokens: list[str], mapping: dict[int, str]) -> None:
    index = 0
    while index < len(tokens):
        low = int.from_bytes(_hex_bytes(tokens[index]), "big")
        high = int.from_bytes(_hex_bytes(tokens[index + 1]), "big")
        if high < low or high - low > 0xFFFF:
            raise ValueError("bad bfrange")
        if tokens[index + 2] == "[":
            end = tokens.index("]", index + 3)
            for offset, dst in enumerate(tokens[index + 3:end]):
                mapping[low + offset] = _utf16(_hex_bytes(dst))
            index = end + 1
        else:
            base = _hex_bytes(tokens[index + 2])
            start = int.from_bytes(base, "big")
            for offset in range(high - low + 1):
                value = (start + offset).to_bytes(len(base), "big")
                mapping[low + offset] = _utf16(value)
            index += 3


def _utf16(data: bytes) -> str:
    return data.decode("utf-16-be")


def _rebuild_cmap(cmap: _CMap) -> str | None:
    """Re-emit every mapping as bfchar blocks between the original header and
    trailer; refuse when anything else sits between the mapping blocks."""
    blocks = list(_CMAP_BLOCK.finditer(cmap.text))
    if not blocks:
        return None
    start, end = blocks[0].start(), blocks[-1].end()
    between = _CMAP_BLOCK.sub("", cmap.text[start:end])
    if between.strip():
        return None
    entries = [
        f"<{code:0{cmap.width * 2}X}> <{value.encode('utf-16-be').hex().upper()}>"
        for code, value in sorted(cmap.mapping.items())
    ]
    body = []
    for first in range(0, len(entries), _ENTRIES_PER_BLOCK):
        chunk = entries[first:first + _ENTRIES_PER_BLOCK]
        body.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar")
    return cmap.text[:start] + "\n".join(body) + cmap.text[end:]


# --- content streams --------------------------------------------------------


def _literal(raw: bytes) -> bytes:
    out = bytearray()
    index = 0
    while index < len(raw):
        byte = raw[index]
        if byte != 0x5C or index + 1 >= len(raw):
            out.append(byte)
            index += 1
            continue
        nxt = raw[index + 1]
        if nxt in _LITERAL_ESCAPES:
            out += _LITERAL_ESCAPES[nxt]
            index += 2
        elif 0x30 <= nxt <= 0x37:
            digits = re.match(rb"[0-7]{1,3}", raw[index + 1:index + 4])
            assert digits is not None
            out.append(int(digits.group(0), 8) & 0xFF)
            index += 1 + len(digits.group(0))
        elif nxt in (0x0A, 0x0D):
            index += 2 + (nxt == 0x0D and raw[index + 2:index + 3] == b"\n")
        else:
            out.append(nxt)
            index += 2
    return bytes(out)


def _text_string(data: bytes) -> str:
    if data[:2] == b"\xfe\xff":
        return data[2:].decode("utf-16-be", errors="replace")
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", errors="replace")
    return data.decode("latin-1")


def _tokens(content: bytes) -> Iterator[tuple[str, bytes]]:
    """Yield ``(kind, raw bytes)`` for names, strings and relevant operators."""
    pos = 0
    while True:
        restart = None
        for match in _CONTENT_TOKEN.finditer(content, pos):
            kind = match.lastgroup
            if kind is None:
                continue  # comment
            raw = match.group(kind)
            if kind == "op" and raw == b"ID":
                end = _INLINE_IMAGE_END.search(content, match.end())
                restart = len(content) if end is None else end.end()
                break
            yield kind, raw
        if restart is None:
            return
        pos = restart


def _string(kind: str, raw: bytes) -> bytes:
    return _hex_bytes(raw.decode("ascii")) if kind == "hex" else _literal(raw)


def _actual_text(operands: list[tuple[str, bytes]]) -> str | None:
    for index, (kind, value) in enumerate(operands[:-1]):
        if kind == "name" and value == b"/ActualText":
            nxt_kind, nxt = operands[index + 1]
            if nxt_kind in ("hex", "lit"):
                return _text_string(_string(nxt_kind, nxt))
    return None


def _collect_evidence(
    content: bytes, fonts: dict[str, int], cmaps: dict[int, _CMap]
) -> None:
    """Record ActualText of single-glyph spans and bare uses of suspect glyphs."""
    operands: list[tuple[str, bytes]] = []
    font: str | None = None
    saved: list[str | None] = []
    # Each open marked-content sequence: None, or [actual text, glyphs].
    spans: list[list[Any] | None] = []
    for kind, value in _tokens(content):
        if kind != "op":
            operands.append((kind, value))
            continue
        if value == b"Tf":
            names = [raw for k, raw in operands if k == "name"]
            if names:
                font = names[-1][1:].decode("latin-1")
        elif value == b"q":
            saved.append(font)
        elif value == b"Q":
            if saved:
                font = saved.pop()
        elif value == b"BDC":
            actual = _actual_text(operands)
            spans.append(None if actual is None else [actual, []])
        elif value == b"BMC":
            spans.append(None)
        elif value == b"EMC":
            if spans:
                span = spans.pop()
                if span is not None and len(span[1]) == 1:
                    cmap_xref, code = span[1][0]
                    if cmap_xref in cmaps:
                        cmaps[cmap_xref].evidence.setdefault(code, set()).add(span[0])
        elif value in (b"Tj", b"TJ", b"'", b'"'):
            cmap_xref = fonts.get(font, -1) if font is not None else -1
            cmap = cmaps.get(cmap_xref)
            open_spans = [span for span in spans if span is not None]
            if open_spans:
                strings = _shown_strings(operands, value)
                if cmap is None:
                    # Text in a font without suspect entries still counts as
                    # glyphs, so a multi-glyph span never becomes evidence.
                    glyphs = [(-1, -1)] * sum(len(data) for data in strings)
                else:
                    glyphs = [(cmap_xref, code) for code in _codes(strings, cmap.width)]
                for span in open_spans:
                    span[1].extend(glyphs)
            elif cmap is not None:
                codes = _codes(_shown_strings(operands, value), cmap.width)
                cmap.bare.update(cmap.suspect.intersection(codes))
        operands = []


def _shown_strings(operands: list[tuple[str, bytes]], op: bytes) -> list[bytes]:
    strings = [_string(kind, raw) for kind, raw in operands if kind in ("hex", "lit")]
    return strings if op == b"TJ" else strings[-1:]


def _codes(strings: list[bytes], width: int) -> Iterable[int]:
    """Split shown strings into character codes of *width* bytes."""
    if width == 1:
        return [byte for data in strings for byte in data]
    if width == 2:
        data = b"".join(data[: len(data) & ~1] for data in strings)
        codes = array("H", data)
        if sys.byteorder == "little":
            codes.byteswap()
        return codes
    return [
        int.from_bytes(data[i:i + width], "big")
        for data in strings
        for i in range(0, len(data) - width + 1, width)
    ]
