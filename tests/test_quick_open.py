"""Tests for the quick-open fuzzy matcher."""

from app.quick_open import fuzzy_score


def test_empty_query_matches_everything():
    assert fuzzy_score("", "anything.md") == 0.0


def test_subsequence_matches():
    assert fuzzy_score("rdme", "readme.md") is not None


def test_non_subsequence_returns_none():
    assert fuzzy_score("xyz", "readme.md") is None


def test_consecutive_beats_scattered():
    consecutive = fuzzy_score("read", "readme.md")
    scattered = fuzzy_score("rame", "readme.md")
    assert consecutive is not None and scattered is not None
    assert consecutive > scattered


def test_prefix_ranks_above_midword():
    a = fuzzy_score("not", "notes.md")
    b = fuzzy_score("not", "cannot.md")
    assert a > b


def test_ranking_sort_picks_best():
    cands = ["archive.md", "readme.md", "art-history.md"]
    ranked = sorted(
        (c for c in cands if fuzzy_score("rme", c) is not None),
        key=lambda c: fuzzy_score("rme", c),
        reverse=True,
    )
    assert ranked[0] == "readme.md"
