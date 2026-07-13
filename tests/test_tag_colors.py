import json

from app.tag_colors import TagColorStore


def test_palette_has_seven_entries():
    assert len(TagColorStore.PALETTE) == 7
    hexes = TagColorStore.palette_hexes()
    assert len(hexes) == 7
    assert hexes == [
        "#E5484D",
        "#F76B15",
        "#F5B70A",
        "#30A46C",
        "#0091FF",
        "#8E4EC6",
        "#8B8D98",
    ]
    # every default color comes from the palette
    for _name, hex_color in TagColorStore.PALETTE:
        assert hex_color.startswith("#")


def test_color_for_deterministic_and_stable(tmp_path):
    store = TagColorStore(path=tmp_path / "colors.json")
    first = store.color_for("重要")
    # deterministic within a process
    assert store.color_for("重要") == first
    # a default color is always drawn from the palette
    assert first in TagColorStore.palette_hexes()
    # stable across a fresh instance (not salted per-process)
    fresh = TagColorStore(path=tmp_path / "colors.json")
    assert fresh.color_for("重要") == first


def test_color_for_never_none(tmp_path):
    store = TagColorStore(path=tmp_path / "colors.json")
    assert store.color_for("anything") is not None
    assert store.explicit_color("anything") is None


def test_set_color_persists_and_reloads(tmp_path):
    path = tmp_path / "colors.json"
    store = TagColorStore(path=path)
    store.set_color("待讀", "#0091FF")

    # persisted on disk with the documented schema
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert raw["colors"] == {"待讀": "#0091FF"}

    # reloads from disk in a new instance
    reloaded = TagColorStore.load(path)
    assert reloaded.color_for("待讀") == "#0091FF"
    assert reloaded.explicit_color("待讀") == "#0091FF"


def test_explicit_override_wins_over_default(tmp_path):
    store = TagColorStore(path=tmp_path / "colors.json")
    default = store.color_for("PD")
    # pick a palette color different from the deterministic default
    override = next(h for h in TagColorStore.palette_hexes() if h != default)
    store.set_color("PD", override)
    assert store.color_for("PD") == override
    assert store.explicit_color("PD") == override


def test_load_only_stores_explicit_mappings(tmp_path):
    path = tmp_path / "colors.json"
    store = TagColorStore(path=path)
    store.color_for("no-explicit")  # resolving a default must not persist anything
    assert not path.exists()
    store.set_color("explicit", "#30A46C")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert list(raw["colors"].keys()) == ["explicit"]


def test_known_tags_only_lists_explicit_and_sorted(tmp_path):
    store = TagColorStore(path=tmp_path / "colors.json")
    assert store.known_tags() == []
    store.set_color("待讀", "#0091FF")
    store.set_color("PD", "#30A46C")
    # resolving a default color must NOT register the tag
    store.color_for("just-a-default")
    assert store.known_tags() == ["PD", "待讀"]
    assert "just-a-default" not in store.known_tags()


def test_remove_deletes_registration_and_persists(tmp_path):
    path = tmp_path / "colors.json"
    store = TagColorStore(path=path)
    store.set_color("待刪", "#F76B15")
    assert "待刪" in store.known_tags()

    store.remove("待刪")
    assert "待刪" not in store.known_tags()

    # removing an unknown tag is a safe no-op
    store.remove("不存在")

    # gone after reloading from disk too
    reloaded = TagColorStore.load(path)
    assert "待刪" not in reloaded.known_tags()
    assert reloaded.explicit_color("待刪") is None
