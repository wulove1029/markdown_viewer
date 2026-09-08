"""Sanity checks for the benchmark tool itself (not the app)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark_markdown_first_load import (
    ALL_CATEGORIES,
    SIZES,
    generate_doc,
    summarize,
)


def test_generate_doc_hits_target_size_within_a_few_bytes():
    for category in ALL_CATEGORIES:
        text = generate_doc(category, 50_000)
        encoded = text.encode("utf-8")
        assert 50_000 - 4 <= len(encoded) <= 50_000, (category, len(encoded))


def test_generate_doc_is_deterministic():
    for category in ALL_CATEGORIES:
        first = generate_doc(category, 20_000)
        second = generate_doc(category, 20_000)
        assert first == second


def test_generate_doc_categories_differ():
    texts = {c: generate_doc(c, 20_000) for c in ALL_CATEGORIES}
    assert len(set(texts.values())) == len(ALL_CATEGORIES)


def test_sizes_are_ascending():
    ordered = sorted(SIZES.values())
    assert ordered == list(SIZES.values())


def test_summarize_empty():
    result = summarize([])
    assert result["n"] == 0


def test_summarize_p50_p95_max():
    result = summarize([1.0, 2.0, 3.0, 4.0, 5.0])
    assert result["n"] == 5
    assert result["p50_ms"] == 3.0
    assert result["max_ms"] == 5.0
    assert result["min_ms"] == 1.0
