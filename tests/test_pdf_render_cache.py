"""Deterministic contracts for the byte-bounded PDF raster cache."""

from app.pdf_render_cache import PdfRenderCache, PdfRenderMeta


def _meta(
    *,
    generation=1,
    page=0,
    kind="page",
    dpr100=100,
    page_px=(1000, 1400),
    clip=None,
):
    return PdfRenderMeta(
        generation=generation,
        page=page,
        kind=kind,
        dpr100=dpr100,
        page_px=page_px,
        clip=clip,
    )


def test_byte_budget_evicts_least_recently_used_entry():
    cache = PdfRenderCache(max_bytes=10)

    assert cache.put("a", "A", 4, _meta())
    assert cache.put("b", "B", 4, _meta())
    assert cache.bytes_used == 8

    # Reading A promotes it, so B is the victim when C exceeds the byte budget.
    assert cache.get("a") == "A"
    assert cache.put("c", "C", 5, _meta())

    assert cache.get("b") is None
    assert cache.get("a") == "A"
    assert cache.get("c") == "C"
    assert len(cache) == 2
    assert cache.bytes_used == 9


def test_byte_budget_replacement_remove_clear_and_oversize_rejection():
    cache = PdfRenderCache(max_bytes=12)

    assert cache.put("a", "old", 3, _meta())
    assert cache.put("b", "B", 5, _meta())
    assert cache.put("a", "new", 7, _meta(page_px=(1200, 1680)))
    assert cache.bytes_used == 12
    assert cache.get("a") == "new"

    # One raster larger than the entire budget is never cached.
    assert cache.put("huge", object(), 13, _meta()) is False
    assert cache.get("huge") is None
    assert cache.bytes_used == 12

    cache.remove("b")
    assert cache.bytes_used == 7
    assert len(cache) == 1

    cache.clear()
    assert not cache
    assert len(cache) == 0
    assert cache.bytes_used == 0


def test_cache_is_bounded_by_bytes_instead_of_an_entry_count():
    cache = PdfRenderCache(max_bytes=64)

    for index in range(24):
        assert cache.put(index, index, 2, _meta(page=index))

    assert len(cache) == 24
    assert cache.bytes_used == 48


def test_best_preview_uses_only_matching_whole_page_rasters():
    cache = PdfRenderCache(max_bytes=100)
    target = (1000, 1400)

    cache.put(
        "far",
        "far page",
        1,
        _meta(page_px=(400, 560)),
    )
    cache.put(
        "wrong-dpr",
        "wrong DPR",
        1,
        _meta(dpr100=200, page_px=(900, 1260)),
    )
    cache.put(
        "best",
        "best page",
        1,
        _meta(kind="preview", page_px=(900, 1260)),
    )
    cache.put(
        "tile",
        "tile must not become a page preview",
        1,
        _meta(kind="tile", page_px=target, clip=(0, 0, 768, 768)),
    )
    cache.put(
        "other-generation",
        "stale page",
        1,
        _meta(generation=0, page_px=target),
    )
    cache.put(
        "other-page",
        "different page",
        1,
        _meta(page=1, page_px=target),
    )

    best = cache.best_page_preview(1, 0, 100, target)

    assert best is not None
    key, value, meta = best
    assert key == "best"
    assert value == "best page"
    assert meta.kind == "preview"
    assert cache.best_page_preview(1, 0, 100, (0, 1400)) is None


def test_selecting_a_preview_promotes_it_in_lru_order():
    cache = PdfRenderCache(max_bytes=3)
    cache.put("chosen", "chosen", 1, _meta(page_px=(1000, 1400)))
    cache.put("older-rival", "rival", 1, _meta(page_px=(500, 700)))
    cache.put("other-page", "other", 1, _meta(page=1, page_px=(1000, 1400)))

    assert cache.best_page_preview(1, 0, 100, (1000, 1400))[0] == "chosen"
    cache.put("new", "new", 1, _meta(page=2))

    assert cache.get("older-rival") is None
    assert cache.get("chosen") == "chosen"
    assert cache.get("other-page") == "other"
    assert cache.get("new") == "new"
