"""Office-editor compatibility warning detection."""

from app.markdown_compat import (
    FRONT_MATTER,
    OBSIDIAN_CALLOUTS,
    REFERENCE_LINK_TITLES,
    WIKI_LINKS,
    office_compatibility_risks,
    office_risk_labels,
    office_warning_fingerprint,
)


def test_ordinary_markdown_has_no_office_warning():
    markdown = "# Title\n\n- item\n\n[site](https://example.com)\n"
    assert office_compatibility_risks(markdown) == ()


def test_detects_known_lossy_office_constructs_in_stable_order():
    markdown = """---
title: Example
---

See [[Target|label]].

> [!NOTE] Important

[docs]: https://example.com "Documentation"
"""
    assert office_compatibility_risks(markdown) == (
        FRONT_MATTER,
        WIKI_LINKS,
        OBSIDIAN_CALLOUTS,
        REFERENCE_LINK_TITLES,
    )


def test_code_examples_do_not_trigger_compatibility_warning():
    markdown = """Use `[[not a link]]` as an example.

```markdown
---
title: sample
---
> [!NOTE] sample
[[sample]]
[docs]: /docs "sample"
```
"""
    assert office_compatibility_risks(markdown) == ()


def test_thematic_rule_is_not_mistaken_for_front_matter():
    assert office_compatibility_risks("---\n\nParagraph\n") == ()


def test_ambiguous_top_delimiter_pair_warns_conservatively():
    assert office_compatibility_risks("---\n\nParagraph\n\n---\n") == (
        FRONT_MATTER,
    )


def test_yaml_front_matter_accepts_dot_closer_and_whitespace_only_delimiters():
    markdown = "\ufeff--- \t\ntitle: Example\n...  \t\n\nBody\n"
    assert office_compatibility_risks(markdown) == (FRONT_MATTER,)


def test_yaml_list_front_matter_is_detected_without_mapping_assignments():
    markdown = "---\n- one\n- two\n---\n\nBody\n"
    assert office_compatibility_risks(markdown) == (FRONT_MATTER,)


def test_toml_front_matter_accepts_trailing_whitespace_on_delimiters():
    markdown = "+++ \t\ntitle = 'Example'\n+++  \n\nBody\n"
    assert office_compatibility_risks(markdown) == (FRONT_MATTER,)


def test_reference_definition_title_on_next_line_is_detected():
    markdown = '[docs]: https://example.com\n   "Documentation"\n'
    assert office_compatibility_risks(markdown) == (REFERENCE_LINK_TITLES,)


def test_reference_titles_with_escapes_or_multiline_content_are_detected():
    escaped = '[docs]: /url "a \\" title"\n'
    multiline = '[docs]: /url "a\n  title"\n'

    assert office_compatibility_risks(escaped) == (REFERENCE_LINK_TITLES,)
    assert office_compatibility_risks(multiline) == (REFERENCE_LINK_TITLES,)


def test_nested_blockquote_callout_is_detected():
    assert office_compatibility_risks("> > [!NOTE] Nested\n") == (
        OBSIDIAN_CALLOUTS,
    )


def test_front_matter_delimiters_reject_non_whitespace_suffixes():
    assert office_compatibility_risks("--- yaml\ntitle: Example\n---\n") == ()
    assert office_compatibility_risks("---\ntitle: Example\n--- end\n") == ()
    assert office_compatibility_risks("+++ toml\ntitle = 'Example'\n+++\n") == ()


def test_indented_code_and_fenced_reference_titles_do_not_trigger():
    indented_code = '[docs]: https://example.com\n    "Documentation"\n'
    fenced_title = '[docs]: https://example.com\n```\n"Documentation"\n```\n'
    inline_code = '`[docs]: https://example.com "Documentation"`\n'

    assert office_compatibility_risks(indented_code) == ()
    assert office_compatibility_risks(fenced_title) == ()
    assert office_compatibility_risks(inline_code) == ()


def test_ambiguous_delimiter_pair_with_whitespace_warns_conservatively():
    assert office_compatibility_risks("---   \n\nParagraph\n\n--- \t\n") == (
        FRONT_MATTER,
    )


def test_labels_and_fingerprint_are_stable_without_retaining_source():
    risks = (WIKI_LINKS, OBSIDIAN_CALLOUTS)
    labels = office_risk_labels(risks)
    assert labels == ("wiki-links／嵌入連結", "Obsidian callouts")
    first = office_warning_fingerprint("[[One]]", risks)
    assert first == office_warning_fingerprint("[[One]]", risks)
    assert first != office_warning_fingerprint("[[Two]]", risks)
    assert "[[One]]" not in first
