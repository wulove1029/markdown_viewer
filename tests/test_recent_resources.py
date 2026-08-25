import json
from pathlib import Path

from app.recent_resources import (
    RecentResource,
    decode_recent_resources,
    encode_recent_resources,
    remember_recent_resource,
    resource_from_markdown,
)


def test_resource_link_resolves_relative_to_owning_note(tmp_path):
    note = tmp_path / "notes" / "one.md"
    record = resource_from_markdown("![圖](<assets/a b.png>)", note)

    assert record == RecentResource(
        "![圖](<assets/a b.png>)",
        "image",
        "圖",
        str((note.parent / "assets" / "a b.png").resolve()),
    )


def test_resource_decoder_migrates_legacy_links_without_guessing_base_path():
    records = decode_recent_resources(json.dumps(["[手冊](assets/manual.pdf)"]))

    assert records == [
        RecentResource("[手冊](assets/manual.pdf)", "attachment", "手冊")
    ]


def test_resource_roundtrip_preserves_unicode_and_absolute_path(tmp_path):
    record = RecentResource(
        "![線圈](assets/線圈.png)",
        "image",
        "線圈",
        str(tmp_path / "線圈.png"),
    )

    assert decode_recent_resources(encode_recent_resources([record])) == [record]


def test_remember_deduplicates_same_file_even_when_link_text_changes(tmp_path):
    path = str(tmp_path / "manual.pdf")
    older = RecentResource("[舊名稱](manual.pdf)", "attachment", "舊名稱", path)
    newer = RecentResource("[新名稱](manual.pdf)", "attachment", "新名稱", path)

    assert remember_recent_resource([older], newer) == [newer]


def test_decode_rejects_malformed_json_and_records():
    assert decode_recent_resources("not json") == []
    assert decode_recent_resources(json.dumps([{"schema": 1, "kind": "other"}])) == []


def test_encoded_special_path_and_escaped_label_remain_reusable(tmp_path):
    note = tmp_path / "note.md"
    record = resource_from_markdown(
        r"[\[Guide\]](assets/guide%23v1%20%28final%29%29.pdf)", note
    )

    assert record.label == "[Guide]"
    assert record.absolute_path == str(
        (tmp_path / "assets" / "guide#v1 (final)).pdf").resolve()
    )
