"""Pure wiki-link candidate and filtering tests."""

from app.wikilink_completion import (
    active_query,
    completion_candidates,
    filter_completions,
)


def test_completion_candidates_are_md_only_root_relative_and_unique(tmp_path):
    vault = tmp_path / "vault"
    nested = vault / "projects"
    files = [
        vault / "Home.md",
        nested / "Roadmap.md",
        nested / "Roadmap.md",
        vault / "ignored.markdown",
        tmp_path / "outside.md",
    ]

    assert completion_candidates([vault, nested], files) == [
        "Home",
        "projects/Roadmap",
    ]


def test_filter_completions_is_case_insensitive_contains_and_relevance_ranked():
    candidates = ["archive/My Note", "noteworthy", "My Note", "misc/denote"]

    assert filter_completions(candidates, "MY NOTE") == [
        "My Note",
        "archive/My Note",
    ]
    assert filter_completions(candidates, "note") == [
        "noteworthy",
        "My Note",
        "archive/My Note",
        "misc/denote",
    ]


def test_filter_completions_never_returns_more_than_50():
    candidates = [f"folder/note-{index:03d}" for index in range(80)]

    assert len(filter_completions(candidates, "note", limit=999)) == 50


def test_active_query_only_matches_an_unfinished_wikilink_on_current_line():
    assert active_query("before [[Road") == "Road"
    assert active_query("[[") == ""
    assert active_query("[[Done]]") is None
    assert active_query("[[alias|") is None
    assert active_query("[[old\nnew") is None
