import pytest
from pygments import highlight
from pygments.lexers import TextLexer, get_lexer_by_name

from app import md_converter as converter


@pytest.mark.parametrize("language", ["python", "javascript", "json", "bash", "", "unknown-lang"])
def test_cached_lexer_output_matches_fresh_lexer_byte_for_byte(language):
    code = 'value = "中文"\nprint(value)\n'
    try:
        lexer = get_lexer_by_name(language) if language else TextLexer()
    except Exception:
        lexer = TextLexer()
    expected = highlight(code, lexer, converter._FORMATTER)
    for _ in range(2):
        assert converter._highlight_code(code, language, "").encode() == expected.encode()


def test_repeated_blocks_reuse_lexer_lookup(monkeypatch):
    calls = []
    original = converter.get_lexer_by_name
    def lookup(language):
        calls.append(language)
        return original(language)
    converter._code_lexer.cache_clear()
    converter._HIGHLIGHT_CACHE.clear()
    monkeypatch.setattr(converter, "get_lexer_by_name", lookup)
    try:
        for code in ("a = 1", "b = 2", "c = 3"):
            converter._highlight_code(code, "python", "")
        assert calls == ["python"]
    finally:
        converter._code_lexer.cache_clear()
        converter._HIGHLIGHT_CACHE.clear()


def test_highlight_cache_obeys_byte_budget(monkeypatch):
    converter._HIGHLIGHT_CACHE.clear()
    monkeypatch.setattr(converter, "_HIGHLIGHT_CACHE_MAX_BYTES", 500)
    try:
        for i in range(10):
            converter._highlight_code(f"variable = {i}\n", "python", "")
        assert sum(size for _, size in converter._HIGHLIGHT_CACHE.values()) <= 500
        count = len(converter._HIGHLIGHT_CACHE)
        converter._highlight_code("a" * 1000, "", "")
        assert len(converter._HIGHLIGHT_CACHE) == count
    finally:
        converter._HIGHLIGHT_CACHE.clear()


def test_oversized_language_is_not_retained_by_either_cache():
    converter._HIGHLIGHT_CACHE.clear()
    converter._code_lexer.cache_clear()
    output = converter._highlight_code("x", "x" * 1000, "")
    assert output == highlight("x", TextLexer(), converter._FORMATTER)
    assert not converter._HIGHLIGHT_CACHE
    assert converter._code_lexer.cache_info().currsize == 0
