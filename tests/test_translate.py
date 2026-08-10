"""Tests for selection translation.

Provider transports are stubbed: these lock the chunking, parsing, and error
handling in place without depending on a network or a free-tier quota.
"""

import json

import pytest

from app import translate as tr


# ── chunking ────────────────────────────────────────────────────────────

def test_split_chunks_is_lossless_and_respects_limit():
    text = "The quick brown fox jumps over the lazy dog. " * 40
    for limit in (10, 57, 480, 1800):
        parts = tr.split_chunks(text, limit)
        assert "".join(parts) == text
        assert all(len(p) <= limit for p in parts)
        assert all(p for p in parts)


def test_split_chunks_prefers_sentence_boundaries():
    text = "First sentence. Second sentence. Third sentence."
    parts = tr.split_chunks(text, 20)
    assert parts[0] == "First sentence. "


def test_split_chunks_hard_splits_text_without_breaks():
    text = "x" * 25
    assert tr.split_chunks(text, 10) == ["x" * 10, "x" * 10, "x" * 5]


def test_split_chunks_short_text_is_one_chunk():
    assert tr.split_chunks("hello", 100) == ["hello"]


def test_split_chunks_rejects_bad_limit():
    with pytest.raises(ValueError):
        tr.split_chunks("hello", 0)


# ── settings normalization ──────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "nope", 42])
def test_normalize_provider_falls_back(value):
    assert tr.normalize_provider(value) == tr.DEFAULT_PROVIDER


def test_normalize_provider_keeps_known_keys():
    for info in tr.PROVIDERS:
        assert tr.normalize_provider(info.key) == info.key


@pytest.mark.parametrize("value", [None, "", "klingon"])
def test_normalize_target_falls_back(value):
    assert tr.normalize_target(value) == tr.DEFAULT_TARGET


def test_target_label_round_trip():
    assert tr.target_label("zh-TW") == "繁體中文"
    assert tr.target_label("unknown") == "unknown"


def test_provider_info_falls_back_to_first():
    assert tr.provider_info("nope").key == tr.PROVIDERS[0].key


# ── source-language guess ───────────────────────────────────────────────

def test_guess_source_english():
    assert tr._guess_source("Hello world, this is English.") == "en"


def test_guess_source_chinese():
    assert tr._guess_source("這是一段中文句子。") == "zh-TW"


def test_guess_source_defaults_without_letters():
    assert tr._guess_source("123 !!! ---") == "en"


# ── guard rails ─────────────────────────────────────────────────────────

def test_translate_rejects_blank():
    with pytest.raises(tr.TranslationError):
        tr.translate("   ", provider="google", target="zh-TW")


def test_translate_rejects_oversized_selection():
    with pytest.raises(tr.TranslationError) as exc:
        tr.translate("x" * (tr.MAX_CHARS + 1), provider="google", target="zh-TW")
    assert str(tr.MAX_CHARS) in str(exc.value)


def test_deepl_without_key_is_refused():
    with pytest.raises(tr.TranslationError) as exc:
        tr.translate("hello", provider="deepl", target="zh-TW", api_key="  ")
    assert "金鑰" in str(exc.value)


# ── provider response parsing (transport stubbed) ───────────────────────

def test_mymemory_parses_and_joins_chunks(monkeypatch):
    seen = []

    def fake_get(url):
        seen.append(url)
        return {
            "responseStatus": 200,
            "responseData": {"translatedText": f"譯{len(seen)}"},
        }

    monkeypatch.setattr(tr, "_get_json", fake_get)
    text = "Sentence one. " * 60  # longer than the 480-char chunk limit
    out = tr.translate(text, provider="mymemory", target="zh-TW")

    assert len(seen) > 1, "long text should be chunked"
    assert out == "".join(f"譯{i + 1}" for i in range(len(seen)))
    assert "langpair=en%7Czh-TW" in seen[0]


def test_mymemory_surfaces_service_error(monkeypatch):
    monkeypatch.setattr(
        tr,
        "_get_json",
        lambda url: {"responseStatus": 403, "responseDetails": "QUOTA EXCEEDED"},
    )
    with pytest.raises(tr.TranslationError) as exc:
        tr.translate("hello", provider="mymemory", target="zh-TW")
    assert "QUOTA EXCEEDED" in str(exc.value)


def test_mymemory_rejects_empty_translation(monkeypatch):
    monkeypatch.setattr(
        tr,
        "_get_json",
        lambda url: {"responseStatus": 200, "responseData": {"translatedText": "  "}},
    )
    with pytest.raises(tr.TranslationError):
        tr.translate("hello", provider="mymemory", target="zh-TW")


def test_google_concatenates_segments(monkeypatch):
    payload = [[["你好，", "Hello, ", None, None], ["世界。", "world.", None, None]]]
    monkeypatch.setattr(tr, "_get_json", lambda url: payload)
    out = tr.translate("Hello, world.", provider="google", target="zh-TW")
    assert out == "你好，世界。"


def test_google_rejects_unexpected_shape(monkeypatch):
    monkeypatch.setattr(tr, "_get_json", lambda url: {"not": "a list"})
    with pytest.raises(tr.TranslationError):
        tr.translate("hello", provider="google", target="zh-TW")


def test_deepl_uses_free_host_for_fx_keys(monkeypatch):
    calls = []

    def fake_post(url, data, headers):
        calls.append((url, data, headers))
        return {"translations": [{"text": "哈囉"}]}

    monkeypatch.setattr(tr, "_post_json", fake_post)
    out = tr.translate("hello", provider="deepl", target="zh-TW", api_key="abc:fx")

    assert out == "哈囉"
    url, data, headers = calls[0]
    assert url.startswith("https://api-free.deepl.com")
    assert data["target_lang"] == "ZH-HANT"
    assert headers["Authorization"] == "DeepL-Auth-Key abc:fx"


def test_deepl_uses_pro_host_for_plain_keys(monkeypatch):
    monkeypatch.setattr(
        tr, "_post_json", lambda url, data, headers: {"translations": [{"text": "hi"}]}
    )
    captured = {}

    def record(url, data, headers):
        captured["url"] = url
        return {"translations": [{"text": "hi"}]}

    monkeypatch.setattr(tr, "_post_json", record)
    tr.translate("hello", provider="deepl", target="en", api_key="plainkey")
    assert captured["url"].startswith("https://api.deepl.com")


def test_deepl_retries_with_base_language_code(monkeypatch):
    attempts = []

    def fake_post(url, data, headers):
        attempts.append(data["target_lang"])
        if data["target_lang"] == "ZH-HANT":
            raise tr.TranslationError("target_lang not supported")
        return {"translations": [{"text": "哈囉"}]}

    monkeypatch.setattr(tr, "_post_json", fake_post)
    out = tr.translate("hello", provider="deepl", target="zh-TW", api_key="k:fx")
    assert attempts == ["ZH-HANT", "ZH"]
    assert out == "哈囉"


# ── HTTP error mapping ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "拒絕存取"), (403, "拒絕存取"), (429, "流量上限"), (500, "HTTP 500")],
)
def test_http_status_codes_map_to_friendly_errors(monkeypatch, status, expected):
    """A raw HTTPError must never reach the UI thread."""
    import urllib.error
    import urllib.request

    def raise_http(req, timeout=None):
        raise urllib.error.HTTPError(
            "https://example.com", status, "boom", {}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", raise_http)
    with pytest.raises(tr.TranslationError) as exc:
        tr._get_json("https://example.com")
    assert expected in str(exc.value)


def test_connection_failure_maps_to_friendly_error(monkeypatch):
    import urllib.error
    import urllib.request

    def raise_url(req, timeout=None):
        raise urllib.error.URLError("getaddrinfo failed")

    monkeypatch.setattr(urllib.request, "urlopen", raise_url)
    with pytest.raises(tr.TranslationError) as exc:
        tr._get_json("https://example.com")
    assert "無法連線" in str(exc.value)


def test_timeout_maps_to_friendly_error(monkeypatch):
    import urllib.request

    def raise_timeout(req, timeout=None):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", raise_timeout)
    with pytest.raises(tr.TranslationError) as exc:
        tr._get_json("https://example.com")
    assert "逾時" in str(exc.value)


def test_get_json_rejects_non_json(monkeypatch):
    monkeypatch.setattr(tr, "_read", lambda req: b"<html>nope</html>")
    with pytest.raises(tr.TranslationError):
        tr._get_json("https://example.com")


def test_post_json_encodes_form_body(monkeypatch):
    captured = {}

    def fake_read(req):
        captured["body"] = req.data
        captured["headers"] = dict(req.headers)
        return json.dumps({"ok": True}).encode()

    monkeypatch.setattr(tr, "_read", fake_read)
    tr._post_json("https://example.com", {"text": "a b"}, {"Authorization": "x"})
    assert captured["body"] == b"text=a+b"
    assert captured["headers"]["Authorization"] == "x"


# ── worker lifetime and thread affinity ─────────────────────────────────

class TestStartTranslation:
    """A collected task silently never delivers, so pin the lifetime down."""

    @staticmethod
    def _drain(app, seen, timeout=5.0):
        import time

        deadline = time.time() + timeout
        while not seen and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)

    def test_result_is_delivered_and_task_released(self, qapp, monkeypatch):
        monkeypatch.setattr(tr, "translate", lambda text, **kw: "譯文")
        seen = []
        tr.start_translation(
            7, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append((rid, r)),
            on_failed=lambda rid, m: seen.append(("failed", m)),
        )
        self._drain(qapp, seen)
        assert seen == [(7, "譯文")]
        assert tr.inflight_count() == 0

    def test_callback_runs_on_the_gui_thread(self, qapp, monkeypatch):
        """Widget updates from a worker thread are undefined behaviour."""
        import threading

        monkeypatch.setattr(tr, "translate", lambda text, **kw: "譯文")
        main_thread = threading.current_thread()
        seen = []
        tr.start_translation(
            1, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append(threading.current_thread()),
            on_failed=lambda rid, m: seen.append(threading.current_thread()),
        )
        self._drain(qapp, seen)
        assert seen and seen[0] is main_thread

    def test_provider_failure_reaches_the_failure_callback(self, qapp, monkeypatch):
        def boom(text, **kw):
            raise tr.TranslationError("服務不可用")

        monkeypatch.setattr(tr, "translate", boom)
        seen = []
        tr.start_translation(
            3, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append(("ok", r)),
            on_failed=lambda rid, m: seen.append((rid, m)),
        )
        self._drain(qapp, seen)
        assert seen == [(3, "服務不可用")]
        assert tr.inflight_count() == 0

    def test_unexpected_exception_is_reported_not_swallowed(self, qapp, monkeypatch):
        def boom(text, **kw):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(tr, "translate", boom)
        seen = []
        tr.start_translation(
            4, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append(("ok", r)),
            on_failed=lambda rid, m: seen.append((rid, m)),
        )
        self._drain(qapp, seen)
        assert len(seen) == 1
        assert seen[0][0] == 4
        assert "kaboom" in seen[0][1]
        assert tr.inflight_count() == 0


# ── result cache ────────────────────────────────────────────────────────

class TestCache:
    @pytest.fixture(autouse=True)
    def _empty_cache(self):
        tr.clear_cache()
        yield
        tr.clear_cache()

    def test_miss_returns_none(self):
        assert tr.cached_translation("hello", "google", "zh-TW") is None

    def test_round_trip(self):
        tr.remember_translation("hello", "google", "zh-TW", "哈囉")
        assert tr.cached_translation("hello", "google", "zh-TW") == "哈囉"

    def test_key_includes_provider_and_target(self):
        tr.remember_translation("hello", "google", "zh-TW", "哈囉")
        assert tr.cached_translation("hello", "mymemory", "zh-TW") is None
        assert tr.cached_translation("hello", "google", "ja") is None

    def test_key_ignores_surrounding_whitespace(self):
        tr.remember_translation("hello", "google", "zh-TW", "哈囉")
        assert tr.cached_translation("  hello \n", "google", "zh-TW") == "哈囉"

    def test_evicts_oldest_beyond_the_limit(self):
        for i in range(tr.CACHE_LIMIT + 10):
            tr.remember_translation(f"text{i}", "google", "zh-TW", f"譯{i}")
        assert tr.cache_size() == tr.CACHE_LIMIT
        assert tr.cached_translation("text0", "google", "zh-TW") is None
        newest = tr.CACHE_LIMIT + 9
        assert tr.cached_translation(f"text{newest}", "google", "zh-TW") is not None

    def test_reading_an_entry_keeps_it_alive(self):
        """LRU, not FIFO: a re-read entry must survive later eviction."""
        tr.remember_translation("keep", "google", "zh-TW", "留著")
        for i in range(tr.CACHE_LIMIT - 1):
            tr.remember_translation(f"filler{i}", "google", "zh-TW", f"譯{i}")
        assert tr.cached_translation("keep", "google", "zh-TW") == "留著"  # touch
        for i in range(20):
            tr.remember_translation(f"more{i}", "google", "zh-TW", f"譯{i}")
        assert tr.cached_translation("keep", "google", "zh-TW") == "留著"

    def test_successful_translation_is_cached(self, qapp, monkeypatch):
        import time

        monkeypatch.setattr(tr, "translate", lambda text, **kw: "譯文")
        seen = []
        tr.start_translation(
            1, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append(r),
            on_failed=lambda rid, m: seen.append(("failed", m)),
        )
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert seen == ["譯文"]
        assert tr.cached_translation("hello", "google", "zh-TW") == "譯文"

    def test_failed_translation_is_not_cached(self, qapp, monkeypatch):
        import time

        def boom(text, **kw):
            raise tr.TranslationError("nope")

        monkeypatch.setattr(tr, "translate", boom)
        seen = []
        tr.start_translation(
            1, "hello", provider="google", target="zh-TW",
            on_finished=lambda rid, r: seen.append(r),
            on_failed=lambda rid, m: seen.append(("failed", m)),
        )
        deadline = time.time() + 5
        while not seen and time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert seen and seen[0][0] == "failed"
        assert tr.cached_translation("hello", "google", "zh-TW") is None
